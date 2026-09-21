"""Tests for the Instagram export adapter.

All test data is SYNTHETIC. No real post, caption, handle or permalink appears
here or in the fixtures. See docs/DATA_HANDLING.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rental_retrieval.sources import extract
from rental_retrieval.sources.classify import DEFAULT_CONFIG, classify_all, score_caption
from rental_retrieval.sources.ig_export import (
    CollectionNotFoundError,
    filter_dress_inventory,
    find_collection,
    flatten_collection_media,
    load_collection,
    normalize_record,
    parse_permalink,
)
from rental_retrieval.sources.meta_export import get_section, repair_mojibake

FIXTURES = Path(__file__).parent / "fixtures"
COLLECTIONS = FIXTURES / "synthetic_saved_collections.json"
SAVED_POSTS = FIXTURES / "synthetic_saved_posts.json"


@pytest.fixture
def loaded():
    posts, diagnostics = load_collection(COLLECTIONS, "Shop", SAVED_POSTS)
    return posts, diagnostics


@pytest.fixture
def by_code(loaded):
    posts, _ = loaded
    return {p.shortcode: p for p in posts}


# --- Locating the collection -------------------------------------------------

def test_finds_shop_collection():
    import json
    collections = json.loads(COLLECTIONS.read_text(encoding="utf-8"))
    assert find_collection(collections, "Shop") is not None


def test_collection_lookup_tolerates_case_and_whitespace():
    import json
    collections = json.loads(COLLECTIONS.read_text(encoding="utf-8"))
    # The fixture stores this name with a trailing space, as real exports do.
    assert find_collection(collections, "recipes") is not None
    assert find_collection(collections, "  SHOP  ") is not None


def test_missing_collection_raises():
    import json
    collections = json.loads(COLLECTIONS.read_text(encoding="utf-8"))
    with pytest.raises(CollectionNotFoundError):
        find_collection(collections, "NoSuchCollection")


def test_reads_nested_media_not_top_level_media():
    """The regression test for the bug that cost us a round trip.

    Top-level `media` is empty; the posts live in the nested 'Media' section.
    """
    import json
    collections = json.loads(COLLECTIONS.read_text(encoding="utf-8"))
    shop = find_collection(collections, "Shop")
    assert shop["media"] == []
    assert len(get_section(shop, "Media")) > 0
    assert len(flatten_collection_media(shop)) > 0


# --- Permalink parsing -------------------------------------------------------

@pytest.mark.parametrize(
    "url,code,kind",
    [
        ("https://www.instagram.com/p/ABC123/", "ABC123", "post"),
        ("https://www.instagram.com/reel/XYZ789/", "XYZ789", "reel"),
        ("https://www.instagram.com/p/ABC123/?utm_source=ig_web", "ABC123", "post"),
        ("https://example.com/not-instagram", None, "unknown"),
        (None, None, "unknown"),
        ("", None, "unknown"),
    ],
)
def test_parse_permalink(url, code, kind):
    assert parse_permalink(url) == (code, kind)


# --- Malformed and duplicate records ----------------------------------------

def test_record_without_permalink_is_dropped(loaded):
    _, diagnostics = loaded
    assert diagnostics["dropped_no_permalink"] == 1


def test_duplicate_shortcode_is_deduplicated(loaded, by_code):
    posts, diagnostics = loaded
    assert diagnostics["duplicate_shortcodes"] == 1
    assert len([p for p in posts if p.shortcode == "SYNTHAAA001"]) == 1


def test_identical_duplicate_captions_are_flagged_not_conflicting(by_code):
    post = by_code["SYNTHAAA006"]
    assert "duplicate_caption_entries_identical" in post.parse_warnings
    assert "duplicate_caption_entries_conflicting" not in post.parse_warnings


def test_missing_caption_is_flagged_and_record_kept(by_code):
    post = by_code["SYNTHAAA005"]
    assert post.raw_caption is None
    assert "missing_caption" in post.parse_warnings


def test_missing_owner_is_flagged_and_record_kept(by_code):
    post = by_code["SYNTHAAA005"]
    assert post.owner_username is None
    assert "missing_owner_username" in post.parse_warnings


# --- Encoding ----------------------------------------------------------------

def test_repair_mojibake_round_trip():
    original = "Size 4 • $45 rental — ✨"
    broken = original.encode("utf-8").decode("latin-1")
    repaired, changed = repair_mojibake(broken)
    assert repaired == original
    assert changed is True


def test_repair_mojibake_leaves_clean_text_alone():
    clean = "Size 4 rental"
    repaired, changed = repair_mojibake(clean)
    assert repaired == clean
    assert changed is False


def test_mojibake_caption_is_repaired_and_flagged(by_code):
    post = by_code["SYNTHAAA001"]
    assert post.caption_was_mojibake is True
    assert "•" in post.raw_caption


# --- Classification ----------------------------------------------------------

def test_clear_rental_listing_is_classified_as_listing(by_code):
    post = by_code["SYNTHAAA001"]
    assert post.classification == "rental_listing"
    assert "rent_explicit_phrase" in post.classification_signals
    assert post.classification_score >= DEFAULT_CONFIG.listing_min_score


def test_ordinary_shopping_post_is_not_a_rental_listing(by_code):
    """A fashion post mentioning a dress must not become inventory."""
    post = by_code["SYNTHAAA003"]
    assert post.classification != "rental_listing"
    assert post.classification in ("other_shop", "uncertain")


def test_thin_post_from_rental_account_becomes_source_candidate(by_code):
    """No rent word, but the account is established as a rental source."""
    post = by_code["SYNTHAAA004"]
    assert post.account_is_rental_source is True
    assert post.classification == "rental_source_candidate"


def test_signals_are_recorded_for_audit(by_code):
    post = by_code["SYNTHAAA002"]
    assert post.classification_signals
    assert all(isinstance(s, str) for s in post.classification_signals)


def test_account_needs_multiple_posts_to_count_as_rental_source():
    from rental_retrieval.schemas.instagram import InstagramSavedPost

    lone = InstagramSavedPost(
        shortcode="X1", permalink="https://www.instagram.com/p/X1/",
        owner_username="one_off_account",
        raw_caption="renting this dress $50, size 4, dry clean",
        classification="uncertain",
    )
    classify_all([lone])
    assert lone.account_is_rental_source is False


# --- Size extraction ---------------------------------------------------------

@pytest.mark.parametrize(
    "caption,expected_numeric",
    [
        ("size 4 available", [4]),
        ("Size: 6", [6]),
        ("sizes 2-4 available", [2, 4]),
        ("size 00 fits small", [0]),
    ],
)
def test_numeric_size_extraction(caption, expected_numeric):
    sizes, raw = extract.extract_sizes(caption)
    assert [s.numeric for s in sizes if s.numeric is not None] == expected_numeric
    assert raw is not None


def test_alpha_size_extraction():
    sizes, _ = extract.extract_sizes("size small, runs true")
    assert any(s.alpha == "S" for s in sizes)


def test_bare_letter_is_not_treated_as_a_size():
    """'s' and 'm' appear constantly in ordinary text; matching them is noise."""
    sizes, raw = extract.extract_sizes("this is a lovely dress and it fits me well")
    assert sizes == []
    assert raw is None


def test_fit_notes_are_not_swallowed_into_size():
    caption = "size 4 but runs small, I am 5'6 and it hit mid calf"
    sizes, raw_size = extract.extract_sizes(caption)
    assert [s.numeric for s in sizes if s.numeric] == [4]
    # The fit note stays in the raw caption; only the matched span is raw_size.
    assert "runs small" not in (raw_size or "")


# --- Price extraction --------------------------------------------------------

def test_simple_rental_price_is_parsed():
    prices, _ = extract.extract_prices("$45 to rent for the weekend")
    cents, warnings = extract.resolve_rental_price(prices)
    assert cents == 4500
    assert warnings == []


def test_rental_and_retail_are_separated():
    prices, _ = extract.extract_prices("rental price $60, retails for $395")
    assert extract.resolve_rental_price(prices)[0] == 6000
    assert extract.resolve_retail_price(prices) == 39500


def test_ambiguous_multiple_amounts_do_not_invent_a_price():
    prices, raw = extract.extract_prices("styling three ways, $40 and $250 mentioned")
    cents, warnings = extract.resolve_rental_price(prices)
    assert cents is None
    assert any(w.startswith("ambiguous_price_count") for w in warnings)
    assert raw is not None          # raw text is preserved for later review


def test_sole_bare_amount_is_used_but_flagged():
    prices, _ = extract.extract_prices("this dress is $55")
    cents, warnings = extract.resolve_rental_price(prices)
    assert cents == 5500
    assert "rental_price_inferred_from_sole_amount" in warnings


def test_trailing_dollar_amount_is_parsed_and_inferred():
    prices, raw = extract.extract_prices(
        "Reformation mini dress! Size 6 50$ + dry cleaning! Available after Sep 24th"
    )
    cents, warnings = extract.resolve_rental_price(prices)
    assert cents == 5000
    assert raw == "50$"
    assert "rental_price_inferred_from_sole_amount" in warnings


def test_no_price_yields_none_without_warning_noise():
    prices, raw = extract.extract_prices("beautiful gown available")
    assert prices == []
    assert raw is None
    assert extract.resolve_rental_price(prices)[0] is None


# --- Garment type ------------------------------------------------------------

@pytest.mark.parametrize(
    "caption,expected",
    [
        ("gorgeous gown for formal", "dress"),
        ("navy two piece set", "set"),
        ("linen skirt", "skirt"),
        ("silk blouse", "top"),
        ("black jumpsuit", "jumpsuit"),
        ("something entirely unrelated", "unknown"),
    ],
)
def test_garment_type_extraction(caption, expected):
    garment, evidence = extract.extract_garment_type(caption)
    assert garment == expected
    if expected != "unknown":
        assert evidence


def test_brand_and_item_name_are_never_invented(by_code):
    """V1 makes no deterministic brand claim. Nulls, not guesses."""
    for post in by_code.values():
        assert post.brand is None
        assert post.item_name is None


# --- Dresses-only boundary ---------------------------------------------------

def test_dress_filter_excludes_non_dress_but_parser_kept_them(loaded):
    posts, _ = loaded
    dresses = filter_dress_inventory(posts)

    # The set post survived parsing and classification...
    set_post = next(p for p in posts if p.shortcode == "SYNTHAAA004")
    assert set_post.garment_type == "set"
    # ...but is excluded from dress inventory.
    assert set_post.shortcode not in {p.shortcode for p in dresses}
    assert all(p.garment_type == "dress" for p in dresses)


def test_other_shop_posts_never_reach_dress_inventory(loaded):
    posts, _ = loaded
    dresses = filter_dress_inventory(posts)
    assert all(
        p.classification in ("rental_listing", "rental_source_candidate") for p in dresses
    )


# --- saved_posts.json join ---------------------------------------------------

def test_saved_at_is_joined_from_saved_posts(by_code):
    post = by_code["SYNTHAAA001"]
    assert post.saved_at is not None
    assert post.saved_at_source == "saved_posts_join"


def test_unmatched_post_has_no_saved_at_and_is_flagged(by_code):
    post = by_code["SYNTHAAA004"]
    assert post.saved_at is None
    assert post.saved_at_source == "none"
    assert "no_saved_at_match_in_saved_posts" in post.parse_warnings


def test_join_does_not_import_posts_outside_the_collection(by_code):
    """saved_posts.json contributes timestamps only, never new records."""
    assert "SYNTHZZZ999" not in by_code


def test_loading_without_saved_posts_still_works():
    posts, diagnostics = load_collection(COLLECTIONS, "Shop", None)
    assert posts
    assert all(p.saved_at is None for p in posts)
    assert diagnostics["saved_at_matched"] == 0


# --- Determinism -------------------------------------------------------------

def test_pipeline_is_deterministic():
    a, _ = load_collection(COLLECTIONS, "Shop", SAVED_POSTS)
    b, _ = load_collection(COLLECTIONS, "Shop", SAVED_POSTS)
    assert [p.model_dump() for p in a] == [p.model_dump() for p in b]


def test_score_caption_handles_none_and_empty():
    assert score_caption(None) == (0, [])
    assert score_caption("") == (0, [])


def test_normalize_record_returns_none_for_junk():
    assert normalize_record({"label_values": []}, source_collection="Shop") is None
    assert normalize_record({}, source_collection="Shop") is None
