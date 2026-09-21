"""The labeled seed set: loading it, and scoring an extractor against it.

The seed set is eight dresses a person looked at and described. Its job is to
answer one question the enrichment pipeline cannot answer about itself: **does
the extractor see what a human sees?**

Without it, a VLM pass is unfalsifiable. Coverage tells you the model filled in
fields; only agreement with a human tells you the fields are right.

TWO GUARDS AGAINST FOOLING OURSELVES
------------------------------------
`SeedSet.is_synthetic` gates every metric. While the permalinks are
placeholders, `score_against_seed` still runs (so the harness is testable) but
stamps every result `is_measured=False`. A number from synthetic seed data is a
smoke test, not evidence, and the code says so rather than relying on whoever
reads the output to remember.

Two of the eight are deliberate **contrast cases**: right occasion, wrong
construction. A ranking system that puts them at the top is matching the event
and ignoring the wearer, which is the specific failure this whole phase exists
to fix. They are the sharpest signal in the set.

PUBLIC-SAFE. The committed seed file contains no real permalinks.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from rental_retrieval.schemas.visual import EnrichedListing, SeedItem, SeedSet


class FieldAgreement(BaseModel):
    """Per-field agreement between the extractor and the human labeller."""

    field: str
    expected: str
    extracted: str | None
    confidence: float
    agrees: bool


class SeedItemScore(BaseModel):
    """How the extractor did on one seed item."""

    shortcode: str
    is_positive: bool
    comparable_fields: int
    agreements: int
    agreement_rate: float
    fields: list[FieldAgreement] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    hallucinated_fields: list[str] = Field(
        default_factory=list,
        description="Confidently filled where the labeller recorded no value",
    )


class SeedScoreReport(BaseModel):
    """Aggregate extractor-versus-human report."""

    seed_set: str
    is_measured: bool = Field(
        description="False while the seed set is synthetic. Gates any claim made from this."
    )
    provider: str
    model_version: str
    prompt_version: str

    items_scored: int
    mean_agreement: float
    overall_agreement: float
    per_field_agreement: dict[str, float] = Field(default_factory=dict)
    items: list[SeedItemScore] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


def load_seed_set(path: Path) -> SeedSet:
    """Load and validate the seed set, ignoring underscore-prefixed doc keys."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    for item in raw.get("items", []):
        for key in [k for k in item if k.startswith("_")]:
            item.pop(key)
    return SeedSet.model_validate(raw)


def to_enrichment_items(seed: SeedSet, image_root: Path | None = None) -> list[dict[str, str]]:
    """Turn seed entries into the input shape `enrich_batch` expects.

    `image_ref` points at a local file when `image_root` is given, and otherwise
    at the permalink. Nothing here downloads anything: fetching images is a
    separate, explicitly-authorized step and this module never performs network
    access.
    """
    items: list[dict[str, str]] = []
    for entry in seed.items:
        if image_root is not None:
            image_ref = str(Path(image_root) / f"{entry.shortcode}.jpg")
        else:
            image_ref = entry.permalink
        items.append({
            "shortcode": entry.shortcode,
            "permalink": entry.permalink,
            "image_ref": image_ref,
        })
    return items


def _expected_pairs(item: SeedItem) -> dict[str, str]:
    """Expected attributes with blanks removed.

    A blank expectation means the labeller deliberately declined to judge, which
    is not the same as expecting an empty answer. Those fields are excluded from
    agreement and used instead to detect fabrication.
    """
    return {k: v for k, v in item.expected_attributes.items() if v.strip()}


def score_item(item: SeedItem, enriched: EnrichedListing,
               min_confidence: float = 0.0) -> SeedItemScore:
    """Compare one extraction against one human label."""
    expected = _expected_pairs(item)
    declined = {k for k, v in item.expected_attributes.items() if not v.strip()}

    fields: list[FieldAgreement] = []
    missing: list[str] = []

    for field, want in expected.items():
        attribute = getattr(enriched.attributes, field, None)
        got = attribute.value if attribute is not None else None
        confidence = attribute.confidence if attribute is not None else 0.0

        if got is None:
            missing.append(field)
            continue
        if confidence < min_confidence:
            missing.append(field)
            continue

        fields.append(FieldAgreement(
            field=field, expected=want, extracted=got,
            confidence=confidence, agrees=(got == want),
        ))

    # Fabrication check: a field the labeller declined to judge, filled with
    # high confidence. On the deliberately-hard edge-case image this is the
    # metric that matters most.
    hallucinated: list[str] = []
    for field in declined:
        attribute = getattr(enriched.attributes, field, None)
        if attribute is not None and attribute.at_least(0.7):
            hallucinated.append(field)

    agreements = sum(1 for f in fields if f.agrees)
    comparable = len(fields)

    return SeedItemScore(
        shortcode=item.shortcode,
        is_positive=item.is_positive,
        comparable_fields=comparable,
        agreements=agreements,
        agreement_rate=round(agreements / comparable, 4) if comparable else 0.0,
        fields=fields,
        missing_fields=sorted(missing),
        hallucinated_fields=sorted(hallucinated),
    )


def score_against_seed(
    seed: SeedSet,
    enriched_by_code: dict[str, EnrichedListing],
    min_confidence: float = 0.0,
) -> SeedScoreReport:
    """Score a whole extraction run against the human labels."""
    scores: list[SeedItemScore] = []
    per_field_hits: dict[str, list[bool]] = {}

    provider = model_version = prompt_version = "none"

    for item in seed.items:
        enriched = enriched_by_code.get(item.shortcode)
        if enriched is None:
            continue
        provider, model_version, prompt_version = (
            enriched.provider, enriched.model_version, enriched.prompt_version
        )
        score = score_item(item, enriched, min_confidence)
        scores.append(score)
        for field in score.fields:
            per_field_hits.setdefault(field.field, []).append(field.agrees)

    total_comparable = sum(s.comparable_fields for s in scores)
    total_agreements = sum(s.agreements for s in scores)

    caveats: list[str] = []
    if seed.is_synthetic:
        caveats.append(
            "SEED SET IS SYNTHETIC: permalinks are placeholders and the extractions "
            "come from a stub provider. These numbers verify that the harness runs. "
            "They are not evidence about extractor quality."
        )
    fabricating = [s.shortcode for s in scores if s.hallucinated_fields]
    if fabricating:
        caveats.append(
            f"{len(fabricating)} item(s) confidently filled a field the labeller "
            f"declined to judge: {', '.join(fabricating)}"
        )

    return SeedScoreReport(
        seed_set=seed.name,
        is_measured=not seed.is_synthetic and provider != "stub",
        provider=provider,
        model_version=model_version,
        prompt_version=prompt_version,
        items_scored=len(scores),
        mean_agreement=round(
            sum(s.agreement_rate for s in scores) / len(scores), 4
        ) if scores else 0.0,
        overall_agreement=round(
            total_agreements / total_comparable, 4
        ) if total_comparable else 0.0,
        per_field_agreement={
            field: round(sum(hits) / len(hits), 4)
            for field, hits in sorted(per_field_hits.items())
        },
        items=scores,
        caveats=caveats,
    )
