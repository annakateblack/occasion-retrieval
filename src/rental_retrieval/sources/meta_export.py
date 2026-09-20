"""Generic reader for Meta's data-export format.

Meta exports everything in one shape, regardless of what the data is:

    [
      {
        "timestamp": 1699999999,
        "media": [],                     <- usually empty; NOT where the data is
        "fbid": "...",
        "label_values": [
          {"label": "URL", "value": "...", "href": "..."},
          {"label": null, "title": "Owner", "dict": [ ...nested blocks... ]},
          {"label": null, "title": "Media", "dict": [ ...nested records... ]}
        ]
      }
    ]

Two traps live in this format, and both were found by inspecting real files:

1. **The payload hides in `label_values`, not in `media`.** A saved collection
   has `"media": []` at the top level while its 221 posts sit in a nested block
   whose `title` is `"Media"`. Reading the top-level array returns nothing and
   looks like an empty collection.

2. **Text is mojibake.** Meta writes UTF-8 bytes reinterpreted as Latin-1, so
   "•" arrives as "â\\x80¢". In the real export this affected 18% of Shop
   captions. Rental captions use bullets and dashes as field separators, so any
   size/price regex run before repair silently fails on those records.

PUBLIC-SAFE. Generic format handling only, no project-specific logic and no
personal data.
"""

from __future__ import annotations

from typing import Any

# The export writes nested sections as an entry with label=None and a title.
NESTED_TITLE_KEY = "title"
NESTED_DICT_KEY = "dict"


def repair_mojibake(text: str) -> tuple[str, bool]:
    """Undo Meta's UTF-8-as-Latin-1 corruption.

    Returns (repaired_text, was_repaired).

    The round trip is lossless when it succeeds. Text that is already clean
    either fails to encode as Latin-1 (raising, which we catch) or round-trips
    to itself, so applying this to clean text is safe.
    """
    if not text:
        return text, False
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text, False
    return repaired, repaired != text


def iter_label_values(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a record's label_values list, tolerating malformed records."""
    lv = record.get("label_values")
    return lv if isinstance(lv, list) else []


def get_labeled(record: dict[str, Any], label: str) -> list[dict[str, Any]]:
    """Every top-level entry in `record` carrying this label.

    Returns a list because the export genuinely duplicates entries: 10 of 328
    saved_posts records carried the same Caption twice, byte-identical.
    """
    return [e for e in iter_label_values(record) if isinstance(e, dict) and e.get("label") == label]


def get_labeled_value(record: dict[str, Any], label: str, *, repair: bool = True) -> tuple[str | None, bool]:
    """First non-empty value for `label`. Returns (value, was_repaired)."""
    for entry in get_labeled(record, label):
        raw = entry.get("value")
        if isinstance(raw, str) and raw.strip():
            if repair:
                fixed, changed = repair_mojibake(raw)
                return fixed.strip(), changed
            return raw.strip(), False
    return None, False


def get_labeled_href(record: dict[str, Any], label: str) -> str | None:
    """First non-empty href for `label`. Hrefs are URLs and are never repaired."""
    for entry in get_labeled(record, label):
        href = entry.get("href")
        if isinstance(href, str) and href.strip():
            return href.strip()
    return None


def get_section(record: dict[str, Any], title: str) -> list[dict[str, Any]]:
    """Return the nested block list for a section title such as 'Media' or 'Owner'.

    This is the function that avoids trap #1. Always reach for a section by
    title; never read the top-level `media` key.
    """
    for entry in iter_label_values(record):
        if not isinstance(entry, dict):
            continue
        if entry.get("label") is None and entry.get(NESTED_TITLE_KEY) == title:
            nested = entry.get(NESTED_DICT_KEY)
            return nested if isinstance(nested, list) else []
    return []


def flatten_section_fields(section: list[dict[str, Any]]) -> dict[str, str]:
    """Collapse a nested section into {label: value}.

    Sections wrap their fields one level deeper than looks necessary: the
    'Owner' section is a list containing one block, and that block's own `dict`
    holds the Username/Name/URL entries. This walks both shapes.
    """
    out: dict[str, str] = {}
    for block in section:
        if not isinstance(block, dict):
            continue
        inner = block.get(NESTED_DICT_KEY)
        entries = inner if isinstance(inner, list) else [block]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            label, value = entry.get("label"), entry.get("value")
            if label and isinstance(value, str) and value.strip():
                fixed, _ = repair_mojibake(value)
                out.setdefault(label, fixed.strip())
    return out


def as_media_record(entry: dict[str, Any]) -> dict[str, Any]:
    """Adapt one nested 'Media' entry so the `get_*` helpers work on it.

    A nested media entry stores its fields under `dict` where a top-level
    record uses `label_values`. Normalizing here means one set of accessors
    handles both a collection's posts and the standalone saved_posts records.
    """
    return {
        "timestamp": entry.get("timestamp"),
        "label_values": entry.get(NESTED_DICT_KEY) or [],
    }
