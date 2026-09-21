"""Tests for the anchor label layer.

CRITICAL: these tests must run on a machine that has NO real anchor file. Every
test here uses the committed public template or fixtures built inline. Any test
that required data/local/eval/anchor_labels.json would fail in CI and, worse,
would pressure someone into committing the real labels to make it pass.

The one test that touches the real path only asserts it is absent-or-ignored,
never that it exists.
"""

from __future__ import annotations

import json
import sys
import warnings
from datetime import UTC
from pathlib import Path

import pytest

from rental_retrieval.eval.anchors import (
    DEFAULT_ANCHOR_PATH,
    TEMPLATE_PATH,
    AnchorPrivacyWarning,
    build_measurement_gate,
    check_anti_rules,
    compute_anchor_coverage,
    load_anchor_set,
    shortcode_from_permalink,
)
from rental_retrieval.schemas.anchors import GRADE_TO_SCORE, MeasurementGate

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / TEMPLATE_PATH


# --- Fixtures: synthetic only, never from private data -----------------------
# `make_anchor_set` is shared with test_live_provider.py. It lives in a helper
# module rather than in a test module so neither file has to import the other
# (`tests` is not guaranteed to be an importable package). Same pattern as
# _work_contracts_double.py.

sys.path.insert(0, str(Path(__file__).parent))


from _anchor_fixtures import make_anchor_set  # noqa: E402

# --- Permalink parsing -------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://www.instagram.com/p/ABC123/", "ABC123"),
    ("https://www.instagram.com/p/ABC123/?img_index=1", "ABC123"),
    ("https://www.instagram.com/reel/XYZ789/", "XYZ789"),
    ("https://www.instagram.com/p/A-B_C123/?img_index=3", "A-B_C123"),
    ("https://example.com/nope", None),
    ("", None),
])
def test_shortcode_extraction_handles_query_strings(url, expected):
    """Real permalinks carry ?img_index=N; the shortcode must survive it."""
    assert shortcode_from_permalink(url) == expected


# --- Grades ------------------------------------------------------------------

def test_grade_scale_matches_the_relevance_labels_table():
    assert GRADE_TO_SCORE["strong"] == 2
    assert GRADE_TO_SCORE["plausible"] == 1
    assert GRADE_TO_SCORE["weak"] == 0
    assert GRADE_TO_SCORE["not_relevant"] == 0


def test_unlabelled_event_scores_zero():
    anchors = make_anchor_set()
    item = anchors.by_shortcode()["TESTAAA1"]
    assert item.score_for("christmas_cocktail") == 0
    assert item.grade_for("christmas_cocktail") is None


def test_graded_labels_cover_every_item():
    anchors = make_anchor_set()
    labels = anchors.graded_labels("my_tie")
    assert set(labels) == {"TESTAAA1", "TESTBBB2"}
    assert labels["TESTAAA1"] == 2
    assert labels["TESTBBB2"] == 1


def test_positives_respect_the_minimum_score():
    anchors = make_anchor_set()
    assert set(anchors.positives_for("my_tie")) == {"TESTAAA1", "TESTBBB2"}
    assert anchors.positives_for("my_tie", min_score=2) == ["TESTAAA1"]
    assert anchors.positives_for("christmas_cocktail") == []


def test_event_keys_are_sorted_and_deduplicated():
    keys = make_anchor_set().event_keys()
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


# --- Measurement gate --------------------------------------------------------

def test_gate_requires_both_real_labels_and_a_live_provider():
    """'anthropic' is the name an implemented provider records. 'live' is only a
    CLI alias, so a record carrying it means a provider was constructed without
    being implemented, and the gate stays conservative about that."""
    real, synthetic = make_anchor_set(True), make_anchor_set(False)
    assert build_measurement_gate(real, "anthropic", "x").is_measured is True
    assert build_measurement_gate(real, "stub", "x").is_measured is False
    assert build_measurement_gate(synthetic, "anthropic", "x").is_measured is False
    assert build_measurement_gate(synthetic, "stub", "x").is_measured is False
    assert build_measurement_gate(None, "anthropic", "x").is_measured is False


@pytest.mark.parametrize("provider",
                         ["stub", "failing", "synthetic_fixture", "none", "", "skeleton", "live"])
