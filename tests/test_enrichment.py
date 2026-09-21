"""Tests for the image-enrichment layer.

All data is SYNTHETIC: invented shortcodes, placeholder permalinks, stub
provider output. No real image, permalink or handle appears here.
See docs/DATA_HANDLING.md.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from rental_retrieval.enrichment import contract, normalize
from rental_retrieval.enrichment.merge import (
    DEFAULT_MIN_CONFIDENCE,
    merge_attributes,
    merge_many,
    merge_report,
)
from rental_retrieval.enrichment.orchestrator import (
    EnrichmentCache,
    compute_field_coverage,
    enrich_batch,
    enrich_one,
    read_enriched,
    write_enriched,
)
from rental_retrieval.enrichment.providers import (
    FailingVisionProvider,
    LiveVisionProvider,
    ProviderError,
    StubVisionProvider,
    VisionProvider,
    get_provider,
)
from rental_retrieval.enrichment.seed import (
    load_seed_set,
    score_against_seed,
    to_enrichment_items,
)
from rental_retrieval.retrieval import attributes as attr
from rental_retrieval.schemas.event import EventContext, LengthPreference, PriceRule
from rental_retrieval.schemas.instagram import InstagramSavedPost, SizeCandidate
from rental_retrieval.schemas.profile import (
    PersonalFitProfile,
    SizeRule,
    StylePreferences,
)
from rental_retrieval.schemas.visual import (
    AttributeValue,
    EnrichedListing,
    VisualAttributes,
)

REPO = Path(__file__).resolve().parent.parent
SEED_PATH = REPO / "config" / "seed" / "labeled_seed_set.json"
FIXTURE = REPO / "tests" / "fixtures" / "synthetic_enriched_seed.jsonl"


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
            preferred_necklines=["plunge_v", "asymmetric", "one_shoulder", "cowl"],
            lower_preference_necklines=["straight_strapless", "high_neck"],
            preferred_silhouettes=["a_line", "fit_and_flare", "slip"],
            lower_preference_silhouettes=["corset", "sheath"],
        ),
    )


def make_post(code: str, caption: str, price_cents: int | None = 4500,
              numeric: list[int] | None = None) -> InstagramSavedPost:
    sizes = [SizeCandidate(raw_text=f"size {n}", numeric=n, pattern="test")
             for n in (numeric or [])]
    return InstagramSavedPost(
        shortcode=code, permalink=f"https://www.instagram.com/p/{code}/",
        owner_username="sample_rental_01", raw_caption=caption,
        rental_price_cents=price_cents, classification="rental_listing",
        garment_type="dress", size_candidates=sizes,
    )


def make_enriched(code: str, **attribute_kwargs) -> EnrichedListing:
    # Filter BEFORE unpacking: image_quality is a plain string and would
    # otherwise be unpacked as a (value, confidence) pair.
    fields = {
        name: AttributeValue(value=pair[0], confidence=pair[1], evidence="test")
        for name, pair in attribute_kwargs.items()
        if name not in {"vibe_keywords", "image_quality"}
    }
    return EnrichedListing(
        shortcode=code, permalink=f"https://www.instagram.com/p/{code}/",
        image_ref=f"https://www.instagram.com/p/{code}/",
        attributes=VisualAttributes(
            **fields,
            vibe_keywords=attribute_kwargs.get("vibe_keywords", []),
            image_quality=attribute_kwargs.get("image_quality", "clear"),
        ),
        provider="test", model_version="test-v1",
        prompt_version=contract.PROMPT_VERSION,
        extracted_at=datetime(2026, 9, 20, tzinfo=UTC),
    )


def date_night_event(**overrides) -> EventContext:
    base = dict(
        name="Generic Date Night", event_types=["date_night", "cocktail"],
        event_date=date(2026, 9, 26),
        length_preference=LengthPreference(mini=1.0, midi=0.65, maxi=0.2, unknown=0.5),
        price_rule=PriceRule(max_rental_price_cents=10000,
                             applies_to_lengths=["mini", "midi"], is_hard_filter=True),
    )
    base.update(overrides)
    return EventContext(**base)


# --- Contract ----------------------------------------------------------------

def test_prompt_forbids_describing_the_photograph():
    """The domain gap is the project's central risk; the prompt must address it."""
    text = contract.SYSTEM_PROMPT.lower()
    assert "garment only" in text
    for word in ("lighting", "background", "mirror", "pose"):
        assert word in text


