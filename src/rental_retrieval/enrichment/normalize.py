"""Raw model JSON -> validated VisualAttributes.

This module is the trust boundary. Everything upstream of it is a model's
free-form opinion; everything downstream can assume a validated object with
controlled vocabularies.

It is written to survive a badly behaved model, because every VLM misbehaves
eventually: missing keys, extra keys, a bare string where an object was asked
for, "V-neck" instead of "v_neck", a confidence of 95 instead of 0.95, prose
wrapped around the JSON. None of those should crash a pipeline, and none should
silently corrupt a dataset either. Each is repaired where repair is
unambiguous and recorded where it is not.

PUBLIC-SAFE. Logic only.
"""

from __future__ import annotations

import json
import re
from typing import Any

from rental_retrieval.enrichment.contract import FIELD_VOCABULARIES
from rental_retrieval.schemas.visual import (
    VIBE_KEYWORDS,
    AttributeValue,
    VisualAttributes,
)

_ATTRIBUTE_FIELDS = list(FIELD_VOCABULARIES.keys())

_VALID_QUALITY = {"clear", "acceptable", "poor", "unusable"}

# Tolerated spellings for vocabulary tokens. Kept deliberately small: it repairs
# formatting differences only, never meaning. "V-neck" -> "v_neck" is formatting;
# mapping "midi" to "knee" would be a judgement and is not done here.
_ALIASES: dict[str, str] = {
    "vneck": "v_neck", "v neck": "v_neck", "deep v": "plunge_v", "deepv": "plunge_v",
    "plunging": "plunge_v", "plunge": "plunge_v",
    "strapless": "straight_strapless", "straight across": "straight_strapless",
    "off the shoulder": "off_shoulder", "offshoulder": "off_shoulder",
    "one shoulder": "one_shoulder", "oneshoulder": "one_shoulder",
    "high neck": "high_neck", "mock neck": "high_neck", "turtleneck": "high_neck",
    "aline": "a_line", "a line": "a_line",
    "fit and flare": "fit_and_flare", "fit & flare": "fit_and_flare",
    "body con": "bodycon", "body-con": "bodycon",
    "three quarter": "three_quarter", "3/4": "three_quarter",
    "spaghetti straps": "spaghetti", "thin straps": "thin",
    "no straps": "none_strapless", "strapless straps": "none_strapless",
    "floor length": "floor", "full length": "floor", "above the knee": "above_knee",
    "tea length": "tea",
    "navy": "blue", "burgundy": "red", "maroon": "red", "emerald": "green",
    "ivory": "white", "cream": "white", "champagne": "gold", "beige": "neutral",
    "tan": "neutral", "brown": "neutral", "nude": "neutral",
    "sequins": "sequin", "sequined": "sequin", "rhinestones": "rhinestone",
    "polka dot": "polka_dot", "color block": "colorblock", "colour block": "colorblock",
    "sequin fabric": "sequin_fabric", "metallic": "metallic_fabric",
}


def _canonical(token: str) -> str:
    """Lowercase, collapse separators, trim. Formatting only."""
    t = token.strip().lower()
    t = re.sub(r"[\s\-]+", "_", t)
    t = re.sub(r"_+", "_", t)
    return t.strip("_")


def coerce_to_vocabulary(field: str, raw: Any) -> tuple[str | None, str | None]:
    """Map a raw value onto the field's vocabulary.

    Returns (accepted_value, unrecognized_raw). Exactly one is non-None, or both
    are None when the model returned nothing at all.
    """
    if raw is None:
        return None, None
    if not isinstance(raw, (str, int, float)):
        return None, str(raw)[:80]

    text = str(raw).strip()
    if not text or text.lower() in {"null", "none", "n/a", "unknown", "unclear"}:
        return None, None

    vocabulary = FIELD_VOCABULARIES.get(field, [])

    if text in vocabulary:                       # exact hit, the common case
        return text, None

    canonical = _canonical(text)
    if canonical in vocabulary:
        return canonical, None

    aliased = _ALIASES.get(canonical) or _ALIASES.get(text.strip().lower())
    if aliased and aliased in vocabulary:
        return aliased, None

    return None, text[:80]


