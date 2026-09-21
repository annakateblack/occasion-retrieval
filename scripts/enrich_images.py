#!/usr/bin/env python
"""Run visual enrichment over the seed set or a dress inventory.

PUBLIC-SAFE: no data, only paths and flags. Writes only under data/local/,
which is gitignored.

    # seed set, stub provider, no API cost
    uv run python scripts/enrich_images.py --seed config/seed/labeled_seed_set.json

    # a real inventory, once a live provider is implemented
    uv run python scripts/enrich_images.py \
        --inventory data/local/derived/ig_shop_2026-09-20_dresses.jsonl \
        --provider live --model-version <vendor-model-id>
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rental_retrieval.enrichment.orchestrator import (  # noqa: E402
    EnrichmentCache,
    enrich_batch,
    write_enriched,
)
from rental_retrieval.enrichment.providers import get_provider  # noqa: E402
from rental_retrieval.enrichment.seed import (  # noqa: E402
    load_seed_set,
    score_against_seed,
)
from rental_retrieval.schemas.instagram import InstagramSavedPost  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--seed", type=Path, help="Labeled seed set JSON")
    source.add_argument("--inventory", type=Path, help="Dress inventory JSONL")

    parser.add_argument("--provider", default="stub", choices=["stub", "live", "failing"])
    parser.add_argument("--model-version", default="stub-v1")
    parser.add_argument("--realism", default="messy", choices=["clean", "messy", "hostile"],
                        help="Stub provider only")
    parser.add_argument("--image-root", type=Path, default=None,
                        help="Directory of local images named <shortcode>.jpg")
    parser.add_argument("--out-dir", type=Path, default=Path("data/local/enriched"))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/local/enrichment_cache"))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    for path, label in [(args.out_dir, "output"), (args.cache_dir, "cache")]:
        if "local" not in path.resolve().parts:
            parser.error(f"Refusing to write {label} to {path.resolve()}; "
                         "it must live under a path containing 'local'.")

    if args.provider == "stub":
        provider = get_provider("stub", realism=args.realism)
    elif args.provider == "failing":
        provider = get_provider("failing")
    else:
        provider = get_provider("live", model_version=args.model_version)

    seed = None
    if args.seed:
        seed = load_seed_set(args.seed)
        from rental_retrieval.enrichment.seed import to_enrichment_items
        items = to_enrichment_items(seed, image_root=args.image_root)
        label = "seed"
    else:
        posts = [InstagramSavedPost.model_validate_json(line)
                 for line in args.inventory.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        items = [{
            "shortcode": p.shortcode,
            "permalink": p.permalink,
            "image_ref": (str(args.image_root / f"{p.shortcode}.jpg")
                          if args.image_root else p.permalink),
        } for p in posts]
        label = "inventory"

    if args.limit:
        items = items[:args.limit]

    cache = EnrichmentCache(args.cache_dir)
    results, run = enrich_batch(items, provider, cache)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / f"enriched_{label}_{date.today().isoformat()}.jsonl"
    write_enriched(out, results)

    print("ENRICHMENT RUN")
    print("=" * 56)
    print(f"provider={run.provider}  model={run.model_version}  prompt={run.prompt_version}")
    print(f"requested={run.requested}  succeeded={run.succeeded}  "
          f"failed={run.failed}  from_cache={run.from_cache}")
    print(f"mean attribute coverage: {run.mean_coverage:.1%}")
    print()
    print("field coverage")
    for field, value in run.field_coverage.items():
        bar = "#" * int(value * 30)
        print(f"  {field:20} {value:6.1%} {bar}")
    if run.errors:
        print(f"\nfirst errors ({len(run.errors)} shown):")
        for err in run.errors[:8]:
            print(f"  - {err}")

    if seed is not None:
        enriched_by_code = {r.shortcode: r for r in results}
        report = score_against_seed(seed, enriched_by_code)
        print()
        print("AGREEMENT WITH HUMAN LABELS")
        print("-" * 56)
        print(f"is_measured : {report.is_measured}")
        print(f"items       : {report.items_scored}")
        print(f"overall     : {report.overall_agreement:.1%}")
        for field, value in report.per_field_agreement.items():
            print(f"  {field:20} {value:6.1%}")
        for caveat in report.caveats:
            print(f"\n  !! {caveat}")

    print(f"\nwrote (gitignored): {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
