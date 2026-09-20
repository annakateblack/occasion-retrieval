#!/usr/bin/env python
"""Run the Instagram export ingestion and write private derived output.

This script is PUBLIC-SAFE: it contains no data, only paths taken from the
command line. Everything it reads and everything it writes lives under
data/local/, which is gitignored.

Usage (from the repository root):

    uv run python scripts/ingest_ig_export.py \
        --collections data/local/instagram/saved_collections.json \
        --saved-posts data/local/instagram/saved_posts.json \
        --collection Shop

Outputs, all under data/local/derived/:

    ig_<collection>_<date>.jsonl        every normalized record
    ig_<collection>_<date>_dresses.jsonl  the dresses-only inventory slice
    ig_<collection>_<date>_report.json    full coverage report
    ig_<collection>_<date>_report.txt     the same report, readable

Nothing is written outside data/local/ and nothing is uploaded anywhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

# Allow running the script directly without installing the package first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rental_retrieval.sources.ig_export import (  # noqa: E402
    CollectionNotFoundError,
    filter_dress_inventory,
    load_collection,
)
from rental_retrieval.sources.report import (  # noqa: E402
    build_private_report,
    build_public_summary,
    format_report_text,
)

DEFAULT_OUT = Path("data/local/derived")


def write_jsonl(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(record.model_dump_json() + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collections", type=Path, required=True,
                        help="Path to saved_collections.json from the Meta export")
    parser.add_argument("--saved-posts", type=Path, default=None,
                        help="Path to saved_posts.json (optional; supplies saved_at only)")
    parser.add_argument("--collection", default="Shop",
                        help="Collection name to ingest (default: Shop)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                        help="Output directory (must stay under data/local/)")
    parser.add_argument("--show-accounts", action="store_true",
                        help="Include real account handles in terminal output")
    parser.add_argument("--emit-public-template", action="store_true",
                        help="Also write the zeroed public methodology template")
    args = parser.parse_args()

    # Guardrail: refuse to write derived personal data outside the private area.
    resolved = args.out_dir.resolve()
    if "local" not in resolved.parts:
        parser.error(
            f"Refusing to write derived personal data to {resolved}. "
            "Output must live under a path containing 'local' (see docs/DATA_HANDLING.md)."
        )

    if not args.collections.exists():
        parser.error(f"Not found: {args.collections}")

    try:
        posts, diagnostics = load_collection(
            args.collections, args.collection, args.saved_posts
        )
    except CollectionNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    dresses = filter_dress_inventory(posts)
    report = build_private_report(posts, diagnostics)

    stamp = date.today().isoformat()
    slug = args.collection.strip().lower().replace(" ", "_")
    base = args.out_dir / f"ig_{slug}_{stamp}"

    write_jsonl(base.with_suffix(".jsonl"), posts)
    write_jsonl(Path(f"{base}_dresses.jsonl"), dresses)

    Path(f"{base}_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    text = format_report_text(report, include_accounts=True)
    Path(f"{base}_report.txt").write_text(text, encoding="utf-8")

    if args.emit_public_template:
        template = Path("docs/ingestion_report_template.json")
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text(
            json.dumps(build_public_summary(), indent=2), encoding="utf-8"
        )
        print(f"wrote public template -> {template}")

    print(format_report_text(report, include_accounts=args.show_accounts))
    print()
    print("wrote (all gitignored):")
    for suffix in [".jsonl", "_dresses.jsonl", "_report.json", "_report.txt"]:
        print(f"  {base}{suffix}" if not suffix.startswith(".")
              else f"  {base.with_suffix(suffix)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