def test_prompt_permits_null():
    assert "null is a correct" in contract.SYSTEM_PROMPT.lower()


def test_every_vocabulary_field_appears_in_the_prompt():
    prompt = contract.build_user_prompt()
    for field in contract.FIELD_VOCABULARIES:
        assert field in prompt


def test_contract_fingerprint_changes_with_the_vocabulary(monkeypatch):
    before = contract.contract_fingerprint()
    patched = dict(contract.FIELD_VOCABULARIES)
    patched["neckline"] = [*patched["neckline"], "invented_neckline"]
    monkeypatch.setattr(contract, "FIELD_VOCABULARIES", patched)
    assert contract.contract_fingerprint() != before


def test_messages_are_provider_neutral():
    messages = contract.build_messages("path/to/image.jpg")
    assert messages[0]["role"] == "system"
    assert any(part.get("image_ref") == "path/to/image.jpg"
               for part in messages[1]["content"])


# --- Normalization -----------------------------------------------------------

def test_normalize_accepts_a_clean_response():
    attributes, errors = normalize.normalize({
        "neckline": {"value": "plunge_v", "confidence": 0.9, "evidence": "deep v"},
        "hem_length": {"value": "mini", "confidence": 0.8, "evidence": "above knee"},
    })
    assert errors == []
    assert attributes.neckline.value == "plunge_v"
    assert attributes.neckline.confidence == 0.9


@pytest.mark.parametrize("raw,expected", [
    ("V-Neck", "v_neck"), ("deep v", "plunge_v"), ("A-Line", "a_line"),
    ("Fit and Flare", "fit_and_flare"), ("off the shoulder", "off_shoulder"),
    ("navy", "blue"), ("burgundy", "red"), ("ivory", "white"),
])
def test_normalize_repairs_formatting_variants(raw, expected):
    field = "neckline" if expected in contract.FIELD_VOCABULARIES["neckline"] else (
        "silhouette" if expected in contract.FIELD_VOCABULARIES["silhouette"] else "colour_family"
    )
    value, bad = normalize.coerce_to_vocabulary(field, raw)
    assert value == expected
    assert bad is None


def test_normalize_records_out_of_vocabulary_values():
    attributes, errors = normalize.normalize(
        {"neckline": {"value": "spiral_galaxy_neckline", "confidence": 0.9}}
    )
    assert attributes.neckline.value is None
    assert "neckline" in attributes.unrecognized_values
    assert any("outside the vocabulary" in e for e in errors)


def test_normalize_accepts_percentage_confidence():
    attributes, _ = normalize.normalize({"neckline": {"value": "cowl", "confidence": 85}})
    assert attributes.neckline.confidence == pytest.approx(0.85)


def test_normalize_accepts_a_bare_scalar_with_zero_confidence():
    """Wrapper dropped. Value kept, but untrusted, so the merge floor refuses it."""
    attributes, _ = normalize.normalize({"neckline": "cowl"})
    assert attributes.neckline.value == "cowl"
    assert attributes.neckline.confidence == 0.0
    assert not attributes.neckline.at_least(DEFAULT_MIN_CONFIDENCE)


def test_normalize_survives_prose_wrapped_json():
    raw = (
        'Here is what I see:\n```json\n'
        '{"neckline": {"value": "halter", "confidence": 0.8}}\n'
        '```\nHope that helps!'
    )
    attributes, errors = normalize.normalize(raw)
    assert attributes.neckline.value == "halter"


def test_normalize_never_raises_on_garbage():
    for garbage in ["not json at all", "", None, 42, [], {"neckline": ["a", "list"]}]:
        attributes, errors = normalize.normalize(garbage)  # type: ignore[arg-type]
        assert isinstance(attributes, VisualAttributes)


def test_normalize_filters_vibe_keywords_to_the_vocabulary():
    attributes, errors = normalize.normalize(
        {"vibe_keywords": ["bright", "not_a_real_vibe", "festive"]}
    )
    assert attributes.vibe_keywords == ["bright", "festive"]
    assert any("not_a_real_vibe" in e for e in errors)


