"""Tests for the Work-branch integration: fill policy and the adapter.

These run WITHOUT the Work branch by injecting `tests/_work_contracts_double.py`,
an exact transcription of Work's `AttributeObservation` / `AttributeResolution`.
The adapter binds to Work's real classes in production; the double exists so the
policy is tested against the true shape rather than an approximation.

Synthetic data only.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _work_contracts_double import (  # noqa: E402
    REQUIRED_KEYS as DOUBLE_REQUIRED_KEYS,
)
from _work_contracts_double import (
    AttributeObservation,
    AttributeResolution,
    make_caption_resolution,
)

from rental_retrieval.enrichment.contract import PROMPT_VERSION  # noqa: E402
from rental_retrieval.enrichment.resolution_policy import (  # noqa: E402
    DEFAULT_MIN_CONFIDENCE,
    NEVER_WRITTEN_BY_VISION,
    VISION_FIELD_TO_KEY,
    apply_vision,
    format_conflict,
    outcome_report,
    project_to_key,
)
from rental_retrieval.enrichment.work_adapter import (  # noqa: E402
    make_vision_attribute_provider,
)
from rental_retrieval.enrichment.work_contracts import (  # noqa: E402
    REQUIRED_KEYS,
    UNKNOWN_VALUES,
    VISION_FILLABLE_KEYS,
    is_unknown,
)
from rental_retrieval.schemas.visual import (  # noqa: E402
    AttributeValue,
    EnrichedListing,
    VisualAttributes,
)

# --- Helpers -----------------------------------------------------------------

@dataclass
class FakePost:
    shortcode: str
    raw_caption: str | None = "dress size 4 $45"


def vision(**pairs) -> EnrichedListing:
    attrs = {k: AttributeValue(value=v, confidence=c, evidence=f"visible {v}")
             for k, (v, c) in pairs.items()}
    return EnrichedListing(
        shortcode="X", permalink="https://www.instagram.com/p/X/", image_ref="x",
        attributes=VisualAttributes(**attrs), provider="anthropic",
        model_version="claude-sonnet-5", prompt_version=PROMPT_VERSION,
        extracted_at=datetime(2026, 9, 20, tzinfo=UTC))


def run_policy(base, enriched, **kwargs):
    return apply_vision(base, enriched, resolution_cls=AttributeResolution,
                        observation_cls=AttributeObservation, **kwargs)


def provider_for(enriched_by_code, caption_fn=None, **kwargs):
    return make_vision_attribute_provider(
        enriched_by_code,
        caption_resolution=caption_fn or (lambda p: make_caption_resolution()),
        resolution_cls=AttributeResolution,
        observation_cls=AttributeObservation,
        **kwargs)


# --- Contract constants ------------------------------------------------------

def test_required_keys_match_the_work_contract():
    assert REQUIRED_KEYS == (
        "length", "colour_family", "sleeve", "neckline",
        "silhouette", "sparkle", "fabric", "availability")
    assert set(REQUIRED_KEYS) == set(DOUBLE_REQUIRED_KEYS)


def test_seven_keys_are_fillable_and_availability_is_not():
    assert set(VISION_FILLABLE_KEYS) == set(REQUIRED_KEYS) - {"availability"}
    assert len(VISION_FILLABLE_KEYS) == 7
    assert "availability" in NEVER_WRITTEN_BY_VISION


def test_field_map_targets_only_fillable_keys():
    assert set(VISION_FIELD_TO_KEY.values()) == set(VISION_FILLABLE_KEYS)


@pytest.mark.parametrize("value,expected", [
    (None, True), ("unknown", True), ("mini", False), ("red", False), ("", False),
])
def test_unknown_semantics_are_exactly_none_or_unknown(value, expected):
    """Work's values are `str | None`; only None and 'unknown' are unfilled."""
    assert is_unknown(value) is expected
    assert UNKNOWN_VALUES == frozenset({None, "unknown"})

def test_base_resolution_is_not_mutated():
    base = make_caption_resolution(length="midi")
    result, _ = run_policy(base, vision(neckline=("cowl", 0.9)))
    assert base.values["neckline"] is None        # base untouched
    assert result.values["neckline"] == "cowl"    # copy carries the fill
    assert result is not base


def test_all_required_keys_survive():
    result, _ = run_policy(make_caption_resolution(), vision(neckline=("cowl", 0.9)))
    assert set(result.values) == set(REQUIRED_KEYS)


def test_no_enrichment_returns_a_faithful_copy():
    """The property that makes the two arms comparable."""
    base = make_caption_resolution(length="midi", availability="available")
    result, outcome = run_policy(base, None)
    assert result.values == base.values
    assert result.conflicts == base.conflicts
    assert outcome.fill_count == 0


# --- Rule: fill UNKNOWN only -------------------------------------------------

@pytest.mark.parametrize("current", [None, "unknown"])
def test_vision_fills_an_unknown_slot(current):
    base = make_caption_resolution(neckline=current)
    result, outcome = run_policy(base, vision(neckline=("cowl", 0.9)))
    assert result.values["neckline"] == "cowl"
    assert outcome.filled == {"neckline": "cowl"}


