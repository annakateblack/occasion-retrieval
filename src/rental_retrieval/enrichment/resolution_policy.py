"""The controlled-experiment fill policy, bound to Work's AttributeResolution.

Pure and side-effect free on the input: it copies `values`, `observations` and
`conflicts` from the base resolution and returns new containers, so the caption
resolution the caller passed in is never mutated.

THE RULES, AND WHERE EACH LIVES
-------------------------------
**Start from the caption.** The caller supplies `caption_resolution(post)`.
This module copies it; it never builds one.

**Fill UNKNOWN only.** A value is written exactly when the current value is
`None` or `"unknown"` AND vision confidence clears the floor.

**Caption wins a disagreement.** Both sides known and different: the caption
value stays, the vision observation is appended, and a conflict string is
recorded. The controlled arm is not where override behaviour gets tested;
overriding would change values the baseline arm also reads and the two arms
would stop being comparable.

**Availability is never written by vision.** It is a required key and a
renter-stated fact. Size, price and dates are outside the visual-resolution
authority entirely and are not part of `AttributeResolution.values`.

**Observations accumulate regardless.** Every vision reading that passes the
confidence floor is appended to `observations[key]`, whether or not it changed
`values[key]`. That is what makes a fill, a conflict and a refusal all auditable
after the fact from the resolution alone.

PUBLIC-SAFE. Logic only, no vendor dependency, no data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rental_retrieval.enrichment.work_contracts import (
    REQUIRED_KEYS,
    is_unknown,
)
from rental_retrieval.schemas.visual import EnrichedListing

DEFAULT_MIN_CONFIDENCE = 0.55
"""Vision below this fills nothing and records nothing. Hand-set prior,
unchanged from the pre-integration value so the two remain comparable."""

NEVER_WRITTEN_BY_VISION: frozenset[str] = frozenset({"availability"})
"""Required keys vision may never write.

Size, price and dates do not appear here because they are not keys of
`AttributeResolution.values` at all: they live on the post and in the ranker's
hard filters, outside this resolution's authority. `availability` IS a key, so
it needs an explicit guard.
"""

VISION_FIELD_TO_KEY: dict[str, str] = {
    # internal visual schema field -> Work resolution key
    "hem_length": "length",
    "colour_family": "colour_family",
    "sleeve_length": "sleeve",
    "neckline": "neckline",
    "silhouette": "silhouette",
    "embellishment": "sparkle",
    "texture": "fabric",
}
"""The seven fillable keys.

`brightness`, `saturation`, `strap_type`, `shoulder_emphasis`, `fit_impression`,
`pattern` and `formality` are deliberately absent: Work's current scorer does
not consume them, so writing them would add attributes one arm ignores. They
remain in the internal visual schema and the enrichment cache for later
experiments.
"""

_HEM_TO_LENGTH: dict[str, str] = {
    "mini": "mini", "above_knee": "mini",
    "knee": "midi", "midi": "midi", "tea": "midi",
    "maxi": "maxi", "floor": "maxi",
}

_SPARKLE_EMBELLISHMENTS = frozenset({
    "sequin", "beaded", "rhinestone", "metallic_fabric", "glitter",
})


@dataclass
class FillOutcome:
    """What the policy did to one listing. Audit trail for the run report."""

    filled: dict[str, str] = field(default_factory=dict)
    conflicted: dict[str, tuple[str | None, str]] = field(default_factory=dict)
    agreed: list[str] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=dict)
    skipped_low_confidence: list[str] = field(default_factory=list)
    observations_added: int = 0
    image_quality: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def fill_count(self) -> int:
        return len(self.filled)

    def summary(self) -> str:
        if not self.filled:
            return "vision filled nothing"
        return "vision filled: " + ", ".join(sorted(self.filled))


def project_to_key(vision_field: str, value: str | None) -> str | None:
    """Map an internal vision value onto Work's vocabulary for that key."""
    if value is None:
        return None
    if vision_field == "hem_length":
        return _HEM_TO_LENGTH.get(value)
    if vision_field == "embellishment":
        return None if value == "none" else (
            "yes" if value in _SPARKLE_EMBELLISHMENTS else None)
    return value


def format_conflict(key: str, caption_value: str | None, vision_value: str,
                    confidence: float) -> str:
    """`AttributeResolution.conflicts` is `list[str]`, so a conflict is a line.

    Written to be greppable and to name the resolution, so a reader of the
    resolution alone can tell what happened and why.
    """
    return (
        f"{key}: caption={caption_value!r} vision={vision_value!r} "
        f"conf={confidence:.2f} -> kept caption (controlled arm: vision fills "
        f"unknown only)"
    )