def test_normalize_flags_unusable_image_that_still_reports_attributes():
    attributes, errors = normalize.normalize({
        "image_quality": "unusable",
        "neckline": {"value": "cowl", "confidence": 0.9},
    })
    assert any("unusable" in e for e in errors)


def test_normalize_treats_null_like_strings_as_null():
    for token in ["null", "none", "N/A", "unknown", "unclear", ""]:
        value, bad = normalize.coerce_to_vocabulary("neckline", token)
        assert value is None and bad is None


# --- Providers ---------------------------------------------------------------

def test_stub_is_deterministic():
    provider = StubVisionProvider()
    assert provider.extract("image_a.jpg") == provider.extract("image_a.jpg")


def test_stub_differs_across_images():
    provider = StubVisionProvider()
    assert provider.extract("image_a.jpg") != provider.extract("image_b.jpg")


def test_stub_output_survives_normalization():
    provider = StubVisionProvider(realism="messy")
    for i in range(20):
        attributes, _ = normalize.normalize(provider.extract(f"img_{i}.jpg"))
        assert isinstance(attributes, VisualAttributes)


def test_hostile_stub_still_normalizes_without_crashing():
    provider = StubVisionProvider(realism="hostile")
    for i in range(20):
        attributes, errors = normalize.normalize(provider.extract(f"hostile_{i}.jpg"))
        assert isinstance(attributes, VisualAttributes)


def test_stub_satisfies_the_provider_protocol():
    assert isinstance(StubVisionProvider(), VisionProvider)
    assert isinstance(FailingVisionProvider(), VisionProvider)


def test_live_provider_refuses_without_a_key(monkeypatch):
    monkeypatch.delenv("MODEL_PROVIDER_API_KEY", raising=False)
    provider = LiveVisionProvider(model_version="x")
    assert provider.is_configured() is False
    with pytest.raises(ProviderError, match="not set"):
        provider.extract("image.jpg")