def test_vision_below_the_floor_fills_nothing_and_adds_no_observation():
    base = make_caption_resolution()
    result, outcome = run_policy(base, vision(neckline=("cowl", 0.2)))
    assert result.values["neckline"] is None
    assert outcome.skipped_low_confidence == ["neckline"]
    assert outcome.observations_added == 0


def test_confidence_exactly_at_the_floor_fills():
    base = make_caption_resolution()
    result, _ = run_policy(base, vision(neckline=("cowl", DEFAULT_MIN_CONFIDENCE)))
    assert result.values["neckline"] == "cowl"


def test_unusable_image_changes_nothing():
    base = make_caption_resolution()
    listing = vision(neckline=("cowl", 0.99))
    listing.attributes.image_quality = "unusable"
    result, outcome = run_policy(base, listing)
    assert result.values["neckline"] is None
    assert any("unusable" in n for n in outcome.notes)


# --- Rule: caption wins a disagreement ---------------------------------------

def test_caption_value_survives_a_confident_disagreement():
    base = make_caption_resolution(length="midi")
    result, outcome = run_policy(base, vision(hem_length=("mini", 0.99)))
    assert result.values["length"] == "midi"
    assert outcome.conflicted == {"length": ("midi", "mini")}


def test_disagreement_appends_the_vision_observation():
    base = make_caption_resolution(length="midi")
    result, _ = run_policy(base, vision(hem_length=("mini", 0.99)))
    sources = [o.source for o in result.observations["length"]]
    assert sources == ["caption", "vision"]
    assert result.observations["length"][-1].value == "mini"


def test_disagreement_records_a_conflict_string():
    base = make_caption_resolution(length="midi")
    result, _ = run_policy(base, vision(hem_length=("mini", 0.9)))
    assert len(result.conflicts) == 1
    line = result.conflicts[0]
    assert line.startswith("length:")
    assert "kept caption" in line


def test_agreement_adds_an_observation_but_no_conflict():
    base = make_caption_resolution(colour_family="red")
    result, outcome = run_policy(base, vision(colour_family=("red", 0.9)))
    assert result.conflicts == []
    assert outcome.agreed == ["colour_family"]
    assert len(result.observations["colour_family"]) == 2


def test_existing_conflicts_are_preserved():
    base = make_caption_resolution(length="midi")
    base.conflicts.append("pre-existing conflict from caption parsing")
    result, _ = run_policy(base, vision(hem_length=("mini", 0.9)))
    assert len(result.conflicts) == 2
    assert result.conflicts[0].startswith("pre-existing")


def test_conflict_format_is_greppable():
    line = format_conflict("length", "midi", "mini", 0.91)
    assert "length:" in line and "caption='midi'" in line and "vision='mini'" in line


# --- Rule: availability is never written by vision ---------------------------

def test_vision_cannot_fill_availability_even_when_unknown():
    base = make_caption_resolution(availability=None)
    result, outcome = run_policy(
        base, vision(neckline=("cowl", 0.99)),
        field_map={"neckline": "availability"})
    assert result.values["availability"] is None
    assert "availability" in outcome.refused
    assert outcome.filled == {}


def test_vision_cannot_overwrite_a_known_availability():
    base = make_caption_resolution(availability="available")
    result, _ = run_policy(
        base, vision(neckline=("cowl", 0.99)),
        field_map={"neckline": "availability"})
    assert result.values["availability"] == "available"


def test_availability_refusal_adds_no_observation():
    base = make_caption_resolution()
    result, outcome = run_policy(
        base, vision(neckline=("cowl", 0.99)),
        field_map={"neckline": "availability"})
    assert outcome.observations_added == 0
    assert "availability" not in result.observations


def test_size_price_and_dates_are_outside_the_resolution_entirely():
    """They are not keys of AttributeResolution.values, so there is no path."""
    for key in ("size", "price", "rental_price_cents", "event_date"):
        assert key not in REQUIRED_KEYS


# --- Vocabulary projection ---------------------------------------------------

@pytest.mark.parametrize("hem,expected", [
    ("mini", "mini"), ("above_knee", "mini"), ("knee", "midi"), ("midi", "midi"),
    ("tea", "midi"), ("maxi", "maxi"), ("floor", "maxi"),
])
def test_hem_projects_onto_length(hem, expected):
    assert project_to_key("hem_length", hem) == expected
    result, _ = run_policy(make_caption_resolution(), vision(hem_length=(hem, 0.9)))
    assert result.values["length"] == expected


def test_embellishment_projects_onto_sparkle():
    assert project_to_key("embellishment", "sequin") == "yes"
    assert project_to_key("embellishment", "none") is None
    result, _ = run_policy(make_caption_resolution(), vision(embellishment=("sequin", 0.9)))
    assert result.values["sparkle"] == "yes"


def test_sleeve_and_texture_map_to_their_work_keys():
    result, _ = run_policy(
        make_caption_resolution(),
        vision(sleeve_length=("long", 0.9), texture=("velvet", 0.9)))
    assert result.values["sleeve"] == "long"
    assert result.values["fabric"] == "velvet"


