"""Reusable reporting for controlled caption-vs-vision experiments."""

from __future__ import annotations

import json
from pathlib import Path

from rental_retrieval.eval.metrics import QueryJudgments, evaluate_arm_pair
from rental_retrieval.schemas.anchors import AnchorSet
from rental_retrieval.schemas.ranking import RankingRun

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
        move = f"{row['rank_move']:+d}" if row["rank_move"] is not None else "n/a"
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
