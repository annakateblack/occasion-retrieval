"""Adapter: Meta data export -> normalized InstagramSavedPost records.

Per ADR 001 this is a **pure function from local files to records**. It performs
no network I/O, holds no credentials, and never touches Instagram. It reads only
files the user exported themselves.

Pipeline:

    saved_collections.json
        └─ find collection by name        (case-insensitive, whitespace-tolerant)
            └─ read its nested 'Media' section   <- NOT the top-level `media` key
                └─ normalize each entry
                    └─ join saved_posts.json by shortcode, for saved_at only
                        └─ classify (two passes)

Why the join is narrow: measured against the real export, saved_posts.json
filled zero missing captions, zero missing owner names and zero missing owner
URLs for matched records, and produced zero caption conflicts. Its only unique
contribution is a per-post timestamp, which collection entries lack entirely.
So it is a timestamp source, not a second content source, and treating it as
one would pull in unrelated saved history for no gain.

PUBLIC-SAFE. No personal data; paths are supplied by the caller.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from rental_retrieval.schemas.instagram import InstagramSavedPost, PostType
from rental_retrieval.sources import extract
from rental_retrieval.sources.classify import (
    DEFAULT_CONFIG,
    ClassifierConfig,
    classify_all,
    score_caption,
)
from rental_retrieval.sources.meta_export import (
    as_media_record,
    flatten_section_fields,
    get_labeled,
    get_labeled_href,
    get_labeled_value,
    get_section,
)

SHORTCODE_RE = re.compile(r"/(p|reel|reels|tv)/([A-Za-z0-9_-]+)")

_POST_TYPE_MAP: dict[str, PostType] = {
    "p": "post", "reel": "reel", "reels": "reel", "tv": "igtv",
}


class CollectionNotFoundError(LookupError):
    """Raised when the requested collection name is absent from the export."""


# --- Low-level helpers -------------------------------------------------------

def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def parse_permalink(url: str | None) -> tuple[str | None, PostType]:
    """Return (shortcode, post_type). The shortcode is our dedup and join key."""
    if not url:
        return None, "unknown"
    m = SHORTCODE_RE.search(url.split("?")[0])
    if not m:
        return None, "unknown"
    return m.group(2), _POST_TYPE_MAP.get(m.group(1), "unknown")


def collection_name(record: dict[str, Any]) -> str:
    value, _ = get_labeled_value(record, "Name")
    return value or ""


def find_collection(collections: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """Locate a collection by name, tolerant of case and trailing whitespace.

    Real collection names carry trailing spaces and emoji, so exact matching is
    fragile. Falls back to a stripped, case-folded comparison.
    """
    target = name.strip().casefold()
    for record in collections:
        if collection_name(record).strip().casefold() == target:
            return record
    available = sorted({collection_name(r).strip() for r in collections if collection_name(r)})
    raise CollectionNotFoundError(
        f"No collection named {name!r}. Found {len(available)} collections in this export."
    )


def flatten_collection_media(collection: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the collection's posts as records the generic accessors understand.

    This is the step that reads the nested 'Media' section rather than the
    top-level `media` key, which is empty for every collection in a real export.
    """
    return [as_media_record(entry) for entry in get_section(collection, "Media")]


# --- Normalization -----------------------------------------------------------

def normalize_record(record: dict[str, Any], *, source_collection: str | None,
                     config: ClassifierConfig = DEFAULT_CONFIG) -> InstagramSavedPost | None:
    """Turn one export record into a normalized post, or None if unusable.

    A record with no resolvable permalink is dropped, because the shortcode is
    the identity of the post. Everything else that is missing becomes a warning
    on a record that is still kept.
    """
    warnings: list[str] = []

    permalink = get_labeled_href(record, "URL")
    shortcode, post_type = parse_permalink(permalink)
    if not shortcode or not permalink:
        return None

    caption, was_mojibake = get_labeled_value(record, "Caption")
    if not caption:
        warnings.append("missing_caption")

    if len(get_labeled(record, "Caption")) > 1:
        values = {
            (e.get("value") or "")
            for e in get_labeled(record, "Caption")
            if (e.get("value") or "").strip()
        }
        warnings.append(
            "duplicate_caption_entries_identical" if len(values) <= 1
            else "duplicate_caption_entries_conflicting"
        )

    owner = flatten_section_fields(get_section(record, "Owner"))
    owner_username = owner.get("Username")
    if not owner_username:
        warnings.append("missing_owner_username")

    # Deterministic extraction. Each returns None rather than guessing.
    sizes, raw_size_text = extract.extract_sizes(caption or "")
    prices, raw_price_text = extract.extract_prices(caption or "")
    rental_cents, price_warnings = extract.resolve_rental_price(prices)
    retail_cents = extract.resolve_retail_price(prices)
    warnings.extend(price_warnings)

    garment, garment_evidence = extract.extract_garment_type(caption or "")
    if garment == "unknown" and caption:
        warnings.append("garment_type_unresolved")

    score, signals = score_caption(caption, config)

    return InstagramSavedPost(
        source="ig_saved_collection" if source_collection else "ig_saved_posts",
        source_collection=source_collection,
        shortcode=shortcode,
        permalink=permalink.split("?")[0],
        post_type=post_type,
        owner_username=owner_username,
        owner_name=owner.get("Name"),
        owner_url=owner.get("URL"),
        raw_caption=caption,
        caption_was_mojibake=was_mojibake,
        classification="uncertain",     # replaced by classify_all()
        classification_signals=signals,
        classification_score=score,
        garment_type=garment,
        garment_type_evidence=garment_evidence,
        raw_size_text=raw_size_text,
        size_candidates=sizes,
        raw_price_text=raw_price_text,
        price_candidates=prices,
        rental_price_cents=rental_cents,
        retail_price_cents=retail_cents,
        availability_text=extract.extract_availability_text(caption or ""),
        contact_text=extract.extract_contact_text(caption or ""),
        requires_dryclean=extract.extract_dryclean(caption or ""),
        parse_warnings=warnings,
    )


