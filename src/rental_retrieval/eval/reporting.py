"""Reusable reporting for controlled caption-vs-vision experiments."""

from __future__ import annotations

import json
from pathlib import Path

from rental_retrieval.eval.anchors import build_measurement_gate
from rental_retrieval.eval.metrics import QueryJudgments, evaluate_arm_pair
from rental_retrieval.schemas.anchors import AnchorSet, MeasurementGate
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


def _measurement_gate(
    experiment: dict, anchors: AnchorSet, label_source: str | None
) -> MeasurementGate:
    """Rebuild the gate from inputs instead of trusting serialized booleans."""
    if "gate" not in experiment:
        raise ValueError("Experiment has no MeasurementGate; refusing to report metrics")
    stored = MeasurementGate.model_validate(experiment["gate"])
    rebuilt = build_measurement_gate(
        anchors,
        provider_name=stored.provider_name,
        label_source=label_source or stored.label_source,
    )
    if (
        stored.labels_are_real != rebuilt.labels_are_real
        or stored.provider_is_live != rebuilt.provider_is_live
    ):
        raise ValueError("Serialized MeasurementGate disagrees with the reporting inputs")
    return rebuilt


def _reachable_codes(experiment: dict) -> set[str]:
    coverage = experiment.get("anchor_coverage")
    if not isinstance(coverage, dict) or "in_inventory" not in coverage:
        raise ValueError(
            "Experiment has no anchor_coverage.in_inventory; rerun the controlled "
            "experiment so reachable-qrel nDCG cannot be guessed from ranked output"
        )
    return set(coverage["in_inventory"])


def _ndcg_block(pair) -> dict[str, float]:
    caption = pair.caption_only.ndcg_at_k
    vision = pair.enriched.ndcg_at_k
    return {"caption_only": caption, "enriched": vision, "delta": vision - caption}


