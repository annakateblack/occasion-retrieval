"""Tests for the deterministic baseline ranker.

All data here is SYNTHETIC: invented handles, captions and permalinks.
See docs/DATA_HANDLING.md.
"""

from __future__ import annotations

from datetime import date

import pytest

from rental_retrieval.retrieval import attributes as attr
from rental_retrieval.retrieval import scoring
from rental_retrieval.retrieval.baseline import rank
from rental_retrieval.retrieval.vibe import parse_vibe_text, resolve_event_tags
from rental_retrieval.schemas.event import EventContext, LengthPreference, PriceRule
from rental_retrieval.schemas.instagram import InstagramSavedPost, SizeCandidate
from rental_retrieval.schemas.profile import (
    PersonalFitProfile,
    SizeRule,
    StylePreferences,
)


# --- Fixtures ----------------------------------------------------------------

@pytest.fixture
def profile() -> PersonalFitProfile:
    return PersonalFitProfile(
        profile_id="test",
        dress_sizes=SizeRule(
            ideal_numeric=[4], workable_numeric=[6], unlikely_numeric=[2],
            ideal_alpha=["S"], workable_alpha=["M", "XS"], unlikely_alpha=["L", "XL"],
        ),
        style=StylePreferences(
            preferred_necklines=["plunge_v", "asymmetric", "cowl"],
            lower_preference_necklines=["straight_strapless"],
            preferred_silhouettes=["flowing", "slip"],
            lower_preference_silhouettes=["structured"],
        ),
    )


def make_post(code: str, caption: str, price_cents: int | None = 4500,
              numeric: list[int] | None = None,
              alpha: list[str] | None = None) -> InstagramSavedPost:
    sizes = []
    for n in (numeric or []):
        sizes.append(SizeCandidate(raw_text=f"size {n}", numeric=n, pattern="test"))
    for a in (alpha or []):
        sizes.append(SizeCandidate(raw_text=f"size {a}", alpha=a, pattern="test"))
    return InstagramSavedPost(
        shortcode=code,
        permalink=f"https://www.instagram.com/p/{code}/",
        owner_username="sample_rental_01",
        raw_caption=caption,
        rental_price_cents=price_cents,
        classification="rental_listing",
        garment_type="dress",
        size_candidates=sizes,
    )


def date_night_event(**overrides) -> EventContext:
    base = dict(
        name="Generic Date Night",
        event_types=["date_night", "cocktail"],
        event_date=date(2026, 9, 26),
        length_preference=LengthPreference(mini=1.0, midi=0.65, maxi=0.2, unknown=0.5),
        price_rule=PriceRule(max_rental_price_cents=10000,
                             applies_to_lengths=["mini", "midi"], is_hard_filter=True),
    )
    base.update(overrides)
    return EventContext(**base)


# --- Size behaviour ----------------------------------------------------------

def test_size_4_ranks_above_incompatible_size_2(profile):
    good = make_post("SIZEOK", "mini dress available $45", numeric=[4])
    bad = make_post("SIZENO", "mini dress available $45", numeric=[2])
    run = rank([good, bad], profile, date_night_event())
    codes = [r.shortcode for r in run.results]
    assert "SIZEOK" in codes
    # Size 2 is the only size offered and sits in the unlikely band -> excluded.
    assert "SIZENO" not in codes
    assert run.rejected_by_filter.get("size") == 1


def test_size_6_stays_eligible_with_lower_confidence(profile):
    ideal = make_post("S4", "mini dress $45", numeric=[4])
    stretch = make_post("S6", "mini dress $45", numeric=[6])
    run = rank([ideal, stretch], profile, date_night_event())
    codes = [r.shortcode for r in run.results]
    assert codes == ["S4", "S6"]
    by = {r.shortcode: r for r in run.results}
    assert by["S6"].fit_score < by["S4"].fit_score
    assert by["S6"].size_band == "workable"


def test_listing_offering_both_sizes_uses_the_better_band(profile):
    post = make_post("BOTH", "mini dress sizes 2-4 $45", numeric=[2, 4])
    run = rank([post], profile, date_night_event())
    assert run.results[0].size_band == "ideal"
    assert run.rejected_by_filter.get("size") is None