def test_live_provider_call_api_is_an_explicit_stub(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER_API_KEY", "fake-key-for-test")
    provider = LiveVisionProvider(model_version="x", max_retries=1)
    with pytest.raises(ProviderError):
        provider.extract("image.jpg")


def test_provider_factory():
    assert isinstance(get_provider("stub"), StubVisionProvider)
    with pytest.raises(ValueError):
        get_provider("nonexistent")


# --- Orchestration -----------------------------------------------------------

def test_enrich_one_returns_a_valid_record():
    listing, cached = enrich_one("ABC", "https://example.com/p/ABC/", "img.jpg",
                                 StubVisionProvider())
    assert listing.shortcode == "ABC"
    assert listing.prompt_version == contract.PROMPT_VERSION
    assert cached is False


def test_provider_failure_yields_a_record_not_an_exception():
    listing, _ = enrich_one("ABC", "https://example.com/p/ABC/", "img.jpg",
                            FailingVisionProvider())
    assert listing.errors
    assert listing.attributes.image_quality == "unusable"
    assert listing.attributes.coverage == 0.0


def test_one_failure_does_not_sink_the_batch():
    items = [{"shortcode": f"C{i}", "permalink": f"https://example.com/p/C{i}/",
              "image_ref": f"img_{i}.jpg"} for i in range(4)]
    results, run = enrich_batch(items, FailingVisionProvider())
    assert len(results) == 4
    assert run.failed == 4
    assert run.requested == 4


def test_cache_prevents_a_second_provider_call(tmp_path):
    cache = EnrichmentCache(tmp_path / "cache")
    provider = StubVisionProvider()

    _, first = enrich_one("A", "https://example.com/p/A/", "img.jpg", provider, cache)
    _, second = enrich_one("A", "https://example.com/p/A/", "img.jpg", provider, cache)

    assert first is False
    assert second is True
    assert len(cache) == 1


def test_prompt_change_invalidates_the_cache(tmp_path, monkeypatch):
    cache = EnrichmentCache(tmp_path / "cache")
    provider = StubVisionProvider()
    enrich_one("A", "https://example.com/p/A/", "img.jpg", provider, cache)

    monkeypatch.setattr(contract, "PROMPT_VERSION", "dress-visual-v2")
    _, cached = enrich_one("A", "https://example.com/p/A/", "img.jpg", provider, cache)
    assert cached is False


def test_corrupt_cache_entry_is_a_miss(tmp_path):
    cache = EnrichmentCache(tmp_path / "cache")
    provider = StubVisionProvider()
    cache.path_for("img.jpg", provider.model_version).write_text("{ broken", encoding="utf-8")
    assert cache.get("img.jpg", provider.model_version) is None


def test_field_coverage_is_reported():
    items = [{"shortcode": f"C{i}", "permalink": f"https://example.com/p/C{i}/",
              "image_ref": f"img_{i}.jpg"} for i in range(10)]
    results, run = enrich_batch(items, StubVisionProvider(realism="clean", null_rate=0.0))
    coverage = compute_field_coverage(results)
    assert coverage["neckline"] == 1.0
    assert run.mean_coverage > 0.9


def test_write_and_read_round_trip(tmp_path):
    items = [{"shortcode": "A", "permalink": "https://example.com/p/A/",
              "image_ref": "img.jpg"}]
    results, _ = enrich_batch(items, StubVisionProvider())
    path = tmp_path / "out.jsonl"
    write_enriched(path, results)
    loaded = read_enriched(path)
    assert loaded["A"].shortcode == "A"


# --- Merge -------------------------------------------------------------------

def test_merge_without_enrichment_is_a_pass_through():
    caption = attr.extract_all("bright red mini dress size 4 $45")
    merged = merge_attributes(caption, None)
    assert merged.has_visual is False
    assert merged.get("colour_family") == "red"
    assert merged.source_of("colour_family") == "caption"


def test_image_fills_a_gap_the_caption_left():
    caption = attr.extract_all("cute dress size 4 $45")
    assert caption["neckline"] is None
    enriched = make_enriched("X", neckline=("plunge_v", 0.9))
    merged = merge_attributes(caption, enriched)
    assert merged.get("neckline") == "plunge_v"
    assert merged.source_of("neckline") == "image"


def test_low_confidence_image_value_does_not_fill_a_gap():
    caption = attr.extract_all("cute dress size 4 $45")
    enriched = make_enriched("X", neckline=("plunge_v", 0.2))
    merged = merge_attributes(caption, enriched)
    assert merged.get("neckline") is None


def test_agreement_is_recorded_as_both_sources():
    caption = attr.extract_all("red mini dress")
    enriched = make_enriched("X", colour_family=("red", 0.95))
    merged = merge_attributes(caption, enriched)
    assert merged.source_of("colour_family") == "caption_and_image"
    assert merged.conflicts == []


def test_confident_image_overrides_the_caption_and_records_the_conflict():
    caption = attr.extract_all("midi dress size 4")
    assert caption["length"] == "midi"
    enriched = make_enriched("X", hem_length=("mini", 0.9))
    merged = merge_attributes(caption, enriched)
    assert merged.get("length") == "mini"
    assert len(merged.conflicts) == 1
    assert merged.conflicts[0].caption_value == "midi"
    assert merged.conflicts[0].image_value == "mini"


def test_unconfident_image_loses_to_the_caption_but_conflict_is_still_logged():
    caption = attr.extract_all("midi dress size 4")
    enriched = make_enriched("X", hem_length=("mini", 0.3))
    merged = merge_attributes(caption, enriched)
    assert merged.get("length") == "midi"
    assert len(merged.conflicts) == 1
    assert "kept caption value" in merged.conflicts[0].resolution


def test_unusable_image_contributes_nothing():
    caption = attr.extract_all("cute dress size 4")
    enriched = make_enriched("X", neckline=("plunge_v", 0.99), image_quality="unusable")
    merged = merge_attributes(caption, enriched)
    assert merged.get("neckline") is None
    assert merged.has_visual is True


def test_hem_vocabulary_maps_onto_caption_lengths():
    caption = attr.extract_all("dress size 4")
    for hem, expected in [("floor", "maxi"), ("above_knee", "mini"), ("knee", "midi")]:
        merged = merge_attributes(caption, make_enriched("X", hem_length=(hem, 0.9)))
        assert merged.get("length") == expected


def test_embellishment_maps_onto_the_sparkle_flag():
    caption = attr.extract_all("dress size 4")
    merged = merge_attributes(caption, make_enriched("X", embellishment=("sequin", 0.9)))
    assert merged.get("sparkle") == "yes"
    none_merged = merge_attributes(caption, make_enriched("X", embellishment=("none", 0.9)))
    assert none_merged.get("sparkle") is None


def test_image_only_fields_are_added():
    caption = attr.extract_all("dress size 4")
    enriched = make_enriched("X", brightness=("light", 0.9), fit_impression=("flowing", 0.8))
    merged = merge_attributes(caption, enriched)
    assert merged.get("brightness") == "light"
    assert merged.source_of("brightness") == "image"


def test_merge_report_counts_gap_fills_and_conflicts():
    captions = {"A": attr.extract_all("dress size 4"),
                "B": attr.extract_all("midi dress size 4")}
    enriched = {"A": make_enriched("A", neckline=("cowl", 0.9)),
                "B": make_enriched("B", hem_length=("mini", 0.9))}
    merged = merge_many(captions, enriched)
    report = merge_report(merged)
    assert report["with_visual"] == 2
    assert report["total_conflicts"] == 1
    assert report["mean_attributes_after"] >= report["mean_attributes_before"]


# --- Ranking integration -----------------------------------------------------

def test_seed_set_loads_and_has_eight_items():
    seed = load_seed_set(SEED_PATH)
    assert len(seed.items) == 8
    assert len({i.shortcode for i in seed.items}) == 8


def test_seed_set_is_flagged_synthetic_until_real_permalinks_arrive():
    assert load_seed_set(SEED_PATH).is_synthetic is True


def test_seed_set_contains_contrast_cases():
    """Positives alone cannot show the system respects construction preferences."""
    seed = load_seed_set(SEED_PATH)
    assert sum(1 for i in seed.items if not i.is_positive) >= 2


def test_seed_converts_to_enrichment_items():
    seed = load_seed_set(SEED_PATH)
    items = to_enrichment_items(seed)
    assert len(items) == 8
    assert all({"shortcode", "permalink", "image_ref"} <= set(i) for i in items)


def test_seed_image_root_produces_local_paths():
    seed = load_seed_set(SEED_PATH)
    items = to_enrichment_items(seed, image_root=Path("data/local/seed_images"))
    assert all(i["image_ref"].endswith(".jpg") for i in items)


def test_synthetic_fixture_covers_the_whole_seed_set():
    seed = load_seed_set(SEED_PATH)
    enriched = read_enriched(FIXTURE)
    assert set(enriched) == {i.shortcode for i in seed.items}


def test_scoring_against_a_synthetic_seed_is_never_reported_as_measured():
    seed = load_seed_set(SEED_PATH)
    report = score_against_seed(seed, read_enriched(FIXTURE))
    assert report.is_measured is False
    assert any("SYNTHETIC" in c for c in report.caveats)


def test_fixture_agrees_with_its_own_labels_where_it_reports_a_value():
    """The fixture is built from the labels, so agreement is high by construction.
    This is a wiring check on the scorer, not evidence about any model."""
    seed = load_seed_set(SEED_PATH)
    report = score_against_seed(seed, read_enriched(FIXTURE))
    assert report.items_scored == 8
    assert report.overall_agreement > 0.85


def test_edge_case_item_degrades_rather_than_fabricating():
    """The hard image must come back mostly empty, not confidently filled."""
    enriched = read_enriched(FIXTURE)["SEEDEDGE01"]
    assert enriched.attributes.image_quality == "poor"
    assert enriched.attributes.coverage < 0.2
    assert enriched.attributes.uncertainty_notes


def test_low_confidence_seed_item_cannot_override_a_caption():
    """SEEDMYTIE03 is scaled below the merge floor on purpose."""
    enriched = read_enriched(FIXTURE)["SEEDMYTIE03"]
    caption = attr.extract_all("midi dress size 4")
    merged = merge_attributes(caption, enriched)
    assert merged.get("length") == "midi"


def test_seed_fixture_records_a_synthetic_provider():
    for listing in read_enriched(FIXTURE).values():
        assert listing.provider == "synthetic_fixture"