def summarize(
    experiment: dict,
    anchors: AnchorSet,
    event_key: str,
    k: int = 10,
    *,
    label_source: str | None = None,
    require_measured: bool = True,
    redact: bool = False,
) -> dict:
    """Separate candidate reachability from ranking quality and enforce the gate."""
    gate = _measurement_gate(experiment, anchors, label_source)
    if require_measured and not gate.is_measured:
        raise ValueError(
            "MeasurementGate rejected publication: " + "; ".join(gate.caveats())
        )

    runs = experiment["runs"]
    caption = RankingRun.model_validate(runs["caption_only"])
    vision = RankingRun.model_validate(runs["enriched"])

    all_labels = anchors.graded_labels(event_key)
    reachable_codes = _reachable_codes(experiment)
    reachable_labels = {
        code: grade for code, grade in all_labels.items() if code in reachable_codes
    }
    full_judgments = QueryJudgments(
        query_id=event_key,
        labels=all_labels,
        relevance_scale_max=anchors.relevance_scale_max,
        is_measured=gate.is_measured,
    )
    reachable_judgments = QueryJudgments(
        query_id=event_key,
        labels=reachable_labels,
        relevance_scale_max=anchors.relevance_scale_max,
        is_measured=gate.is_measured,
    )
    full_pair = evaluate_arm_pair(caption, vision, full_judgments, FIELDS, k=k)
    reachable_pair = evaluate_arm_pair(
        caption, vision, reachable_judgments, FIELDS, k=k
    )

    caption_by_code = {row.shortcode: row for row in caption.results}
    vision_by_code = {row.shortcode: row for row in vision.results}
    redacted_ids = {
        item.shortcode: f"anchor_{index:03d}"
        for index, item in enumerate(anchors.items, start=1)
    }

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
                "item_id": (
                    redacted_ids[item.shortcode] if redact else item.shortcode
                ),
                "reachable": item.shortcode in reachable_codes,
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
    full_ndcg = _ndcg_block(full_pair)
    reachable_ndcg = _ndcg_block(reachable_pair)
    caveats = gate.caveats()
    if redact and gate.label_source:
        caveats = [text.replace(gate.label_source, "[redacted]") for text in caveats]
    return {
        "event_key": event_key,
        "k": k,
        "redacted": redact,
        "measurement_gate": {
            "labels_are_real": gate.labels_are_real,
            "provider_is_live": gate.provider_is_live,
            "provider_name": gate.provider_name,
            "is_measured": gate.is_measured,
            "caveats": caveats,
        },
        "relevance_scale_max": anchors.relevance_scale_max,
        "full_qrel_ndcg_at_k": full_ndcg,
        "reachable_qrel_ndcg_at_k": reachable_ndcg,
        "reachability_ndcg_gap": {
            "caption_only": (
                reachable_ndcg["caption_only"] - full_ndcg["caption_only"]
            ),
            "enriched": reachable_ndcg["enriched"] - full_ndcg["enriched"],
        },
        "qrels_total": len(all_labels),
        "qrels_reachable": len(reachable_labels),
        "positive_qrels_total": sum(grade > 0 for grade in all_labels.values()),
        "positive_qrels_reachable": sum(
            grade > 0 for grade in reachable_labels.values()
        ),
        "top_k_overlap": full_pair.top_k_overlap,
        "caption_style_evaluability": full_pair.caption_only.style_score_evaluability,
        "vision_style_evaluability": full_pair.enriched.style_score_evaluability,
        "hard_filter_survivor_delta": full_pair.hard_filter_survivor_delta,
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
    gate = summary["measurement_gate"]
    print("VLM EXPERIMENT REPORT")
    print("=" * 76)
    print(
        "measurement status     : "
        f"{'MEASURED' if gate['is_measured'] else 'NOT MEASURED'}"
    )
    print(f"provider               : {gate['provider_name']}")
    for caveat in gate["caveats"]:
        print(f"  !! {caveat}")
    print(f"event key              : {summary['event_key']}")
    print(f"relevance scale        : 0..{summary['relevance_scale_max']}")
    print(
        "qrel reachability      : "
        f"{summary['qrels_reachable']}/{summary['qrels_total']} labels; "
        f"{summary['positive_qrels_reachable']}/"
        f"{summary['positive_qrels_total']} positive labels"
    )
    for label, key in (
        ("full qrels", "full_qrel_ndcg_at_k"),
        ("reachable qrels", "reachable_qrel_ndcg_at_k"),
    ):
        block = summary[key]
        print(
            f"nDCG@{summary['k']} ({label:<15}): "
            f"{block['caption_only']:.4f} -> {block['enriched']:.4f} "
            f"({block['delta']:+.4f})"
        )
    print(
        "reachability gap       : "
        f"caption {summary['reachability_ndcg_gap']['caption_only']:+.4f}; "
        f"enriched {summary['reachability_ndcg_gap']['enriched']:+.4f} "
        "(candidate-generation/recall loss, not ranking loss)"
    )
    print(f"top-{summary['k']} overlap          : {summary['top_k_overlap']:.1%}")
    print(
        "style evaluability     : "
        f"{summary['caption_style_evaluability']:.1%} -> "
        f"{summary['vision_style_evaluability']:.1%}"
    )
    print(
        "hard-filter survivors  : "
        f"delta {summary['hard_filter_survivor_delta']:+d}; vision-filled length "
        "can change this outcome"
    )
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
    print("-" * 76)
    for row in summary["movements"]:
        c_rank = row["caption_rank"] if row["caption_rank"] is not None else "OUT"
        v_rank = row["vision_rank"] if row["vision_rank"] is not None else "OUT"
        move = f"{row['rank_move']:+d}" if row["rank_move"] is not None else "n/a"
        print(
            f"{row['item_id']:14} caption={str(c_rank):>4} "
            f"vision={str(v_rank):>4} move={move:>4} "
            f"reachable={'yes' if row['reachable'] else 'no'}"
        )
        if row["score_delta"] is not None:
            print(
                f"{'':14} score {row['caption_score']:.4f} -> "
                f"{row['vision_score']:.4f} ({row['score_delta']:+.4f}); "
                f"style {row['caption_style']} -> {row['vision_style']}"
            )