def test_missing_size_is_eligible_and_flagged(profile):
    post = make_post("NOSIZE", "cute mini dress $45", numeric=[], alpha=[])
    run = rank([post], profile, date_night_event())
    assert run.results
    assert run.results[0].fit_score is None
    assert "size not stated" in run.results[0].flags


# --- Price -------------------------------------------------------------------

def test_mini_over_100_excluded_for_date_night(profile):
    post = make_post("PRICEY", "mini dress size 4", price_cents=12500, numeric=[4])
    run = rank([post], profile, date_night_event())
    assert run.results == []
    assert run.rejected_by_filter.get("price_ceiling") == 1


def test_mini_under_100_passes(profile):
    post = make_post("CHEAP", "mini dress size 4", price_cents=9500, numeric=[4])
    run = rank([post], profile, date_night_event())
    assert len(run.results) == 1


def test_price_ceiling_not_enforced_when_length_unknown(profile):
    """A mini-scoped ceiling cannot be applied to a dress of unstated length."""
    post = make_post("UNKLEN", "gorgeous dress size 4", price_cents=12500, numeric=[4])
    run = rank([post], profile, date_night_event())
    assert len(run.results) == 1
    pf = {f.name: f for f in run.results[0].hard_filters}
    assert pf["price_ceiling"].outcome == "not_applicable"


def test_formal_event_allows_higher_price(profile):
    formal = EventContext(
        name="Formal", event_types=["formal"], event_date=date(2027, 4, 10),
        length_preference=LengthPreference(mini=0.3, midi=0.6, maxi=1.0),
        price_rule=PriceRule(max_rental_price_cents=None, is_hard_filter=False),
    )
    post = make_post("GOWN", "floor length gown size 4", price_cents=18000, numeric=[4])
    run = rank([post], profile, formal)
    assert len(run.results) == 1


# --- Availability ------------------------------------------------------------

def test_unavailable_is_excluded(profile):
    post = make_post("BOOKED", "mini dress size 4 $45 - booked for that weekend", numeric=[4])
    run = rank([post], profile, date_night_event())
    assert run.results == []
    assert run.rejected_by_filter.get("availability") == 1


def test_unknown_availability_remains_eligible_and_flagged(profile):
    post = make_post("NOAVAIL", "mini dress size 4 $45", numeric=[4])
    run = rank([post], profile, date_night_event())
    assert len(run.results) == 1
    assert run.results[0].availability_status == "unknown"
    assert "confirm with renter" in run.results[0].flags
    # Unobserved, so the reported score is None; it contributes 0.5 internally.
    assert run.results[0].availability_score is None
    avail = next(c for c in run.results[0].components if c.name == "availability")
    assert avail.effective_score == 0.5


def test_stated_available_scores_above_unknown(profile):
    known = make_post("AVAIL", "mini dress size 4 $45 still available", numeric=[4])
    unknown = make_post("UNKN", "mini dress size 4 $45", numeric=[4])
    run = rank([known, unknown], profile, date_night_event())
    by = {r.shortcode: r for r in run.results}
    assert by["AVAIL"].availability_score == 1.0
    assert by["UNKN"].availability_score is None
    assert by["AVAIL"].overall_score > by["UNKN"].overall_score


# --- Length preference -------------------------------------------------------

def test_mini_preferred_over_midi_for_date_night(profile):
    mini = make_post("MINI", "mini dress size 4 $45", numeric=[4])
    midi = make_post("MIDI", "midi dress size 4 $45", numeric=[4])
    run = rank([mini, midi], profile, date_night_event())
    assert [r.shortcode for r in run.results] == ["MINI", "MIDI"]


def test_midi_remains_eligible(profile):
    midi = make_post("MIDI", "midi dress size 4 $45", numeric=[4])
    run = rank([midi], profile, date_night_event())
    assert len(run.results) == 1
    assert run.results[0].dress_length == "midi"


