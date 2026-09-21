"""The extraction contract: what we ask a vision model, and what we accept back.

The prompt lives here rather than inside the provider because the prompt IS the
schema. Changing a word changes the data, so it is versioned, kept in one place,
and part of the cache key. A provider swap must not change the contract.

PROMPT DESIGN, AND WHY EACH RULE IS THERE
-----------------------------------------
**Judge the garment, not the photograph.** The catalog is dorm-room mirror
selfies; the taste corpus is studio product photography. If the model describes
lighting, background or styling, that difference becomes signal and the system
learns to prefer well-lit photos. Every phase of this project fights that same
domain gap.

**Answer from the fixed vocabulary or answer null.** Free text cannot be scored
or tested. An out-of-vocabulary answer is captured in `unrecognized_values`
rather than accepted, so vocabulary drift is visible instead of silent.

**Null is a correct answer.** The single largest risk in a VLM extraction step
is fluent invention. The prompt says so explicitly, asks for per-field
confidence, and provides `uncertainty_notes` as somewhere for doubt to go.

**Per-field confidence.** A model sure about colour and unsure about fabric must
be able to say so; the merge layer thresholds on exactly that number.

PUBLIC-SAFE. Prompt text and vocabularies only.
"""

from __future__ import annotations

import json
from typing import Any

from rental_retrieval.schemas.visual import (
    BRIGHTNESS_LEVELS,
    COLOUR_FAMILIES,
    EMBELLISHMENT_TYPES,
    FIT_IMPRESSIONS,
    HEM_LENGTHS,
    NECKLINES,
    PATTERNS,
    SATURATION_LEVELS,
    SHOULDER_EMPHASIS,
    SILHOUETTES,
    SLEEVE_LENGTHS,
    STRAP_TYPES,
    TEXTURES,
    VIBE_KEYWORDS,
)

PROMPT_VERSION = "dress-visual-v1"
"""Bump on ANY prompt change. It is part of the cache key, so bumping it
invalidates every cached extraction, which is the intended behaviour: old
outputs were produced by a different question."""

# Field name -> allowed tokens. Single source of truth for both the prompt text
# and the normalizer's coercion step, so they cannot drift apart.
FIELD_VOCABULARIES: dict[str, list[str]] = {
    "colour_family": COLOUR_FAMILIES,
    "secondary_colour": COLOUR_FAMILIES,
    "brightness": BRIGHTNESS_LEVELS,
    "saturation": SATURATION_LEVELS,
    "embellishment": EMBELLISHMENT_TYPES,
    "pattern": PATTERNS,
    "texture": TEXTURES,
    "silhouette": SILHOUETTES,
    "neckline": NECKLINES,
    "sleeve_length": SLEEVE_LENGTHS,
    "strap_type": STRAP_TYPES,
    "hem_length": HEM_LENGTHS,
    "shoulder_emphasis": SHOULDER_EMPHASIS,
    "fit_impression": FIT_IMPRESSIONS,
    "formality": ["1", "2", "3", "4", "5"],
}

SYSTEM_PROMPT = """\
You are a careful apparel attribute extractor. You look at one photograph of a \
dress and report only what is visible.

Three rules govern every answer:

1. Describe the GARMENT only. Ignore the photograph itself: lighting, \
background, room, mirror, camera quality, pose, hair, and anything worn with the \
dress. A dress photographed in a dim bedroom and the same dress photographed in a \
studio must receive identical answers.

2. Use ONLY the allowed values listed for each field. If the garment does not \
match any allowed value, return null for that field and explain in \
uncertainty_notes.

3. null is a correct and expected answer. If a hem is cropped out of frame, the \
hem_length is null, not a guess. Never infer one attribute from another. Do not \
describe what is likely; describe what is visible.

For every field give a confidence between 0.0 and 1.0 reflecting how clearly the \
image supports your answer, and a short evidence clause naming what you saw.

Return a single JSON object and nothing else.\
"""


def _vocab_block() -> str:
    lines = []
    for field, values in FIELD_VOCABULARIES.items():
        lines.append(f"  {field}: {' | '.join(values)}")
    lines.append(f"  vibe_keywords (0-4 of): {' | '.join(VIBE_KEYWORDS)}")
    return "\n".join(lines)


RESPONSE_TEMPLATE: dict[str, Any] = {
    "colour_family": {"value": None, "confidence": 0.0, "evidence": None},
    "secondary_colour": {"value": None, "confidence": 0.0, "evidence": None},
    "brightness": {"value": None, "confidence": 0.0, "evidence": None},
    "saturation": {"value": None, "confidence": 0.0, "evidence": None},
    "embellishment": {"value": None, "confidence": 0.0, "evidence": None},
    "pattern": {"value": None, "confidence": 0.0, "evidence": None},
    "texture": {"value": None, "confidence": 0.0, "evidence": None},
    "silhouette": {"value": None, "confidence": 0.0, "evidence": None},
    "neckline": {"value": None, "confidence": 0.0, "evidence": None},
    "sleeve_length": {"value": None, "confidence": 0.0, "evidence": None},
    "strap_type": {"value": None, "confidence": 0.0, "evidence": None},
    "hem_length": {"value": None, "confidence": 0.0, "evidence": None},
    "shoulder_emphasis": {"value": None, "confidence": 0.0, "evidence": None},
    "fit_impression": {"value": None, "confidence": 0.0, "evidence": None},
    "formality": {"value": None, "confidence": 0.0, "evidence": None},
    "vibe_keywords": [],
    "image_quality": "acceptable",
    "notes": None,
    "uncertainty_notes": [],
}


def build_user_prompt() -> str:
    """The instruction sent alongside the image."""
    return f"""\
Extract dress attributes from the attached photograph.

Allowed values:
{_vocab_block()}

image_quality must be one of: clear | acceptable | poor | unusable.
Use "poor" or "unusable" when the image genuinely prevents judgement, and expect \
most fields to be null in that case.

formality is an integer 1-5 as a string, where 1 is very casual and 5 is black tie.

Return exactly this JSON shape, with no commentary before or after:

{json.dumps(RESPONSE_TEMPLATE, indent=2)}
"""


def build_messages(image_ref: str) -> list[dict[str, Any]]:
    """Provider-neutral message list.

    Deliberately not shaped for any one vendor's SDK. Each provider adapts this
    into its own wire format, so switching vendors touches one adapter rather
    than the contract.
    """
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image", "image_ref": image_ref},
                {"type": "text", "text": build_user_prompt()},
            ],
        },
    ]


def contract_fingerprint() -> str:
    """Stable identifier for the whole contract.

    Covers the prompt version AND the vocabularies, so adding a neckline token
    invalidates the cache even if the prompt prose is untouched.
    """
    import hashlib

    payload = json.dumps(
        {"prompt_version": PROMPT_VERSION, "vocab": FIELD_VOCABULARIES,
         "vibe": VIBE_KEYWORDS, "system": SYSTEM_PROMPT},
        sort_keys=True,
    )
    return f"{PROMPT_VERSION}+{hashlib.sha256(payload.encode()).hexdigest()[:12]}"
