#!/usr/bin/env python
"""Report on the private anchor label set: coverage, gate status, anti-rules.

PUBLIC-SAFE SCRIPT. It contains no labels; it reads a path you supply. By
default that path is data/local/eval/anchor_labels.json, which is gitignored.

    # gate + coverage against the real inventory
    uv run python scripts/eval_anchors.py \
        --anchors data/local/eval/anchor_labels.json \
        --inventory data/local/derived/ig_shop_2026-09-20_dresses.jsonl

    # see the shape without touching real labels
    uv run python scripts/eval_anchors.py --anchors config/seed/anchor_labels.template.json

Add --show-permalinks only when you are not sharing your screen: it prints real
permalinks.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rental_retrieval.enrichment.orchestrator import read_enriched  # noqa: E402
from rental_retrieval.eval.anchors import (  # noqa: E402
    DEFAULT_ANCHOR_PATH,
    build_measurement_gate,
    compute_anchor_coverage,
    load_anchor_set,
)
from rental_retrieval.schemas.anchors import GRADE_TO_SCORE  # noqa: E402
from rental_retrieval.schemas.instagram import InstagramSavedPost  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--anchors", type=Path, default=DEFAULT_ANCHOR_PATH)
    parser.add_argument("--inventory", type=Path, default=None)
    parser.add_argument("--enriched", type=Path, default=None)
    parser.add_argument("--provider", default="stub",
                        help="Provider that produced the enrichment; gates is_measured")
    parser.add_argument("--show-permalinks", action="store_true")
    args = parser.parse_args()

    anchors = load_anchor_set(args.anchors)

    inventory_codes: set[str] = set()
    if args.inventory and args.inventory.exists():
        inventory_codes = {
            InstagramSavedPost.model_validate_json(line).shortcode
            for line in args.inventory.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    enriched = read_enriched(args.enriched) if args.enriched else {}
    gate = build_measurement_gate(anchors, args.provider, str(args.anchors))
    coverage = compute_anchor_coverage(anchors, inventory_codes, enriched)

    print("ANCHOR LABEL SET")
    print("=" * 64)
    print(f"name        : {anchors.name}")
    print(f"is_real     : {anchors.is_real}")
    print(f"labeller    : {anchors.labeller or 'unspecified'}")
    print(f"items       : {len(anchors.items)}")
    print(f"event keys  : {', '.join(anchors.event_keys())}")
    print()

    print("MEASUREMENT GATE")
    print("-" * 64)
    print(f"labels_are_real  : {gate.labels_are_real}")
    print(f"provider_is_live : {gate.provider_is_live}  ({gate.provider_name})")
    print(f"IS_MEASURED      : {gate.is_measured}")
    for caveat in gate.caveats():
        print(f"  !! {caveat}")
    print()

    print("GRADES PER EVENT")
    print("-" * 64)
    for key in anchors.event_keys():
        labels = anchors.graded_labels(key)
        strong = sum(1 for v in labels.values() if v == 2)
        plausible = sum(1 for v in labels.values() if v == 1)
        print(f"  {key:26} strong={strong}  plausible={plausible}  "
              f"positives={strong + plausible}")
    print()

    if inventory_codes:
        print("PIPELINE REACHABILITY")
        print("-" * 64)
        print(f"  inventory size        : {len(inventory_codes)}")
        print(f"  anchors in inventory  : {len(coverage.in_inventory)}/{coverage.total_anchors} "
              f"({coverage.inventory_rate:.0%})")
        if args.enriched:
            print(f"  anchors enriched      : {len(coverage.enriched)}/{coverage.total_anchors} "
                  f"({coverage.enrichment_rate:.0%})")
        for note in coverage.notes():
            print(f"  !! {note}")
        print()

    anti = anchors.all_negative_associations()
    if anti:
        print("ANTI-RULES ASSERTED BY THE LABELLER")
        print("-" * 64)
        for code, rules in anti.items():
            print(f"  {code}: {', '.join(rules)}")
        print()

    print("PER-ITEM")
    print("-" * 64)
    for item in anchors.items:
        reach = "in-inventory" if item.shortcode in inventory_codes else (
            "NOT IN INVENTORY" if inventory_codes else "unchecked")
        print(f"  {item.shortcode:14} conf={item.label_confidence:6} {reach}")
        for entry in item.relevance:
            print(f"      {entry.event_key:26} {entry.grade:12} "
                  f"(score {GRADE_TO_SCORE[entry.grade]})")
        if item.fit_notes:
            print(f"      fit note: {item.fit_notes}")
        if args.show_permalinks:
            print(f"      {item.permalink}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