def build_saved_at_index(saved_posts: list[dict[str, Any]]) -> dict[str, int]:
    """Map shortcode -> unix save timestamp, from saved_posts.json.

    Collection entries carry no per-post timestamp; only the collection itself
    has an 'Update time'. This index is the sole reason saved_posts.json is read.
    """
    index: dict[str, int] = {}
    for record in saved_posts:
        shortcode, _ = parse_permalink(get_labeled_href(record, "URL"))
        ts = record.get("timestamp")
        if shortcode and isinstance(ts, int) and ts > 0:
            index.setdefault(shortcode, ts)
    return index


# --- Public entry point ------------------------------------------------------

def load_collection(
    collections_path: Path,
    collection_name_wanted: str,
    saved_posts_path: Path | None = None,
    config: ClassifierConfig = DEFAULT_CONFIG,
) -> tuple[list[InstagramSavedPost], dict[str, Any]]:
    """Load, normalize and classify one named collection.

    Returns (posts, diagnostics). Diagnostics carry only counts, never content,
    so they are safe to print to a terminal or log.
    """
    collections = _load_json(Path(collections_path))
    if not isinstance(collections, list):
        raise ValueError("saved_collections.json should contain a list of collections")

    collection = find_collection(collections, collection_name_wanted)
    raw_entries = flatten_collection_media(collection)

    posts: list[InstagramSavedPost] = []
    dropped_no_permalink = 0
    for entry in raw_entries:
        normalized = normalize_record(entry, source_collection=collection_name_wanted.strip(),
                                      config=config)
        if normalized is None:
            dropped_no_permalink += 1
            continue
        posts.append(normalized)

    # Deduplicate on shortcode. First occurrence wins; later ones are counted.
    seen: dict[str, InstagramSavedPost] = {}
    duplicates = 0
    for p in posts:
        if p.shortcode in seen:
            duplicates += 1
            continue
        seen[p.shortcode] = p
    posts = list(seen.values())

    # Join saved_posts.json for timestamps only.
    matched = 0
    if saved_posts_path is not None and Path(saved_posts_path).exists():
        saved_posts = _load_json(Path(saved_posts_path))
        index = build_saved_at_index(saved_posts if isinstance(saved_posts, list) else [])
        for p in posts:
            ts = index.get(p.shortcode)
            if ts:
                from datetime import datetime, timezone
                p.saved_at = datetime.fromtimestamp(ts, timezone.utc)
                p.saved_at_source = "saved_posts_join"
                matched += 1
            else:
                p.parse_warnings.append("no_saved_at_match_in_saved_posts")

    classify_all(posts, config)

    diagnostics = {
        "collection_requested": collection_name_wanted,
        "raw_media_entries": len(raw_entries),
        "dropped_no_permalink": dropped_no_permalink,
        "duplicate_shortcodes": duplicates,
        "normalized_posts": len(posts),
        "saved_at_matched": matched,
        "saved_at_unmatched": len(posts) - matched if saved_posts_path else None,
    }
    return posts, diagnostics


def filter_dress_inventory(posts: list[InstagramSavedPost]) -> list[InstagramSavedPost]:
    """Apply the dresses-only V1 boundary.

    Nothing is deleted upstream: non-dress records were classified and retained.
    This is the query boundary, applied here so the exclusion is one explicit,
    testable call rather than a filter scattered through the parser.
    """
    return [p for p in posts if p.is_dress_candidate]
