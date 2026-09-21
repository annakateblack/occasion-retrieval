from __future__ import annotations

import json

import pytest

from rental_retrieval.eval.reporting import summarize
from rental_retrieval.schemas.anchors import AnchorLabel, AnchorSet, EventRelevance
from rental_retrieval.schemas.ranking import RankedDress, RankingRun


def _row(code: str, rank: int, score: float) -> RankedDress:
    return RankedDress(
        rank=rank, permalink=f"https://example.com/{code}", shortcode=code,
        owner_username=None, raw_caption=None, rental_price_cents=None,
        garment_type="dress", dress_length="mini", size_summary=None, size_band=None,
        availability_status="unknown", availability_note=None, attributes={},
        attribute_provenance={}, attribute_conflicts=[], hard_filters=[],
        passed_hard_filters=True, fit_score=None, style_score=0.5, event_score=1.0,
        seasonality_score=None, price_score=None, availability_score=None,
        components=[], overall_score=score, coverage=0.5, explanation="x", flags=[],
    )


def _run(rows: list[RankedDress], survivors: int = 10) -> RankingRun:
    return RankingRun(
        event_name="Test", profile_id="p", weights={"fit": 1.0},
        total_candidates=10, passed_hard_filters=survivors, returned=len(rows),
        rejected_by_filter={}, results=rows, notes=[],
    )


def _anchors(grades: dict[str, str]) -> AnchorSet:
    return AnchorSet(
        name="x", description="x", created_at="2026-09-20", is_real=True,
        relevance_scale_max=2,
        items=[
            AnchorLabel(
                shortcode=code, permalink=f"https://example.com/{code}",
                relevance=[EventRelevance(event_key="event", grade=grade)],
            )
            for code, grade in grades.items()
        ],
    )


def _experiment(
    caption: RankingRun,
    vision: RankingRun,
    reachable: list[str],
    provider: str = "anthropic",
) -> dict:
    return {
        "gate": {
            "labels_are_real": True,
            "provider_is_live": provider == "anthropic",
            "provider_name": provider,
            "label_source": "data/local/eval/anchors.json",
        },
        "anchor_coverage": {"in_inventory": reachable},
        "runs": {
            "caption_only": caption.model_dump(),
            "enriched": vision.model_dump(),
        },
    }


def test_partial_comparability_absent_anchors_and_mean_denominator():
    caption = _run([_row("B", 1, 0.9), _row("A", 4, 0.5)])
    vision = _run([_row("C", 1, 0.9), _row("A", 1, 0.7)])
    anchors = _anchors({"A": "strong", "B": "plausible", "C": "plausible", "D": "strong"})
    report = summarize(
        _experiment(caption, vision, ["A", "B", "C"]), anchors, "event", k=2
    )

    assert report["anchors_comparable"] == 1
    assert report["anchors_moved_up"] == 1
    assert report["mean_rank_move"] == 3
    assert report["mean_score_delta"] == pytest.approx(0.2)
    movements = {row["item_id"]: row for row in report["movements"]}
    assert movements["B"]["vision_rank"] is None
    assert movements["C"]["caption_rank"] is None
    assert movements["D"]["caption_rank"] is None
    assert movements["D"]["vision_rank"] is None


def test_empty_comparable_set_has_no_means():
    report = summarize(
        _experiment(_run([_row("A", 1, 0.8)]), _run([_row("B", 1, 0.8)]), ["A", "B"]),
        _anchors({"A": "strong", "B": "strong"}),
        "event",
    )
    assert report["anchors_comparable"] == 0
    assert report["mean_rank_move"] is None
    assert report["mean_score_delta"] is None


@pytest.mark.parametrize(
    ("caption_order", "vision_order", "positive"),
    [(["B", "A"], ["A", "B"], True), (["A", "B"], ["B", "A"], False)],
)
def test_reports_both_ndcg_delta_directions(caption_order, vision_order, positive):
    def ranked(order):
        return _run([
            _row(code, rank, 1.0 - rank / 10)
            for rank, code in enumerate(order, 1)
        ])

    report = summarize(
        _experiment(ranked(caption_order), ranked(vision_order), ["A", "B"]),
        _anchors({"A": "strong", "B": "plausible"}),
        "event",
        k=2,
    )
    delta = report["full_qrel_ndcg_at_k"]["delta"]
    assert (delta > 0) is positive


def test_unreachable_positive_separates_recall_from_ranking_loss():
    run = _run([_row("A", 1, 0.9), _row("X", 2, 0.8)])
    report = summarize(
        _experiment(run, run, ["A"]),
        _anchors({"A": "plausible", "D": "strong"}),
        "event",
        k=2,
    )
    assert report["qrels_total"] == 2
    assert report["qrels_reachable"] == 1
    assert report["positive_qrels_total"] == 2
    assert report["positive_qrels_reachable"] == 1
    assert report["full_qrel_ndcg_at_k"]["caption_only"] < 1
    assert report["reachable_qrel_ndcg_at_k"]["caption_only"] == 1
    assert report["reachability_ndcg_gap"]["caption_only"] > 0


def test_hard_filter_survivor_delta_is_reported():
    row = _row("A", 1, 0.9)
    report = summarize(
        _experiment(_run([row], 7), _run([row], 9), ["A"]),
        _anchors({"A": "strong"}),
        "event",
    )
    assert report["hard_filter_survivor_delta"] == 2


def test_gate_and_reachability_metadata_are_required():
    row = _row("A", 1, 0.9)
    experiment = _experiment(_run([row]), _run([row]), ["A"])
    experiment.pop("gate")
    with pytest.raises(ValueError, match="no MeasurementGate"):
        summarize(experiment, _anchors({"A": "strong"}), "event")

    experiment = _experiment(_run([row]), _run([row]), ["A"])
    experiment.pop("anchor_coverage")
    with pytest.raises(ValueError, match="no anchor_coverage"):
        summarize(experiment, _anchors({"A": "strong"}), "event")


def test_non_live_provider_needs_explicit_unmeasured_mode():
    row = _row("A", 1, 0.9)
    experiment = _experiment(_run([row]), _run([row]), ["A"], provider="stub")
    with pytest.raises(ValueError, match="MeasurementGate rejected"):
        summarize(experiment, _anchors({"A": "strong"}), "event")

    report = summarize(
        experiment, _anchors({"A": "strong"}), "event", require_measured=False
    )
    assert report["measurement_gate"]["is_measured"] is False


def test_redacted_report_contains_no_private_identifier():
    row = _row("REAL_SHORTCODE", 1, 0.9)
    report = summarize(
        _experiment(_run([row]), _run([row]), ["REAL_SHORTCODE"]),
        _anchors({"REAL_SHORTCODE": "strong"}),
        "event",
        redact=True,
    )
    assert "REAL_SHORTCODE" not in json.dumps(report)
    assert report["movements"][0]["item_id"] == "anchor_001"
    assert "shortcode" not in report["movements"][0]

