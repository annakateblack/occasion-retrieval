from __future__ import annotations

import pytest

from rental_retrieval.schemas.anchors import AnchorLabel, AnchorSet, EventRelevance
from rental_retrieval.schemas.ranking import RankedDress, RankingRun
from rental_retrieval.eval.reporting import summarize


def _row(code: str, rank: int, score: float, style: float | None) -> RankedDress:
    return RankedDress(
        rank=rank,
        permalink=f"https://example.com/{code}",
        shortcode=code,
        owner_username=None,
        raw_caption=None,
        rental_price_cents=None,
        garment_type="dress",
        dress_length="mini",
        size_summary=None,
        size_band=None,
        availability_status="unknown",
        availability_note=None,
        attributes={},
        attribute_provenance={},
        attribute_conflicts=[],
        hard_filters=[],
        passed_hard_filters=True,
        fit_score=None,
        style_score=style,
        event_score=1.0,
        seasonality_score=None,
        price_score=None,
        availability_score=None,
        components=[],
        overall_score=score,
        coverage=0.5,
        explanation="x",
        flags=[],
    )


def _run(rows: list[RankedDress]) -> RankingRun:
    return RankingRun(
        event_name="Test",
        profile_id="p",
        weights={"fit": 1.0},
        total_candidates=2,
        passed_hard_filters=2,
        returned=len(rows),
        rejected_by_filter={},
        results=rows,
        notes=[],
    )


def test_summarize_reports_anchor_movement_and_score_delta():
    caption = _run([_row("A", 2, 0.5, None), _row("B", 1, 0.7, 0.5)])
    vision = _run([_row("A", 1, 0.7, 0.5), _row("B", 2, 0.6, 0.5)])
    experiment = {
        "runs": {
            "caption_only": caption.model_dump(),
            "enriched": vision.model_dump(),
        }
    }
    anchors = AnchorSet(
        name="x",
        description="x",
        created_at="2026-09-20",
        is_real=True,
        relevance_scale_max=2,
        items=[
            AnchorLabel(
                shortcode="A",
                permalink="https://example.com/A",
                relevance=[EventRelevance(event_key="my_tie", grade="strong")],
            ),
            AnchorLabel(
                shortcode="B",
                permalink="https://example.com/B",
                relevance=[EventRelevance(event_key="my_tie", grade="plausible")],
            ),
        ],
    )

    report = summarize(experiment, anchors, "my_tie", k=2)

    assert report["anchors_comparable"] == 2
    assert report["anchors_moved_up"] == 1
    assert report["anchors_moved_down"] == 1
    assert report["mean_rank_move"] == 0
    assert report["mean_score_delta"] == pytest.approx(0.05)
    assert report["vision_ndcg_at_k"] > report["caption_ndcg_at_k"]