def test_maxi_preferred_for_formal(profile):
    formal = EventContext(
        name="Formal", event_types=["formal"], event_date=date(2027, 4, 10),
        length_preference=LengthPreference(mini=0.3, midi=0.6, maxi=1.0),
        price_rule=PriceRule(is_hard_filter=False),
    )
    mini = make_post("MINI", "mini dress size 4 $45", numeric=[4])
    maxi = make_post("MAXI", "floor length gown size 4 $45", numeric=[4])
    run = rank([mini, maxi], profile, formal)
    assert [r.shortcode for r in run.results] == ["MAXI", "MINI"]


# --- Vibe parsing ------------------------------------------------------------

def test_my_tie_boosts_bright_late_summer_terms(profile):
    event = date_night_event(
        name="My Tie",
        vibe_text="bright fun elevated late-summer, avoid heavy wintery styles",
    )
    bright = make_post("BRIGHT", "bright pink mini dress size 4 $45", numeric=[4])
    plain = make_post("PLAIN", "black mini dress size 4 $45", numeric=[4])
    run = rank([bright, plain], profile, event)
    by = {r.shortcode: r for r in run.results}
    assert by["BRIGHT"].event_score > by["PLAIN"].event_score


def test_my_tie_negation_suppresses_cold_weather_tag():
    positive, discouraged, _ = parse_vibe_text(
        "bright fun elevated late-summer, avoid heavy wintery styles"
    )
    assert "bright" in positive
    assert "warm_weather" in positive
    assert "cold_weather" in discouraged
    assert "cold_weather" not in positive


def test_not_too_girly_is_read_as_a_negation():
    positive, discouraged, _ = parse_vibe_text("feminine but not too girly")
    assert "romantic" in discouraged
    assert "romantic" not in positive


def test_christmas_cocktail_boosts_holiday_colours_and_sparkle(profile):
    event = date_night_event(
        name="Christmas Cocktail",
        event_date=date(2026, 12, 12),
        vibe_text="holiday red green metallic sparkle, colder weather, long sleeve a plus",
        vibe_tags=["holiday", "long_sleeve_bonus"],
        price_rule=PriceRule(max_rental_price_cents=10000,
                             applies_to_lengths=["mini"], is_hard_filter=True),
    )
    festive = make_post("FESTIVE", "red sequin mini dress size 4 $45", numeric=[4])
    plain = make_post("PLAIN", "beige mini dress size 4 $45", numeric=[4])
    run = rank([festive, plain], profile, event)
    by = {r.shortcode: r for r in run.results}
    assert by["FESTIVE"].event_score > by["PLAIN"].event_score


def test_long_sleeve_boosted_when_event_asks_for_it(profile):
    event = date_night_event(
        name="Christmas Cocktail", event_date=date(2026, 12, 12),
        vibe_tags=["holiday", "long_sleeve_bonus"],
    )
    sleeved = make_post("SLV", "long sleeve red mini dress size 4 $45", numeric=[4])
    sleeveless = make_post("NOSLV", "red mini dress size 4 $45", numeric=[4])
    run = rank([sleeved, sleeveless], profile, event)
    by = {r.shortcode: r for r in run.results}
    assert by["SLV"].event_score >= by["NOSLV"].event_score


def test_unknown_tags_are_dropped_and_noted(profile):
    event = date_night_event(vibe_tags=["holiday", "not_a_real_tag"])
    run = rank([make_post("X", "mini dress size 4 $45", numeric=[4])], profile, event)
    assert any("controlled vocabulary" in n for n in run.notes)


def test_explicit_tags_merge_with_parsed_tags():
    merged, _, _ = resolve_event_tags(["holiday"], "bright and sparkly")
    assert "holiday" in merged
    assert "bright" in merged
    assert "sparkle" in merged


# --- Unknown handling --------------------------------------------------------

def test_unknown_attributes_do_not_score_as_zero(profile):
    """A terse caption must not be punished for brevity."""
    terse = make_post("TERSE", "size 4 $45", numeric=[4])
    run = rank([terse], profile, date_night_event())
    result = run.results[0]
    assert result.style_score is None
    assert result.seasonality_score is None
    assert result.overall_score > 0.5       # still competitive
    assert result.coverage < 1.0            # but visibly thin evidence