def test_every_synthetic_provider_blocks_measurement(provider):
    assert build_measurement_gate(make_anchor_set(True), provider, "x").is_measured is False


def test_gate_explains_why_it_blocked():
    gate = build_measurement_gate(make_anchor_set(False), "stub", "config/seed/x.json")
    caveats = " ".join(gate.caveats())
    assert "LABELS ARE SYNTHETIC" in caveats
    assert "PROVIDER IS NOT LIVE" in caveats


def test_measured_gate_emits_no_caveats():
    gate = MeasurementGate(labels_are_real=True, provider_is_live=True,
                           provider_name="live", label_source="private")
    assert gate.is_measured is True
    assert gate.caveats() == []


# --- Loading and the privacy guard -------------------------------------------

def test_public_template_loads_and_is_not_real():
    anchors = load_anchor_set(TEMPLATE)
    assert anchors.is_real is False
    assert len(anchors.items) >= 2


def test_template_contains_no_real_looking_permalinks():
    """The committed template must be obviously fake."""
    raw = TEMPLATE.read_text(encoding="utf-8")
    assert "TEMPLATE" in raw
    anchors = load_anchor_set(TEMPLATE)
    for item in anchors.items:
        assert item.shortcode.startswith("TEMPLATE")


def test_loading_real_labels_from_a_committable_path_warns(tmp_path):
    """The combination that would leak: is_real true, path not private."""
    payload = make_anchor_set(is_real=True).model_dump(mode="json")
    path = tmp_path / "config" / "anchor_labels.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.warns(AnchorPrivacyWarning, match="gitignored"):
        load_anchor_set(path)


def test_loading_synthetic_labels_from_any_path_is_silent(tmp_path):
    payload = make_anchor_set(is_real=False).model_dump(mode="json")
    path = tmp_path / "anchor_labels.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with warnings.catch_warnings():
        warnings.simplefilter("error", AnchorPrivacyWarning)
        load_anchor_set(path)


def test_loading_real_labels_from_a_local_path_is_silent(tmp_path):
    payload = make_anchor_set(is_real=True).model_dump(mode="json")
    path = tmp_path / "data" / "local" / "eval" / "anchor_labels.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with warnings.catch_warnings():
        warnings.simplefilter("error", AnchorPrivacyWarning)
        load_anchor_set(path)


def test_missing_anchor_file_names_the_private_location():
    with pytest.raises(FileNotFoundError, match=r"data[\\/]local[\\/]eval"):
        load_anchor_set(Path("does/not/exist.json"))


def test_shortcode_is_derived_from_permalink_when_absent(tmp_path):
    payload = {
        "name": "t", "description": "t", "created_at": "2026-09-20", "is_real": False,
        "items": [{"permalink": "https://www.instagram.com/p/DERIVED123/?img_index=1",
                   "relevance": [{"event_key": "my_tie", "grade": "strong"}]}],
    }
    path = tmp_path / "a.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    anchors = load_anchor_set(path)
    assert anchors.items[0].shortcode == "DERIVED123"


def test_underscore_documentation_keys_are_ignored(tmp_path):
    payload = {
        "_privacy": "a note to humans", "name": "t", "description": "t",
        "created_at": "2026-09-20", "is_real": False,
        "items": [{"_why": "note", "shortcode": "X1",
                   "permalink": "https://www.instagram.com/p/X1/", "relevance": []}],
    }
    path = tmp_path / "a.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_anchor_set(path).items[0].shortcode == "X1"


# --- Pipeline reachability ---------------------------------------------------

def test_coverage_identifies_anchors_missing_from_the_inventory():
    anchors = make_anchor_set()
    coverage = compute_anchor_coverage(anchors, {"TESTAAA1"}, {})
    assert coverage.in_inventory == ["TESTAAA1"]
    assert coverage.missing_from_inventory == ["TESTBBB2"]
    assert coverage.inventory_rate == 0.5


def test_missing_anchor_produces_an_actionable_note():
    anchors = make_anchor_set()
    notes = " ".join(compute_anchor_coverage(anchors, set(), {}).notes())
    assert "cannot appear in any result" in notes
    assert "garment-type filter" in notes


