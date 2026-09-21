#!/usr/bin/env python
"""Regenerate the synthetic enriched fixture from the labeled seed set.

The fixture is committed so tests are stable and readable, but it is GENERATED
so it cannot drift from the seed set it is supposed to mirror. Re-run after
editing config/seed/labeled_seed_set.json.

Output is synthetic by construction: provider is "synthetic_fixture", which the
seed scorer treats as unmeasured. PUBLIC-SAFE.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rental_retrieval.enrichment.contract import PROMPT_VERSION  # noqa: E402
from rental_retrieval.enrichment.seed import load_seed_set  # noqa: E402
from rental_retrieval.schemas.visual import (  # noqa: E402
    AttributeValue,
    EnrichedListing,
    VisualAttributes,
)

SEED = Path("config/seed/labeled_seed_set.json")
OUT = Path("tests/fixtures/synthetic_enriched_seed.jsonl")

# Deliberate imperfections, so the fixture exercises the real code paths rather
# than a best case. Each is a failure mode a real model actually produces.
IMPERFECTIONS: dict[str, dict] = {
    # Model disagrees with a caption that says midi. Tests conflict recording.
    "SEEDXMAS02": {"override": {"hem_length": ("mini", 0.61)}},
    # Low confidence throughout: below the merge threshold, so it must NOT
    # override anything. Tests the confidence floor.
    "SEEDMYTIE03": {"confidence_scale": 0.55},
    # Hard image: almost everything null, quality poor. Tests graceful degradation.
    "SEEDEDGE01": {"drop_all_but": {"colour_family"}, "quality": "poor"},
    # One field the model simply did not return. Tests partial extraction.
    "SEEDFORMAL01": {"drop": {"texture"}},
}

VIBES: dict[str, list[str]] = {
    "SEEDMYTIE01": ["bright", "playful", "summery"],
    "SEEDMYTIE02": ["bright", "elevated", "summery"],
    "SEEDMYTIE03": ["bright", "glam"],
    "SEEDXMAS01": ["festive", "holiday", "glam"],
    "SEEDXMAS02": ["holiday", "elevated", "wintery"],
    "SEEDXMAS03": ["festive", "holiday", "glam"],
    "SEEDFORMAL01": ["elevated", "classic"],
    "SEEDEDGE01": [],
}

BASE_CONFIDENCE = {
    "colour_family": 0.94, "secondary_colour": 0.62, "brightness": 0.88,
    "saturation": 0.84, "embellishment": 0.79, "pattern": 0.86, "texture": 0.66,
    "silhouette": 0.77, "neckline": 0.83, "sleeve_length": 0.91,
    "strap_type": 0.75, "hem_length": 0.87, "shoulder_emphasis": 0.68,
    "fit_impression": 0.72, "formality": 0.70,
}


def main() -> int:
    seed = load_seed_set(SEED)
    rows: list[EnrichedListing] = []

    for item in seed.items:
        tweak = IMPERFECTIONS.get(item.shortcode, {})
        expected = {k: v for k, v in item.expected_attributes.items() if v.strip()}

        fields: dict[str, AttributeValue] = {}
        for field, value in expected.items():
            if field in tweak.get("drop", set()):
                continue
            keep = tweak.get("drop_all_but")
            if keep is not None and field not in keep:
                continue

            confidence = BASE_CONFIDENCE.get(field, 0.75)
            override = tweak.get("override", {}).get(field)
            if override:
                value, confidence = override
            confidence = round(confidence * tweak.get("confidence_scale", 1.0), 2)

            fields[field] = AttributeValue(
                value=value,
                confidence=confidence,
                evidence=f"visible {value.replace('_', ' ')} in frame",
            )

        uncertainty: list[str] = []
        if tweak.get("drop_all_but"):
            uncertainty.append("hem cropped out of frame; most attributes not judgeable")
        if tweak.get("confidence_scale"):
            uncertainty.append("low light reduces confidence across all fields")

        attributes = VisualAttributes(
            **fields,
            vibe_keywords=VIBES.get(item.shortcode, []),
            image_quality=tweak.get("quality", "clear"),
            uncertainty_notes=uncertainty,
        )

        rows.append(EnrichedListing(
            shortcode=item.shortcode,
            permalink=item.permalink,
            image_ref=item.permalink,
            attributes=attributes,
            provider="synthetic_fixture",
            model_version="fixture-v1",
            prompt_version=PROMPT_VERSION,
            extracted_at=datetime(2026, 9, 20, tzinfo=UTC),
            latency_ms=0,
        ))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(row.model_dump_json() + "\n")

    print(f"wrote {len(rows)} synthetic enriched records -> {OUT}")
    for row in rows:
        print(f"  {row.shortcode:14} coverage={row.attributes.coverage:.2f} "
              f"quality={row.attributes.image_quality}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
