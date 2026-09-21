"""Normalized visual attributes extracted from a listing image.

This is the contract a vision model fills. It is deliberately separate from
`retrieval/attributes.py`, which reads captions: the two produce the same KINDS
of facts from different evidence, and keeping them apart is what lets the merge
layer reason about which source to trust for which field.

THREE RULES THAT SHAPE THIS SCHEMA
----------------------------------
1. **Every field is optional.** A model that cannot see a hem does not guess at
   one. `None` is a first-class answer and is never replaced by a default.

2. **Confidence is per field, not per record.** A vision model is near-certain
   about colour and genuinely unsure about fabric from a dim mirror selfie.
   One record-level confidence would average those into something meaningless,
   and the merge layer needs the per-field number to decide what to trust.

3. **Controlled vocabularies, coerced at the boundary.** Free-text attribute
   values cannot be scored, compared, or tested. The model is asked for tokens
   from a fixed list; anything outside it is dropped into `unrecognized_values`
   rather than silently accepted or silently discarded.

PUBLIC-SAFE. Definitions and vocabularies only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# --- Controlled vocabularies -------------------------------------------------
# These deliberately mirror retrieval/attributes.py so caption-derived and
# image-derived values are directly comparable without a translation table.

COLOUR_FAMILIES = [
    "black", "white", "red", "pink", "green", "blue", "purple",
    "yellow", "orange", "gold", "silver", "neutral", "multi",
]

BRIGHTNESS_LEVELS = ["very_light", "light", "medium", "dark", "very_dark"]
SATURATION_LEVELS = ["muted", "moderate", "vivid"]

EMBELLISHMENT_TYPES = [
    "none", "sequin", "beaded", "rhinestone", "metallic_fabric",
    "glitter", "lace", "embroidery", "feather", "bow", "ruffle",
]

SILHOUETTES = [
    "a_line", "bodycon", "sheath", "slip", "fit_and_flare",
    "mermaid", "shift", "wrap", "empire", "tiered", "corset",
]

NECKLINES = [
    "plunge_v", "v_neck", "square", "sweetheart", "scoop", "cowl",
    "halter", "high_neck", "straight_strapless", "asymmetric",
    "one_shoulder", "off_shoulder", "boat", "collared",
]

SLEEVE_LENGTHS = ["sleeveless", "cap", "short", "three_quarter", "long"]

STRAP_TYPES = [
    "none_strapless", "spaghetti", "thin", "wide", "halter_tie",
    "one_shoulder", "off_shoulder", "sleeve", "corset_boned",
]

HEM_LENGTHS = ["mini", "above_knee", "knee", "midi", "tea", "maxi", "floor"]

FIT_IMPRESSIONS = ["very_fitted", "fitted", "semi_fitted", "relaxed", "flowing"]

PATTERNS = [
    "solid", "floral", "stripe", "polka_dot", "animal", "geometric",
    "abstract", "colorblock", "plaid", "paisley",
]

TEXTURES = [
    "satin", "silk", "velvet", "lace", "chiffon", "tulle", "knit",
    "crepe", "cotton", "linen", "mesh", "sequin_fabric", "denim", "leather",
]

SHOULDER_EMPHASIS = ["none", "puff", "structured", "padded", "draped", "cutout", "bare"]

VIBE_KEYWORDS = [
    "bright", "dark", "festive", "holiday", "elevated", "playful",
    "classic", "romantic", "preppy", "edgy", "minimal", "glam",
    "summery", "wintery", "sporty",
]

FORMALITY_SCALE = "1 = very casual, 5 = black tie"

ImageQuality = Literal["clear", "acceptable", "poor", "unusable"]


class AttributeValue(BaseModel):
    """One extracted attribute with its confidence and its visual justification.

    `evidence` is the model's own one-clause reason ("visible sequin sheen across
    bodice"). It is the visual analogue of the matched substring that caption
    extraction records, and it exists so a wrong attribute can be diagnosed
    without re-running the model.
    """

    value: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: str | None = None

    @property
    def is_present(self) -> bool:
        return self.value is not None

    def at_least(self, threshold: float) -> bool:
        return self.is_present and self.confidence >= threshold


class VisualAttributes(BaseModel):
    """Everything one image can tell us about one dress."""

    # Colour and tone
    colour_family: AttributeValue = Field(default_factory=AttributeValue)
    secondary_colour: AttributeValue = Field(default_factory=AttributeValue)
    brightness: AttributeValue = Field(default_factory=AttributeValue)
    saturation: AttributeValue = Field(default_factory=AttributeValue)

    # Surface
    embellishment: AttributeValue = Field(default_factory=AttributeValue)
    pattern: AttributeValue = Field(default_factory=AttributeValue)
    texture: AttributeValue = Field(default_factory=AttributeValue)

    # Construction
    silhouette: AttributeValue = Field(default_factory=AttributeValue)
    neckline: AttributeValue = Field(default_factory=AttributeValue)
    sleeve_length: AttributeValue = Field(default_factory=AttributeValue)
    strap_type: AttributeValue = Field(default_factory=AttributeValue)
    hem_length: AttributeValue = Field(default_factory=AttributeValue)
    shoulder_emphasis: AttributeValue = Field(default_factory=AttributeValue)
    fit_impression: AttributeValue = Field(default_factory=AttributeValue)

    # Judgement
    formality: AttributeValue = Field(
        default_factory=AttributeValue, description=f"Integer 1-5 as a string. {FORMALITY_SCALE}"
    )
    vibe_keywords: list[str] = Field(default_factory=list)

    # Meta
    image_quality: ImageQuality = "acceptable"
    notes: str | None = None
    uncertainty_notes: list[str] = Field(
        default_factory=list,
        description="Anything the model explicitly declined to judge, and why",
    )
    unrecognized_values: dict[str, str] = Field(
        default_factory=dict,
        description="field -> raw value the model returned that is outside the vocabulary",
    )

    def observed_fields(self, min_confidence: float = 0.0) -> dict[str, AttributeValue]:
        """Attributes actually present at or above a confidence floor."""
        out: dict[str, AttributeValue] = {}
        for name, value in self:
            if isinstance(value, AttributeValue) and value.at_least(min_confidence):
                out[name] = value
        return out

    @property
    def coverage(self) -> float:
        """Share of attribute slots the model actually filled."""
        slots = [v for _, v in self if isinstance(v, AttributeValue)]
        if not slots:
            return 0.0
        return round(sum(1 for v in slots if v.is_present) / len(slots), 4)


class EnrichedListing(BaseModel):
    """A listing's visual enrichment plus the provenance needed to reproduce it.

    `prompt_version` and `model_version` are part of the cache key, exactly as
    `model_version` is part of the embeddings table key in ADR 002. Changing the
    prompt must invalidate the cache; changing the model must not silently mix
    two models' outputs in one dataset.
    """

    shortcode: str
    permalink: str
    image_ref: str = Field(description="Local path or URL the model actually saw")
    image_sha256: str | None = None

    attributes: VisualAttributes
    provider: str
    model_version: str
    prompt_version: str

    extracted_at: datetime
    latency_ms: int | None = None
    cost_usd: float | None = None
    raw_response: dict | None = Field(
        default=None, description="Retained for re-normalization without re-calling the model"
    )
    errors: list[str] = Field(default_factory=list)

    @field_validator("shortcode")
    @classmethod
    def _shortcode_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("shortcode must not be blank")
        return v

    @property
    def cache_key(self) -> str:
        return f"{self.image_ref}|{self.prompt_version}|{self.model_version}"


class SeedItem(BaseModel):
    """One entry in the hand-labeled gold seed set."""

    shortcode: str
    permalink: str
    label: str = Field(description="Why this item was chosen, in the labeller's words")
    event_hint: str | None = Field(
        default=None, description="Event this exemplifies, e.g. 'my_tie' or 'christmas_cocktail'"
    )
    is_positive: bool = Field(
        default=True, description="False marks a deliberate near-miss, used as a contrast case"
    )
    expected_attributes: dict[str, str] = Field(
        default_factory=dict,
        description="Human-stated attributes, used to score the extractor against a human",
    )
    notes: str | None = None


class SeedSet(BaseModel):
    """The gold seed set: a small, hand-labeled grounding sample."""

    name: str
    description: str
    created_at: str
    is_synthetic: bool = Field(
        description="True until real permalinks replace the placeholders. Gates any metric claim."
    )
    items: list[SeedItem]

    def by_shortcode(self) -> dict[str, SeedItem]:
        return {item.shortcode: item for item in self.items}


class EnrichmentRun(BaseModel):
    """Summary of one enrichment pass. Counts only, safe to print."""

    provider: str
    model_version: str
    prompt_version: str
    requested: int
    succeeded: int
    failed: int
    from_cache: int
    total_cost_usd: float = 0.0
    total_latency_ms: int = 0
    mean_coverage: float = 0.0
    field_coverage: dict[str, float] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