def test_coverage_reflects_evaluable_weight(profile):
    rich = make_post("RICH", "flowing red velvet long sleeve deep v mini dress size 4 $45",
                     numeric=[4])
    terse = make_post("TERSE", "size 4 $45", numeric=[4])
    run = rank([rich, terse], profile, date_night_event())
    by = {r.shortcode: r for r in run.results}
    assert by["RICH"].coverage > by["TERSE"].coverage


def test_attributes_never_invented(profile):
    post = make_post("BARE", "size 4 $45", numeric=[4])
    run = rank([post], profile, date_night_event())
    a = run.results[0].attributes
    assert a["neckline"] is None
    assert a["colour_family"] is None
    assert a["sleeve"] is None
    assert a["sparkle"] is None
    assert a["length"] == "unknown"


def test_sparkle_absence_is_none_not_false():
    value, evidence = attr.extract_sparkle("plain cotton dress")
    assert value is None
    assert evidence is None


# --- Style preferences -------------------------------------------------------

def test_preferred_neckline_scores_above_lower_preference(profile):
    liked = make_post("VNECK", "deep v mini dress size 4 $45", numeric=[4])
    less = make_post("STRAP", "straight across strapless mini dress size 4 $45", numeric=[4])
    run = rank([liked, less], profile, date_night_event())
    by = {r.shortcode: r for r in run.results}
    assert by["VNECK"].style_score > by["STRAP"].style_score


# --- Output contract ---------------------------------------------------------

def test_does_not_pad_to_ten(profile):
    posts = [make_post(f"P{i}", "mini dress size 4 $45", numeric=[4]) for i in range(3)]
    run = rank(posts, profile, date_night_event(), top_k=10)
    assert run.returned == 3
    assert any("Returned 3 of a requested 10" in n for n in run.notes)


def test_respects_top_k(profile):
    posts = [make_post(f"P{i:02d}", "mini dress size 4 $45", numeric=[4]) for i in range(25)]
    run = rank(posts, profile, date_night_event(), top_k=10)
    assert run.returned == 10
    assert [r.rank for r in run.results] == list(range(1, 11))


def test_every_result_carries_full_breakdown(profile):
    post = make_post("FULL", "bright red flowing deep v mini dress size 4 $45 available",
                     numeric=[4])
    run = rank([post], profile, date_night_event())
    r = run.results[0]
    assert r.permalink and r.owner_username and r.raw_caption
    assert r.hard_filters and r.components
    assert r.explanation
    names = {c.name for c in r.components}
    assert names == {"fit", "style", "event", "seasonality", "price", "availability"}


def test_hard_filter_results_present_on_passing_rows(profile):
    post = make_post("HF", "mini dress size 4 $45", numeric=[4])
    run = rank([post], profile, date_night_event())
    outcomes = {f.name: f.outcome for f in run.results[0].hard_filters}
    assert set(outcomes) == {"availability", "size", "price_ceiling"}
    assert "fail" not in outcomes.values()


# --- Determinism and consistency --------------------------------------------

def test_ranking_is_deterministic(profile):
    posts = [make_post(f"D{i:02d}", "mini dress size 4 $45", numeric=[4]) for i in range(20)]
    event = date_night_event()
    a = rank(posts, profile, event).model_dump()
    b = rank(posts, profile, event).model_dump()
    assert a == b


def test_identical_scores_break_ties_stably(profile):
    posts = [make_post(c, "mini dress size 4 $45", numeric=[4]) for c in ("ZZZ", "AAA", "MMM")]
    run = rank(posts, profile, date_night_event())
    assert [r.shortcode for r in run.results] == ["AAA", "MMM", "ZZZ"]


def test_explanation_is_consistent_with_score_components(profile):
    """Every unevaluable component must be named, and coverage stated when
    anything was unevaluable. At full coverage there is nothing to disclose, so
    the coverage sentence is correctly absent."""
    post = make_post("EXP", "bright red flowing mini dress size 4 $45", numeric=[4])
    run = rank([post], profile, date_night_event())
    r = run.results[0]

    unknown_names = [c.name for c in r.components if not c.is_known]
    for name in unknown_names:
        assert name in r.explanation

    if unknown_names:
        assert f"{r.coverage:.0%}" in r.explanation
        assert "Not evaluable" in r.explanation
    else:
        assert r.coverage == 1.0
        assert "Not evaluable" not in r.explanation