def _coerce_confidence(raw: Any) -> float:
    """Clamp to [0, 1], accepting a percentage as a courtesy.

    Models return 95 for "95%" often enough that rejecting it would throw away
    good data, and a confidence above 1 has no other sensible reading.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if value > 1.0:
        value = value / 100.0 if value <= 100.0 else 1.0
    return max(0.0, min(1.0, value))


def _as_attribute_object(raw: Any) -> dict[str, Any]:
    """Accept either {'value':..} or a bare scalar.

    Models drop the wrapper under load. A bare value is unambiguous, so it is
    accepted with zero confidence rather than discarded: the merge layer's
    threshold will then correctly refuse to trust it.
    """
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return {}
    return {"value": raw, "confidence": 0.0, "evidence": "bare value, wrapper missing"}


def extract_json_block(text: str) -> dict[str, Any] | None:
    """Pull a JSON object out of a response that may be wrapped in prose or fences."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def normalize(raw: dict[str, Any] | str | None) -> tuple[VisualAttributes, list[str]]:
    """Validate and coerce a raw model response.

    Returns (attributes, errors). Never raises: a malformed response yields an
    empty-but-valid VisualAttributes plus an error list, so one bad extraction
    cannot halt a batch.
    """
    errors: list[str] = []

    if isinstance(raw, str):
        parsed = extract_json_block(raw)
        if parsed is None:
            return VisualAttributes(image_quality="unusable"), ["response was not valid JSON"]
        raw = parsed

    if not isinstance(raw, dict):
        return VisualAttributes(image_quality="unusable"), ["response was not a JSON object"]

    fields: dict[str, Any] = {}
    unrecognized: dict[str, str] = {}

    for field in _ATTRIBUTE_FIELDS:
        obj = _as_attribute_object(raw.get(field))
        value, bad = coerce_to_vocabulary(field, obj.get("value"))
        if bad is not None:
            unrecognized[field] = bad
            errors.append(f"{field}: value {bad!r} is outside the vocabulary; dropped")

        evidence = obj.get("evidence")
        fields[field] = AttributeValue(
            value=value,
            confidence=_coerce_confidence(obj.get("confidence")) if value else 0.0,
            evidence=str(evidence)[:200] if isinstance(evidence, str) and evidence else None,
        )

    # vibe_keywords: a list, filtered to the vocabulary, order-stable, deduped.
    vibes: list[str] = []
    raw_vibes = raw.get("vibe_keywords") or []
    if isinstance(raw_vibes, str):
        raw_vibes = [v for v in re.split(r"[,;]", raw_vibes) if v.strip()]
    if isinstance(raw_vibes, list):
        for item in raw_vibes:
            token = _canonical(str(item))
            if token in VIBE_KEYWORDS and token not in vibes:
                vibes.append(token)
            elif token and token not in VIBE_KEYWORDS:
                errors.append(f"vibe_keywords: {token!r} is outside the vocabulary; dropped")

    quality = str(raw.get("image_quality") or "acceptable").strip().lower()
    if quality not in _VALID_QUALITY:
        errors.append(f"image_quality: {quality!r} invalid; defaulted to 'acceptable'")
        quality = "acceptable"

    notes = raw.get("notes")
    raw_uncertainty = raw.get("uncertainty_notes") or []
    if isinstance(raw_uncertainty, str):
        raw_uncertainty = [raw_uncertainty]
    uncertainty = [str(n)[:300] for n in raw_uncertainty if str(n).strip()][:10]

    attributes = VisualAttributes(
        **fields,
        vibe_keywords=vibes,
        image_quality=quality,  # type: ignore[arg-type]
        notes=str(notes)[:500] if isinstance(notes, str) and notes.strip() else None,
        uncertainty_notes=uncertainty,
        unrecognized_values=unrecognized,
    )

    # An unusable image that still reports attributes is self-contradictory.
    # Record it rather than resolving it: the merge layer's confidence threshold
    # will discount them anyway, and hiding the contradiction hides a bad model.
    if quality == "unusable" and attributes.coverage > 0:
        errors.append("model reported image_quality='unusable' but still returned attributes")

    return attributes, errors