def test_missing_enrichment_is_noted_separately_from_missing_inventory():
    """Two different failures: unreachable by the ranker, versus reachable but
    caption-only. They need different fixes, so they get different notes."""
    anchors = make_anchor_set()
    coverage = compute_anchor_coverage(anchors, {"TESTAAA1", "TESTBBB2"}, {})
    assert coverage.inventory_rate == 1.0
    assert coverage.enrichment_rate == 0.0
    notes = " ".join(coverage.notes())
    assert "no visual enrichment" in notes
    assert "cannot appear in any result" not in notes


def test_full_coverage_produces_no_notes():
    from datetime import datetime

    from rental_retrieval.enrichment.contract import PROMPT_VERSION
    from rental_retrieval.schemas.visual import EnrichedListing, VisualAttributes

    def stub(code: str) -> EnrichedListing:
        return EnrichedListing(
            shortcode=code, permalink=f"https://www.instagram.com/p/{code}/",
            image_ref="x", attributes=VisualAttributes(), provider="test",
            model_version="t", prompt_version=PROMPT_VERSION,
            extracted_at=datetime(2026, 9, 20, tzinfo=UTC),
        )

    anchors = make_anchor_set()
    coverage = compute_anchor_coverage(
        anchors, {"TESTAAA1", "TESTBBB2"},
        {"TESTAAA1": stub("TESTAAA1"), "TESTBBB2": stub("TESTBBB2")},
    )
    assert coverage.inventory_rate == 1.0
    assert coverage.enrichment_rate == 1.0
    assert coverage.notes() == []


# --- Anti-rules --------------------------------------------------------------

def test_sparkle_anti_rule_fails_when_violated():
    """An item the labeller graded 'weak' for Christmas must not top it."""
    anchors = make_anchor_set()
    results = check_anti_rules(
        anchors, {"christmas_cocktail": ["TESTBBB2", "TESTAAA1"]}, top_k=5
    )
    sparkle = [r for r in results if r.rule == "sparkle_implies_holiday"]
    assert len(sparkle) == 1
    assert sparkle[0].passed is False
    assert "christmas_cocktail" in sparkle[0].detail


def test_sparkle_anti_rule_passes_when_respected():
    anchors = make_anchor_set()
    results = check_anti_rules(
        anchors, {"christmas_cocktail": ["OTHER1", "OTHER2"]}, top_k=5
    )
    assert all(r.passed for r in results)


def test_anti_rule_ignores_events_with_no_holiday_semantics():
    anchors = make_anchor_set()
    results = check_anti_rules(anchors, {"my_tie": ["TESTBBB2"]}, top_k=5)
    assert all(r.passed for r in results)


def test_unknown_anti_rule_is_recorded_not_silently_dropped():
    anchors = make_anchor_set()
    anchors.items[0].negative_associations = ["some_future_rule"]
    results = check_anti_rules(anchors, {"christmas_cocktail": []}, top_k=5)
    unknown = [r for r in results if r.rule == "some_future_rule"]
    assert len(unknown) == 1
    assert "no checker implemented" in unknown[0].detail


# --- The two label sets stay distinct ----------------------------------------

def test_synthetic_seed_set_is_still_present_and_public():
    """The public fixture must survive the addition of the private anchor set."""
    from rental_retrieval.enrichment.seed import load_seed_set
    seed = load_seed_set(REPO / "config" / "seed" / "labeled_seed_set.json")
    assert seed.is_synthetic is True
    assert len(seed.items) == 8


def test_seed_and_anchor_sets_have_different_label_shapes():
    """Attribute labels vs relevance labels: they grade different components."""
    from rental_retrieval.enrichment.seed import load_seed_set
    seed = load_seed_set(REPO / "config" / "seed" / "labeled_seed_set.json")
    anchors = load_anchor_set(TEMPLATE)

    assert hasattr(seed.items[0], "expected_attributes")
    assert not hasattr(seed.items[0], "relevance")
    assert hasattr(anchors.items[0], "relevance")
    assert not hasattr(anchors.items[0], "expected_attributes")


def test_no_real_anchor_file_is_required_to_run_the_suite():
    """This suite must pass on a fresh clone with no private data present."""
    assert TEMPLATE.exists()
    real = REPO / DEFAULT_ANCHOR_PATH
    if real.exists():
        gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
        assert "data/local/eval/" in gitignore
