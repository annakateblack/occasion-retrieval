"""Synthetic tests for the image-enrichment integration and evaluation seam."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from rental_retrieval.eval.io import load_private_judgments
from rental_retrieval.eval.metrics import (
    QueryJudgments,
    boundary_ties,
    field_coverage,
    ndcg_at_k,
    top_k_overlap,
)
from rental_retrieval.retrieval.attribute_provider import caption_resolution
from rental_retrieval.retrieval.baseline import rank
from rental_retrieval.schemas.enrichment import AttributeObservation
from rental_retrieval.schemas.event import EventContext, LengthPreference, PriceRule
from rental_retrieval.schemas.instagram import InstagramSavedPost, SizeCandidate
from rental_retrieval.schemas.profile import PersonalFitProfile, SizeRule, StylePreferences


@pytest.fixture
def profile() -> PersonalFitProfile:
    return PersonalFitProfile(
        profile_id="synthetic-test",
        dress_sizes=SizeRule(
            ideal_numeric=[4], workable_numeric=[6], unlikely_numeric=[2],
            ideal_alpha=["S"], workable_alpha=["M"], unlikely_alpha=["L"],
        ),
        style=StylePreferences(
            preferred_necklines=["cowl"], preferred_silhouettes=["flowing"]
        ),
    )


def make_post(code: str, caption: str, numeric: list[int]) -> InstagramSavedPost:
    return InstagramSavedPost(
        shortcode=code,
        permalink=f"https://www.instagram.com/p/{code}/",
        raw_caption=caption,
        rental_price_cents=4500,
        classification="rental_listing",
        garment_type="dress",
        size_candidates=[
            SizeCandidate(raw_text=f"size {size}", numeric=size, pattern="synthetic")
            for size in numeric
        ],
    )


def date_night_event() -> EventContext:
    return EventContext(
        name="Synthetic Date Night",
        event_types=["date_night"],
        event_date=date(2026, 9, 26),
        length_preference=LengthPreference(mini=1.0, midi=0.6, maxi=0.2, unknown=0.5),
        price_rule=PriceRule(max_rental_price_cents=10000, is_hard_filter=True),
    )


def test_ndcg_rewards_the_better_order():
    labels = {"best": 3, "okay": 2, "weak": 1, "bad": 0}
    good = ndcg_at_k(["best", "okay", "weak", "bad"], labels, k=4)
    bad = ndcg_at_k(["bad", "weak", "okay", "best"], labels, k=4)
    assert good == pytest.approx(1.0)
    assert good > bad


def test_ndcg_treats_unjudged_items_as_zero():
    labels = {"known": 2}
    assert ndcg_at_k(["unknown", "known"], labels, k=2) < 1.0


def test_negative_relevance_is_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        ndcg_at_k(["bad"], {"bad": -1})


def test_top_k_overlap_uses_available_denominator():
    assert top_k_overlap(["a", "b", "c"], ["b", "c", "d"], k=10) == pytest.approx(2 / 3)


def test_attribute_coverage_preserves_unknowns():
    rows = [
        {"neckline": "cowl", "fabric": None},
        {"neckline": None, "fabric": "unknown"},
    ]
    assert field_coverage(rows, ["neckline", "fabric"]) == {
        "neckline": 0.5,
        "fabric": 0.0,
    }


def test_boundary_ties_require_a_row_below_cutoff():
    count, observable = boundary_ties([0.9, 0.8, 0.8], k=2)
    assert count == 2
    assert observable is True

    count, observable = boundary_ties([0.9, 0.8], k=2)
    assert count == 1
    assert observable is False


def test_private_loader_rejects_labels_outside_local_eval(tmp_path: Path):
    public = tmp_path / "tests" / "labels.json"
    public.parent.mkdir()
    public.write_text(
        json.dumps({"query_id": "synthetic", "labels": {"A": 3}, "is_measured": True}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="data/local/eval"):
        load_private_judgments(public, repo_root=tmp_path)


def test_private_loader_requires_measured_flag(tmp_path: Path):
    target = tmp_path / "data" / "local" / "eval" / "labels.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        QueryJudgments(query_id="synthetic", labels={"A": 3}).model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="is_measured=true"):
        load_private_judgments(target, repo_root=tmp_path)


def test_enriched_provider_uses_same_ranker(profile):
    post = make_post("VISION", "mini dress size 4 $45", numeric=[4])

    def provider(candidate):
        resolution = caption_resolution(candidate)
        resolution.values["neckline"] = "cowl"
        resolution.observations["neckline"] = [
            AttributeObservation(
                value="cowl", source="vision", confidence=0.94,
                provider="synthetic_stub", model_version="test-v1",
            )
        ]
        return resolution

    baseline = rank([post], profile, date_night_event())
    enriched = rank([post], profile, date_night_event(), attribute_provider=provider)

    assert baseline.results[0].style_score is None
    assert enriched.results[0].style_score == 1.0
    assert enriched.results[0].attribute_provenance["neckline"][0]["source"] == "vision"


def test_provider_missing_ranker_fields_fails_fast(profile):
    from rental_retrieval.schemas.enrichment import AttributeResolution

    post = make_post("BROKEN", "mini dress size 4 $45", numeric=[4])
    with pytest.raises(ValueError, match="omitted required fields"):
        rank(
            [post], profile, date_night_event(),
            attribute_provider=lambda _: AttributeResolution(values={"length": "mini"}),
        )
