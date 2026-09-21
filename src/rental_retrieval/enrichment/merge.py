"""Merge caption-derived and image-derived attributes into one feature set.

THE DESIGN QUESTION
-------------------
Two sources now describe the same dress and they will disagree. The caption says
"midi"; the image looks like a mini. Which wins?

The answer is not "the more confident one". It depends on what kind of fact it
is, and getting this wrong is how a system ends up overriding a renter's own
statement with a model's guess.

**The caption wins on stated facts.** Size, price and availability are the
renter's assertions about their own garment. A vision model cannot observe them
and must never overwrite them. A model that decides a dress "looks like a 6"
would be inventing an authority it does not have.

**The image wins on visual facts, above a confidence threshold.** Neckline,
silhouette, hem, colour, embellishment. The renter usually did not mention them
at all; when they did, they were describing what they could see, and so is the
model, but the model looked on purpose.

**Every disagreement is recorded, never silently resolved.** A `MergeConflict`
carries both values so the merge policy is auditable and so systematic
disagreement (which would indicate a bad prompt or a bad caption parser) shows up
as data rather than as mysterious ranking behaviour.

**Provenance travels with every value.** Downstream code and the explanation
text can always say where a fact came from, which is what keeps the ranker
explainable now that it has two sources.

PUBLIC-SAFE. Logic only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from rental_retrieval.schemas.visual import EnrichedListing

Provenance = Literal["caption", "image", "caption_and_image", "none"]

DEFAULT_MIN_CONFIDENCE = 0.55
"""Below this, an image-derived attribute is not trusted enough to override or
to fill. Chosen as a starting prior, not a fitted value: it should be tuned
against the seed set once real extractions exist."""

# Image-derived field -> the caption-derived field it corresponds to.
# Only these are merged; anything not listed is image-only or caption-only.
FIELD_MAP: dict[str, str] = {
    "colour_family": "colour_family",
    "neckline": "neckline",
    "silhouette": "silhouette",
    "sleeve_length": "sleeve",
    "hem_length": "length",
    "texture": "fabric",
    "embellishment": "sparkle",
}

CAPTION_AUTHORITATIVE = frozenset({"size", "price", "availability"})
"""Renter-stated facts. A vision model may not overwrite these, ever."""

# hem_length has a finer vocabulary than caption length; map it down so the two
# are comparable and so the existing length-preference scorer keeps working.
_HEM_TO_LENGTH: dict[str, str] = {
    "mini": "mini", "above_knee": "mini",
    "knee": "midi", "midi": "midi", "tea": "midi",
    "maxi": "maxi", "floor": "maxi",
}

_EMBELLISHMENT_TO_SPARKLE = {
    "sequin", "beaded", "rhinestone", "metallic_fabric", "glitter",
}


class MergeConflict(BaseModel):
    """A disagreement between the two sources, preserved for audit."""

    field: str
    caption_value: str | None
    image_value: str | None
    image_confidence: float
    resolution: str = Field(description="Which value was kept, and why")


class MergedAttributes(BaseModel):
    """The unified attribute set the ranker consumes."""

    values: dict[str, str | None] = Field(default_factory=dict)
    provenance: dict[str, Provenance] = Field(default_factory=dict)
    confidence: dict[str, float] = Field(default_factory=dict)
    conflicts: list[MergeConflict] = Field(default_factory=list)

    visual_coverage: float = 0.0
    has_visual: bool = False
    image_quality: str | None = None

    def get(self, field: str) -> str | None:
        return self.values.get(field)

    def source_of(self, field: str) -> Provenance:
        return self.provenance.get(field, "none")

    @property
    def observed_count(self) -> int:
        return sum(1 for v in self.values.values() if v is not None)

    def summary_line(self) -> str:
        """One line naming which fields came from the image. For explanations."""
        from_image = sorted(
            f for f, p in self.provenance.items()
            if p in ("image", "caption_and_image") and self.values.get(f)
        )
        return f"image supplied: {', '.join(from_image)}" if from_image else "no visual attributes"


def _normalize_hem(hem: str | None) -> str | None:
    return _HEM_TO_LENGTH.get(hem) if hem else None


def _normalize_embellishment(value: str | None) -> str | None:
    """Collapse the embellishment vocabulary onto the caption's sparkle flag."""
    if value is None:
        return None
    if value == "none":
        return None
    return "yes" if value in _EMBELLISHMENT_TO_SPARKLE else None