# --- Internal-only fields stay internal --------------------------------------

def test_internal_only_attributes_never_reach_the_resolution():
    base = make_caption_resolution()
    listing = vision(neckline=("cowl", 0.9))
    for name, value in [("brightness", "light"), ("saturation", "vivid"),
                        ("strap_type", "halter_tie"), ("shoulder_emphasis", "draped"),
                        ("fit_impression", "flowing"), ("pattern", "solid"),
                        ("formality", "4")]:
        setattr(listing.attributes, name,
                AttributeValue(value=value, confidence=0.95, evidence="t"))

    result, _ = run_policy(base, listing)
    assert set(result.values) == set(REQUIRED_KEYS)
    for name in ("brightness", "saturation", "strap_type", "shoulder_emphasis",
                 "fit_impression", "pattern", "formality"):
        assert name not in result.values
        assert name not in result.observations


def test_internal_fields_remain_available_in_the_cache_schema():
    """They are kept for later experiments, just not injected into the contract."""
    listing = vision(neckline=("cowl", 0.9))
    listing.attributes.brightness = AttributeValue(value="light", confidence=0.9)
    assert listing.attributes.brightness.value == "light"


# --- Observations ------------------------------------------------------------

def test_vision_observation_carries_provider_and_model_version():
    result, _ = run_policy(make_caption_resolution(), vision(neckline=("cowl", 0.9)))
    obs = result.observations["neckline"][-1]
    assert obs.source == "vision"
    assert obs.provider == "anthropic"
    assert obs.model_version == "claude-sonnet-5"
    assert obs.confidence == 0.9
    assert obs.evidence == "visible cowl"


def test_observations_accumulate_rather_than_replace():
    base = make_caption_resolution(colour_family="red")
    assert len(base.observations["colour_family"]) == 1
    result, _ = run_policy(base, vision(colour_family=("blue", 0.9)))
    assert len(result.observations["colour_family"]) == 2


# --- The adapter -------------------------------------------------------------

def test_provider_is_callable_and_returns_a_work_resolution():
    provider = provider_for({})
    result = provider(FakePost("AAA"))
    assert callable(provider)
    assert isinstance(result, AttributeResolution)


def test_provider_calls_caption_resolution_for_every_post():
    seen: list[str] = []

    def spy(post):
        seen.append(post.shortcode)
        return make_caption_resolution()

    provider = provider_for({}, caption_fn=spy)
    provider(FakePost("AAA"))
    provider(FakePost("BBB"))
    assert seen == ["AAA", "BBB"]


def test_provider_keys_enrichment_by_shortcode():
    provider = provider_for({"AAA": vision(neckline=("cowl", 0.9))})
    assert provider(FakePost("AAA")).values["neckline"] == "cowl"
    assert provider(FakePost("BBB")).values["neckline"] is None


def test_unenriched_listing_equals_the_caption_arm():
    caption_fn = lambda p: make_caption_resolution(length="midi")  # noqa: E731
    baseline = caption_fn(FakePost("ZZZ"))
    provider = provider_for({}, caption_fn=caption_fn)
    assert provider(FakePost("ZZZ")).values == baseline.values


def test_provider_records_one_outcome_per_listing():
    provider = provider_for({"AAA": vision(neckline=("cowl", 0.9))})
    provider(FakePost("AAA"))
    provider(FakePost("BBB"))
    assert set(provider.outcomes) == {"AAA", "BBB"}
    assert provider.outcomes["AAA"].fill_count == 1
    assert provider.outcomes["BBB"].fill_count == 0


def test_provider_report_is_counts_only():
    provider = provider_for({"AAA": vision(neckline=("cowl", 0.9))})
    provider(FakePost("AAA"))
    report = provider.report()
    assert report["fills_by_key"] == {"neckline": 1}
    assert report["never_written_by_vision"] == ["availability"]
    assert len(report["fillable_keys"]) == 7


def test_provider_is_deterministic():
    enriched = {"AAA": vision(neckline=("cowl", 0.9), hem_length=("mini", 0.8))}
    a = provider_for(enriched)(FakePost("AAA"))
    b = provider_for(enriched)(FakePost("AAA"))
    assert a.values == b.values
    assert a.conflicts == b.conflicts

def test_custom_confidence_floor_is_honoured():
    provider = provider_for({"AAA": vision(neckline=("cowl", 0.4))},
                            min_confidence=0.3)
    assert provider(FakePost("AAA")).values["neckline"] == "cowl"


# --- Aggregate report --------------------------------------------------------

def test_outcome_report_counts_fills_conflicts_and_refusals():
    a, oa = run_policy(make_caption_resolution(), vision(neckline=("cowl", 0.9)))
    b, ob = run_policy(make_caption_resolution(length="midi"),
                       vision(hem_length=("mini", 0.9)))
    report = outcome_report({"A": oa, "B": ob})
    assert report["listings"] == 2
    assert report["fills_by_key"] == {"neckline": 1}
    assert report["total_conflicts"] == 1
    assert report["observations_added"] == 2


def test_outcome_report_handles_an_empty_run():
    assert outcome_report({}) == {"listings": 0}