def apply_vision(
    base: Any,
    enriched: EnrichedListing | None,
    *,
    resolution_cls: Any,
    observation_cls: Any,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    field_map: dict[str, str] | None = None,
) -> tuple[Any, FillOutcome]:
    """Return a NEW AttributeResolution with vision applied, plus the audit.

    `base` is the resolution from `caption_resolution(post)` and is not mutated.
    `resolution_cls` and `observation_cls` are Work's classes, injected so this
    module never imports them and stays unit-testable against an exact double.

    With `enriched=None` the returned resolution is a faithful copy of the base,
    which is what makes an un-enriched listing in the vision arm identical to
    the baseline arm.
    """
    field_map = field_map or VISION_FIELD_TO_KEY
    outcome = FillOutcome()

    values: dict[str, str | None] = dict(base.values)
    observations: dict[str, list[Any]] = {
        key: list(items) for key, items in base.observations.items()
    }
    conflicts: list[str] = list(base.conflicts)

    if enriched is None:
        return resolution_cls(values=values, observations=observations,
                              conflicts=conflicts), outcome

    outcome.image_quality = enriched.attributes.image_quality

    if enriched.attributes.image_quality == "unusable":
        outcome.notes.append("image unusable; no attributes applied")
        return resolution_cls(values=values, observations=observations,
                              conflicts=conflicts), outcome

    for vision_field, key in field_map.items():
        attribute = getattr(enriched.attributes, vision_field, None)
        if attribute is None or not attribute.is_present:
            continue

        vision_value = project_to_key(vision_field, attribute.value)
        if vision_value is None:
            continue

        confidence = attribute.confidence

        # Guard first: availability is a required key vision may never write,
        # whatever its confidence and whatever the caption says.
        if key in NEVER_WRITTEN_BY_VISION:
            outcome.refused[key] = (
                "renter-stated fact; vision has no authority over this key"
            )
            continue

        if confidence < min_confidence:
            outcome.skipped_low_confidence.append(key)
            continue

        observation = observation_cls(
            value=vision_value,
            source="vision",
            confidence=confidence,
            evidence=attribute.evidence,
            provider=enriched.provider,
            model_version=enriched.model_version,
        )
        observations.setdefault(key, []).append(observation)
        outcome.observations_added += 1

        current = values.get(key)

        if is_unknown(current):
            values[key] = vision_value
            outcome.filled[key] = vision_value
        elif current == vision_value:
            outcome.agreed.append(key)
        else:
            conflicts.append(format_conflict(key, current, vision_value, confidence))
            outcome.conflicted[key] = (current, vision_value)

    resolution = resolution_cls(
        values=values, observations=observations, conflicts=conflicts)

    missing = [k for k in REQUIRED_KEYS if k not in resolution.values]
    if missing:
        outcome.notes.append(
            f"resolution is missing required key(s): {', '.join(missing)}. "
            "caption_resolution should have supplied them."
        )

    return resolution, outcome


def outcome_report(outcomes: dict[str, FillOutcome]) -> dict[str, Any]:
    """Aggregate across an inventory. Counts only; safe to print."""
    if not outcomes:
        return {"listings": 0}

    fills: dict[str, int] = {}
    conflicts: dict[str, int] = {}
    refusals: dict[str, int] = {}

    for outcome in outcomes.values():
        for key in outcome.filled:
            fills[key] = fills.get(key, 0) + 1
        for key in outcome.conflicted:
            conflicts[key] = conflicts.get(key, 0) + 1
        for key in outcome.refused:
            refusals[key] = refusals.get(key, 0) + 1

    total = len(outcomes)
    return {
        "listings": total,
        "listings_with_any_fill": sum(1 for o in outcomes.values() if o.filled),
        "mean_fills_per_listing": round(
            sum(o.fill_count for o in outcomes.values()) / total, 2),
        "fills_by_key": dict(sorted(fills.items(), key=lambda kv: -kv[1])),
        "conflicts_by_key": dict(sorted(conflicts.items(), key=lambda kv: -kv[1])),
        "total_conflicts": sum(conflicts.values()),
        "refusals_by_key": dict(sorted(refusals.items(), key=lambda kv: -kv[1])),
        "observations_added": sum(o.observations_added for o in outcomes.values()),
        "unusable_images": sum(
            1 for o in outcomes.values() if o.image_quality == "unusable"),
        "fillable_keys": sorted(set(VISION_FIELD_TO_KEY.values())),
        "never_written_by_vision": sorted(NEVER_WRITTEN_BY_VISION),
    }