def merge_attributes(
    caption_attributes: dict[str, str | None],
    enriched: EnrichedListing | None,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> MergedAttributes:
    """Combine caption-derived and image-derived attributes.

    `caption_attributes` is the dict produced by retrieval.attributes.extract_all.
    `enriched` may be None, in which case this is a pass-through and the result
    is identical to the caption-only behaviour. That property is what lets the
    enrichment layer be added without changing any existing ranking result for
    un-enriched listings.
    """
    merged = MergedAttributes()

    # Start from the caption. Evidence keys are dropped: they are provenance for
    # the caption parser, and this object carries its own provenance.
    for key, value in caption_attributes.items():
        if key.endswith("_evidence"):
            continue
        normalized = None if value == "unknown" else value
        merged.values[key] = normalized
        merged.provenance[key] = "caption" if normalized is not None else "none"
        merged.confidence[key] = 1.0 if normalized is not None else 0.0

    if enriched is None:
        return merged

    merged.has_visual = True
    merged.visual_coverage = enriched.attributes.coverage
    merged.image_quality = enriched.attributes.image_quality

    # An unusable image contributes nothing, whatever it claimed to see.
    if enriched.attributes.image_quality == "unusable":
        return merged

    for image_field, caption_field in FIELD_MAP.items():
        attribute = getattr(enriched.attributes, image_field, None)
        if attribute is None or not attribute.is_present:
            continue

        image_value: str | None = attribute.value
        if image_field == "hem_length":
            image_value = _normalize_hem(image_value)
        elif image_field == "embellishment":
            image_value = _normalize_embellishment(image_value)
        if image_value is None:
            continue

        caption_value = merged.values.get(caption_field)
        trusted = attribute.confidence >= min_confidence

        if caption_value is None:
            # Gap fill. This is the main event: the caption said nothing.
            if trusted:
                merged.values[caption_field] = image_value
                merged.provenance[caption_field] = "image"
                merged.confidence[caption_field] = attribute.confidence
            continue

        if caption_value == image_value:
            # Agreement raises confidence without changing the value.
            merged.provenance[caption_field] = "caption_and_image"
            merged.confidence[caption_field] = min(
                1.0, merged.confidence.get(caption_field, 1.0) + 0.0
            )
            continue

        # Disagreement. Record it either way; resolve only above threshold.
        if trusted:
            resolution = (
                f"kept image value (confidence {attribute.confidence:.2f} "
                f">= {min_confidence:.2f}); visual attributes are observed, not asserted"
            )
            merged.values[caption_field] = image_value
            merged.provenance[caption_field] = "image"
            merged.confidence[caption_field] = attribute.confidence
        else:
            resolution = (
                f"kept caption value (image confidence {attribute.confidence:.2f} "
                f"< {min_confidence:.2f})"
            )

        merged.conflicts.append(
            MergeConflict(
                field=caption_field,
                caption_value=caption_value,
                image_value=image_value,
                image_confidence=attribute.confidence,
                resolution=resolution,
            )
        )

    # Image-only fields: no caption equivalent exists, so nothing to reconcile.
    for image_field in ("brightness", "saturation", "fit_impression",
                        "strap_type", "shoulder_emphasis", "pattern", "formality"):
        attribute = getattr(enriched.attributes, image_field, None)
        if attribute is not None and attribute.at_least(min_confidence):
            merged.values[image_field] = attribute.value
            merged.provenance[image_field] = "image"
            merged.confidence[image_field] = attribute.confidence

    if enriched.attributes.vibe_keywords:
        merged.values["visual_vibes"] = ",".join(enriched.attributes.vibe_keywords)
        merged.provenance["visual_vibes"] = "image"
        merged.confidence["visual_vibes"] = 1.0

    return merged


def merge_many(
    caption_attributes_by_code: dict[str, dict[str, str | None]],
    enriched_by_code: dict[str, EnrichedListing],
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> dict[str, MergedAttributes]:
    """Merge a whole inventory. Listings without enrichment pass through unchanged."""
    return {
        code: merge_attributes(attributes, enriched_by_code.get(code), min_confidence)
        for code, attributes in caption_attributes_by_code.items()
    }


def merge_report(merged: dict[str, MergedAttributes]) -> dict[str, object]:
    """Counts describing what the merge actually did. Safe to print."""
    total = len(merged)
    if total == 0:
        return {"total": 0}

    with_visual = sum(1 for m in merged.values() if m.has_visual)
    gap_filled: dict[str, int] = {}
    conflicts: dict[str, int] = {}

    for m in merged.values():
        for field, source in m.provenance.items():
            if source == "image" and m.values.get(field):
                gap_filled[field] = gap_filled.get(field, 0) + 1
        for c in m.conflicts:
            conflicts[c.field] = conflicts.get(c.field, 0) + 1

    return {
        "total": total,
        "with_visual": with_visual,
        "mean_attributes_before": round(
            sum(sum(1 for f, p in m.provenance.items() if p == "caption")
                for m in merged.values()) / total, 2
        ),
        "mean_attributes_after": round(
            sum(m.observed_count for m in merged.values()) / total, 2
        ),
        "fields_supplied_by_image": dict(sorted(gap_filled.items(),
                                                key=lambda kv: -kv[1])),
        "conflicts_by_field": dict(sorted(conflicts.items(), key=lambda kv: -kv[1])),
        "total_conflicts": sum(conflicts.values()),
    }
