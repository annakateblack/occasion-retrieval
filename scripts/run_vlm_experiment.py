#!/usr/bin/env python
"""Controlled A/B through Work's `run_controlled_arms`.

Canonical signature on prep-vlm-evaluation:

    def run_controlled_arms(
        inventory: list[InstagramSavedPost],
        profile: PersonalFitProfile,
        event: EventContext,
        enriched_provider: AttributeProvider,
        settings: ControlledArmSettings | None = None,
    ) -> ControlledArmRuns

It takes LOADED OBJECTS and ONE event. This script loads them, builds the
enriched provider, and calls it exactly. No signature introspection, no dropping
of unsupported parameters, no fallback experiment implementation: Work owns arm
setup, metrics and IO.

PUBLIC-SAFE. No labels, no permalinks; paths come from the command line and
output is refused outside data/local/.

    uv run python scripts/run_vlm_experiment.py \
        --inventory data/local/derived/ig_shop_2026-09-20_dresses.jsonl \
        --profile data/local/profile.json \
        --event config/events/my_tie_september.json \
        --enriched data/local/enriched/enriched_seed_2026-09-20.jsonl \
        --anchors data/local/eval/anchor_labels.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rental_retrieval.enrichment.orchestrator import read_enriched  # noqa: E402
from rental_retrieval.enrichment.work_adapter import (  # noqa: E402
    make_vision_attribute_provider,
)
from rental_retrieval.enrichment.work_contracts import (  # noqa: E402
    WorkContractsUnavailable,
)
from rental_retrieval.eval.anchors import (  # noqa: E402
    build_measurement_gate,
    compute_anchor_coverage,
    load_anchor_set,
)
from rental_retrieval.schemas.event import EventContext  # noqa: E402
from rental_retrieval.schemas.instagram import InstagramSavedPost  # noqa: E402
from rental_retrieval.schemas.profile import PersonalFitProfile  # noqa: E402


def load_inventory(path: Path) -> list[InstagramSavedPost]:
    return [InstagramSavedPost.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_event(path: Path) -> EventContext:
    raw = {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items()
           if not k.startswith("_")}
    return EventContext.model_validate(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--event", type=Path, required=True,
                        help="One event config. run_controlled_arms takes one event.")
    parser.add_argument("--enriched", type=Path, required=True)
    parser.add_argument("--anchors", type=Path,
                        default=Path("data/local/eval/anchor_labels.json"))
    parser.add_argument("--reporting-k", type=int, default=10)
    parser.add_argument("--retained-depth", type=int, default=20,
                        help="ControlledArmSettings default is 20; must exceed 10.")
    parser.add_argument("--quality-threshold", type=float, default=None,
                        help="Omit to use Work's QUALITY_THRESHOLD default.")
    parser.add_argument("--min-visual-confidence", type=float, default=None)
    parser.add_argument("--out-dir", type=Path, default=Path("data/local/eval"))
    args = parser.parse_args()

    if args.retained_depth <= 10:
        parser.error(
            f"--retained-depth must exceed 10 (got {args.retained_depth}); "
            "cutoff-tie diagnostics need depth beyond the reporting k."
        )
    if "local" not in args.out_dir.resolve().parts:
        parser.error(
            f"Refusing to write output containing real shortcodes to "
            f"{args.out_dir.resolve()}."
        )

    # --- Work contracts ------------------------------------------------------
    try:
        from rental_retrieval.eval.experiment import (  # noqa: PLC0415
            ControlledArmSettings,
            run_controlled_arms,
        )
    except ImportError:
        print("BLOCKED: could not import Work's controlled-arm runner.")
        print("  Expected: src/rental_retrieval/eval/experiment.py")
        print("            -> run_controlled_arms, ControlledArmSettings")
        print("  This script does not fall back to a parallel implementation.")
        print("  Copy this package's enrichment/ modules onto")
        print("  prep-vlm-evaluation and run there.")
        return 3

    # --- Load objects --------------------------------------------------------
    inventory = load_inventory(args.inventory)
    profile = PersonalFitProfile.model_validate_json(
        args.profile.read_text(encoding="utf-8"))
    event = load_event(args.event)
    enriched = read_enriched(args.enriched)

    provider_kwargs = {}
    if args.min_visual_confidence is not None:
        provider_kwargs["min_confidence"] = args.min_visual_confidence

    try:
        enriched_provider = make_vision_attribute_provider(enriched, **provider_kwargs)
    except WorkContractsUnavailable as exc:
        print(f"BLOCKED: {exc}")
        return 3

    settings_kwargs = {
        "reporting_k": args.reporting_k,
        "retained_depth": args.retained_depth,
    }
    if args.quality_threshold is not None:
        settings_kwargs["quality_threshold"] = args.quality_threshold
    settings = ControlledArmSettings(**settings_kwargs)

    # --- Gate and reachability ----------------------------------------------
    anchors = load_anchor_set(args.anchors) if args.anchors.exists() else None
    provider_name = "none"
    if enriched:
        names = {e.provider for e in enriched.values()}
        provider_name = names.pop() if len(names) == 1 else "mixed"
    gate = build_measurement_gate(anchors, provider_name, str(args.anchors))

    print("CONTROLLED ARMS")
    print("=" * 62)
    print(f"  event              : {event.name}")
    print(f"  inventory          : {len(inventory)} listings")
    print(f"  enrichment records : {len(enriched)}")
    print(f"  reporting_k        : {settings.reporting_k}")
    print(f"  retained_depth     : {settings.retained_depth}")
    print(f"  quality_threshold  : {settings.quality_threshold}")
    print(f"  confidence floor   : {enriched_provider.min_confidence}")
    print(f"  fillable keys      : "
          f"{', '.join(sorted(set(enriched_provider.field_map.values())))}")
    print("  never written      : availability")
    print(f"  IS_MEASURED        : {gate.is_measured}")
    for caveat in gate.caveats():
        print(f"    !! {caveat}")

    coverage = None
    if anchors is not None:
        coverage = compute_anchor_coverage(
            anchors, {p.shortcode for p in inventory}, enriched)
        print(f"  anchors reachable  : {len(coverage.in_inventory)}"
              f"/{coverage.total_anchors}")
        print(f"  relevance_scale_max: {anchors.relevance_scale_max}")
        for note in coverage.notes():
            print(f"    !! {note}")
        for problem in anchors.validate_scale():
            print(f"    !! {problem}")
    print()

    # --- The call, exactly as declared --------------------------------------
    runs = run_controlled_arms(
        inventory=inventory,
        profile=profile,
        event=event,
        enriched_provider=enriched_provider,
        settings=settings,
    )

    audit = enriched_provider.report()
    print("VISION PROVIDER AUDIT")
    print("-" * 62)
    for key, value in audit.items():
        print(f"  {key}: {value}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    slug = event.name.lower().replace(" ", "_")
    base = args.out_dir / f"vlm_experiment_{slug}_{date.today().isoformat()}"
    payload = {
        "event": event.name,
        "settings": settings.model_dump(),
        "gate": gate.model_dump(),
        "anchor_coverage": coverage.model_dump() if coverage is not None else None,
        "vision_provider_audit": audit,
        "runs": runs.model_dump() if hasattr(runs, "model_dump") else str(runs),
    }
    Path(f"{base}.json").write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote (gitignored): {base}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
