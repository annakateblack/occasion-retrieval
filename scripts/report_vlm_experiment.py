#!/usr/bin/env python
"""Summarize a controlled caption-vs-vision experiment.

PUBLIC-SAFE. This script contains no real labels or private identifiers. It reads
private experiment outputs and anchor labels from data/local/ at runtime.

Example:

    uv run python scripts/report_vlm_experiment.py \
        --experiment data/local/eval/vlm_experiment_my_tie_2026-09-20.json \
        --anchors data/local/eval/anchor_labels.json \
        --event-key my_tie
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rental_retrieval.eval.metrics import QueryJudgments, evaluate_arm_pair  # noqa: E402
from rental_retrieval.schemas.anchors import AnchorSet  # noqa: E402
from rental_retrieval.schemas.ranking import RankingRun  # noqa: E402

FIELDS = [
    "colour_family",
    "fabric",
    "length",
    "neckline",
    "silhouette",
    "sleeve",
    "sparkle",
]


def load_inputs(experiment_path: Path, anchors_path: Path) -> tuple[dict, AnchorSet]:
    experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
    anchors = AnchorSet.model_validate_json(anchors_path.read_text(encoding="utf-8"))
    return experiment, anchors


def summarize(experiment: dict, anchors: AnchorSet, event_key: str, k: int = 10) -> dict:
    runs = experiment["runs"]
    caption = RankingRun.model_validate(runs["caption_only"])
    vision = RankingRun.model_validate(runs["enriched"])

    judgments = QueryJudgments(
        query_id=event_key,
        labels=anchors.graded_labels(event_key),
        relevance_scale_max=anchors.relevance_scale_max,
        is_measured=anchors.is_real,
    )
    pair = evaluate_arm_pair(caption, vision, judgments, FIELDS, k=k)

    caption_by_code = {row.shortcode: row for row in caption.results}
    vision_by_code = {row.shortcode: row for row in vision.results}

    movement_rows: list[dict] = []
    rank_moves: list[int] = []
    score_deltas: list[float] = []

    for item in anchors.items:
        c = caption_by_code.get(item.shortcode)
        v = vision_by_code.get(item.shortcode)
        rank_move = None
        score_delta = None

        if c is not None and v is not None:
            rank_move = c.rank - v.rank
            score_delta = v.overall_score - c.overall_score
            rank_moves.append(rank_move)
            score_deltas.append(score_delta)

        movement_rows.append(
            {
                "shortcode": item.shortcode,
                "caption_rank": c.rank if c is not None else None,
                "vision_rank": v.rank if v is not None else None,
                "rank_move": rank_move,
                "caption_score": c.overall_score if c is not None else None,
                "vision_score": v.overall_score if v is not None else None,
                "score_delta": score_delta,
                "caption_style": c.style_score if c is not None else None,
                "vision_style": v.style_score if v is not None else None,
            }
        )

    comparable = len(rank_moves)
    return {
        "event_key": event_key,
        "k": k,
        "caption_ndcg_at_k": pair.caption_only.ndcg_at_k,
        "vision_ndcg_at_k": pair.enriched.ndcg_at_k,
        "ndcg_delta": pair.enriched.ndcg_at_k - pair.caption_only.ndcg_at_k,
        "top_k_overlap": pair.top_k_overlap,
        "caption_style_evaluability": pair.caption_only.style_score_evaluability,
        "vision_style_evaluability": pair.enriched.style_score_evaluability,
        "hard_filter_survivor_delta": pair.hard_filter_survivor_delta,
        "anchors_total": len(anchors.items),
        "anchors_comparable": comparable,
        "anchors_moved_up": sum(move > 0 for move in rank_moves),
        "anchors_moved_down": sum(move < 0 for move in rank_moves),
        "anchors_unchanged": sum(move == 0 for move in rank_moves),
        "mean_rank_move": (sum(rank_moves) / comparable) if comparable else None,
        "mean_score_delta": (sum(score_deltas) / comparable) if comparable else None,
        "movements": movement_rows,
    }


def print_report(summary: dict) -> None:
    print("VLM EXPERIMENT REPORT")
    print("=" * 68)
    print(f"event key              : {summary['event_key']}")
    print(f"caption nDCG@{summary['k']:<2}       : {summary['caption_ndcg_at_k']:.4f}")
    print(f"vision nDCG@{summary['k']:<2}        : {summary['vision_ndcg_at_k']:.4f}")
    print(f"nDCG delta             : {summary['ndcg_delta']:+.4f}")
    print(f"top-{summary['k']} overlap          : {summary['top_k_overlap']:.1%}")
    print(
        "style evaluability     : "
        f"{summary['caption_style_evaluability']:.1%} -> "
        f"{summary['vision_style_evaluability']:.1%}"
    )
    print(f"hard-filter delta      : {summary['hard_filter_survivor_delta']:+d}")
    print()
    print(
        "anchor movement        : "
        f"{summary['anchors_moved_up']} up / "
        f"{summary['anchors_moved_down']} down / "
        f"{summary['anchors_unchanged']} unchanged "
        f"({summary['anchors_comparable']} comparable)"
    )
    if summary["mean_rank_move"] is not None:
        print(f"mean rank move         : {summary['mean_rank_move']:+.2f} places")
        print(f"mean score delta       : {summary['mean_score_delta']:+.4f}")
    print()
    print("PER-ANCHOR")
    print("-" * 68)
    for row in summary["movements"]:
        c_rank = row["caption_rank"] if row["caption_rank"] is not None else "OUT"
        v_rank = row["vision_rank"] if row["vision_rank"] is not None else "OUT"
        move = (
            f"{row['rank_move']:+d}"
            if row["rank_move"] is not None
            else "n/a"
        )
        print(
            f"{row['shortcode']:14} caption={str(c_rank):>4} "
            f"vision={str(v_rank):>4} move={move:>4}"
        )
        if row["score_delta"] is not None:
            print(
                f"{'':14} score {row['caption_score']:.4f} -> "
                f"{row['vision_score']:.4f} ({row['score_delta']:+.4f}); "
                f"style {row['caption_style']} -> {row['vision_style']}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--anchors", type=Path, required=True)
    parser.add_argument("--event-key", required=True)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    experiment, anchors = load_inputs(args.experiment, args.anchors)
    summary = summarize(experiment, anchors, args.event_key, k=args.k)
    print_report(summary)

    if args.json_out is not None:
        if "local" not in args.json_out.resolve().parts:
            parser.error("Refusing to write a private report outside a path containing 'local'.")
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"\nwrote (gitignored): {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