def test_thin_caption_explanation_discloses_coverage(profile):
    post = make_post("THIN", "size 4 $45", numeric=[4])
    run = rank([post], profile, date_night_event())
    r = run.results[0]
    assert "Not evaluable" in r.explanation
    assert f"{r.coverage:.0%}" in r.explanation
    assert "style" in r.explanation and "seasonality" in r.explanation


def test_explanation_names_a_dragging_component(profile):
    """A low-scoring known component must be surfaced, not hidden."""
    post = make_post("DRAG", "maxi dress size 4 $95", numeric=[4])
    run = rank([post], profile, date_night_event())
    assert "Held back by" in run.results[0].explanation


def test_combine_returns_zero_when_nothing_is_evaluable():
    from rental_retrieval.schemas.ranking import ScoreComponent
    components = [
        ScoreComponent(name="fit", score=None, weight=0.35, reason="x"),
        ScoreComponent(name="price", score=None, weight=0.15, reason="y"),
    ]
    assert scoring.combine(components) == (0.0, 0.0)


def test_weights_are_reported_in_the_run(profile):
    run = rank([make_post("W", "mini dress size 4 $45", numeric=[4])],
               profile, date_night_event())
    assert run.weights == scoring.DEFAULT_WEIGHTS
    assert abs(sum(run.weights.values()) - 1.0) < 1e-9


# --- Unknown-handling policy (regression tests for a real failure) -----------

def test_missing_price_is_not_an_advantage(profile):
    """Observed on real data: dropping price entirely put every unpriced dress
    at the top, because fewer components meant a higher average."""
    priced = make_post("PRICED", "mini dress size 4 $35", price_cents=3500, numeric=[4])
    unpriced = make_post("UNPRICED", "mini dress size 4", price_cents=None, numeric=[4])
    run = rank([priced, unpriced], profile, date_night_event())
    by = {r.shortcode: r for r in run.results}
    assert by["PRICED"].overall_score > by["UNPRICED"].overall_score
    assert [r.shortcode for r in run.results][0] == "PRICED"


def test_missing_size_is_not_an_advantage(profile):
    sized = make_post("SIZED", "mini dress size 4 $45", numeric=[4])
    unsized = make_post("UNSIZED", "mini dress $45")
    run = rank([sized, unsized], profile, date_night_event())
    by = {r.shortcode: r for r in run.results}
    assert by["SIZED"].overall_score > by["UNSIZED"].overall_score


def test_unobservable_attributes_still_drop_out(profile):
    """Neckline and fabric may genuinely not apply, so they leave the average."""
    post = make_post("NOATTR", "mini dress size 4 $45", numeric=[4])
    run = rank([post], profile, date_night_event())
    by_name = {c.name: c for c in run.results[0].components}
    assert by_name["style"].neutral_fallback is None
    assert by_name["style"].counts_toward_average is False
    assert by_name["price"].counts_toward_average is True


def test_coverage_counts_only_observed_components(profile):
    """A neutral-filled component must not inflate reported confidence."""
    post = make_post("NEUTRAL", "mini dress", price_cents=None)
    run = rank([post], profile, date_night_event())
    r = run.results[0]
    assert r.fit_score is None and r.price_score is None
    # event is the only observed component (weight 0.25 of 1.00)
    assert r.coverage == pytest.approx(0.25, abs=1e-6)


def test_expensive_dress_ranks_below_cheap_one(profile):
    cheap = make_post("CHEAP", "mini dress size 4 $25", price_cents=2500, numeric=[4])
    dear = make_post("DEAR", "mini dress size 4 $95", price_cents=9500, numeric=[4])
    run = rank([cheap, dear], profile, date_night_event())
    assert [r.shortcode for r in run.results] == ["CHEAP", "DEAR"]
