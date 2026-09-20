#!/usr/bin/env python
"""Rank the local dress inventory for one event.

PUBLIC-SAFE: no data, only paths from the command line. Reads and writes only
under data/local/, which is gitignored.

Usage (from the repository root):

    uv run python scripts/rank_event.py \
        --inventory data/local/derived/ig_shop_2026-09-20_dresses.jsonl \
        --profile data/local/profile.json \
        --event config/events/my_tie_september.json \
        --top-k 10
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rental_retrieval.retrieval.baseline import rank  # noqa: E402
from rental_retrieval.schemas.event import EventContext  # noqa: E402
from rental_retrieval.schemas.instagram import InstagramSavedPost  # noqa: E402
from rental_retrieval.schemas.profile import PersonalFitProfile  # noqa: E402


def load_inventory(path: Path) -> list[InstagramSavedPost]:
    posts = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                posts.append(InstagramSavedPost.model_validate_json(line))
    return posts


def money(cents: int | None) -> str:
    return f"${cents/100:.0f}" if cents is not None else "price n/s"


def fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "  --"


def render(run, show_captions: bool) -> str:
    out: list[str] = []
    out.append(f"EVENT: {run.event_name}    profile: {run.profile_id}")
    out.append("=" * 74)
    out.append(f"candidates {run.total_candidates}  ->  passed hard filters "
               f"{run.passed_hard_filters}  ->  returned {run.returned}")
    if run.rejected_by_filter:
        out.append("rejected by: " + ", ".join(
            f"{k}={v}" for k, v in sorted(run.rejected_by_filter.items())))
    out.append("weights: " + ", ".join(f"{k}={v}" for k, v in run.weights.items()))
    for note in run.notes:
        out.append(f"note: {note}")
    out.append("")
    out.append(f"{'#':>2}  {'score':>5} {'cov':>4} | {'fit':>4} {'sty':>4} {'evt':>4} "
               f"{'sea':>4} {'prc':>4} {'avl':>4} | {'price':>6} {'length':<8} {'size':<14} avail")
    out.append("-" * 74)
    for r in run.results:
        out.append(
            f"{r.rank:>2}  {r.overall_score:>5.3f} {r.coverage:>4.2f} | "
            f"{fmt(r.fit_score):>4} {fmt(r.style_score):>4} {fmt(r.event_score):>4} "
            f"{fmt(r.seasonality_score):>4} {fmt(r.price_score):>4} "
            f"{fmt(r.availability_score):>4} | "
            f"{money(r.rental_price_cents):>6} {r.dress_length:<8} "
            f"{(r.size_summary or 'n/s')[:14]:<14} {r.availability_status}"
        )
    out.append("")
    for r in run.results:
        out.append(f"[{r.rank}] @{r.owner_username or 'unknown'}  {r.permalink}")
        out.append(f"    {r.explanation}")
        if show_captions and r.raw_caption:
            snippet = " ".join(r.raw_caption.split())[:160]
            out.append(f"    caption: {snippet}")
        out.append("")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--out-dir", type=Path, default=Path("data/local/derived"))
    parser.add_argument("--show-captions", action="store_true",
                        help="Include caption snippets in terminal output")
    args = parser.parse_args()

    if "local" not in args.out_dir.resolve().parts:
        parser.error(
            f"Refusing to write personalized output to {args.out_dir.resolve()}. "
            "Output must live under a path containing 'local' (docs/DATA_HANDLING.md)."
        )

    inventory = load_inventory(args.inventory)
    profile = PersonalFitProfile.model_validate_json(
        args.profile.read_text(encoding="utf-8")
    )
    event_raw = json.loads(args.event.read_text(encoding="utf-8"))
    event_raw.pop("_comment", None)
    event = EventContext.model_validate(event_raw)

    run = rank(inventory, profile, event, top_k=args.top_k)

    slug = event.name.lower().replace(" ", "_")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    base = args.out_dir / f"ranking_{slug}_{date.today().isoformat()}"
    Path(f"{base}.json").write_text(
        run.model_dump_json(indent=2), encoding="utf-8"
    )
    text = render(run, show_captions=True)
    Path(f"{base}.txt").write_text(text, encoding="utf-8")

    print(render(run, show_captions=args.show_captions))
    print(f"wrote (gitignored): {base}.json  {base}.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
