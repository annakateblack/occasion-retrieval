"""Coverage report for an ingestion run.

Two outputs, deliberately separated:

  build_private_report()  counts PLUS the per-account breakdown. Contains real
                          usernames, so it is written only under data/local/.

  build_public_summary()  the same metric names with all values replaced by
                          zeros, as a methodology template. Safe to commit, so
                          the repo can show WHAT is measured without showing
                          anyone's data.

The point of the report is to stop "the parser seems to work" from being the
standard of evidence. Every number here is a thing that can regress.

PUBLIC-SAFE module. The private data appears only in the value it returns, never
in the source.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from rental_retrieval.schemas.instagram import InstagramSavedPost

METRIC_KEYS = [
    "total_records",
    "unique_permalinks",
    "duplicate_shortcodes",
    "dropped_no_permalink",
    "rental_listing",
    "rental_source_candidate",
    "other_shop",
    "uncertain",
    "dress_count_in_rental",
    "dress_inventory_final",
    "pct_with_owner_username",
    "pct_with_caption",
    "pct_caption_mojibake_repaired",
    "pct_with_usable_size",
    "pct_with_usable_rental_price",
    "pct_with_saved_at",
    "records_with_parse_warnings",
    "distinct_accounts",
    "rental_source_accounts",
]


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def build_private_report(posts: list[InstagramSavedPost],
                         diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Full report including per-account counts. PRIVATE: write to data/local/ only."""
    n = len(posts)
    classes = Counter(p.classification for p in posts)
    rental = [p for p in posts if p.classification in ("rental_listing", "rental_source_candidate")]
    dresses = [p for p in rental if p.garment_type == "dress"]

    accounts = Counter(p.owner_username for p in posts if p.owner_username)
    rental_accounts = Counter(
        p.owner_username for p in rental if p.owner_username
    )

    metrics = {
        "total_records": n,
        "unique_permalinks": len({p.shortcode for p in posts}),
        "duplicate_shortcodes": diagnostics.get("duplicate_shortcodes", 0),
        "dropped_no_permalink": diagnostics.get("dropped_no_permalink", 0),
        "rental_listing": classes.get("rental_listing", 0),
        "rental_source_candidate": classes.get("rental_source_candidate", 0),
        "other_shop": classes.get("other_shop", 0),
        "uncertain": classes.get("uncertain", 0),
        "dress_count_in_rental": len(dresses),
        "dress_inventory_final": len([p for p in posts if p.is_dress_candidate]),
        "pct_with_owner_username": _pct(sum(1 for p in posts if p.owner_username), n),
        "pct_with_caption": _pct(sum(1 for p in posts if p.raw_caption), n),
        "pct_caption_mojibake_repaired": _pct(sum(1 for p in posts if p.caption_was_mojibake), n),
        "pct_with_usable_size": _pct(sum(1 for p in posts if p.size_candidates), n),
        "pct_with_usable_rental_price": _pct(
            sum(1 for p in posts if p.rental_price_cents is not None), n
        ),
        "pct_with_saved_at": _pct(sum(1 for p in posts if p.saved_at), n),
        "records_with_parse_warnings": sum(1 for p in posts if p.parse_warnings),
        "distinct_accounts": len(accounts),
        "rental_source_accounts": len({p.owner_username for p in posts
                                       if p.account_is_rental_source and p.owner_username}),
    }

    return {
        "metrics": metrics,
        "diagnostics": diagnostics,
        "garment_type_distribution": dict(Counter(p.garment_type for p in posts).most_common()),
        "post_type_distribution": dict(Counter(p.post_type for p in posts).most_common()),
        "signal_frequency": dict(
            Counter(s for p in posts for s in p.classification_signals).most_common()
        ),
        "parse_warning_frequency": dict(
            Counter(w.split(":")[0] for p in posts for w in p.parse_warnings).most_common()
        ),
        # PRIVATE: real usernames below this line.
        "account_post_counts": dict(accounts.most_common()),
        "account_rental_counts": dict(rental_accounts.most_common()),
    }


def build_public_summary() -> dict[str, Any]:
    """Zeroed template of the same metrics. Safe to commit."""
    return {
        "note": "Methodology template. All values zeroed; real runs write to data/local/.",
        "metrics": {key: 0 for key in METRIC_KEYS},
        "garment_type_distribution": {
            k: 0 for k in ["dress", "set", "top", "skirt", "jumpsuit", "other", "unknown"]
        },
        "signal_frequency": {},
        "parse_warning_frequency": {},
    }


def format_report_text(report: dict[str, Any], *, include_accounts: bool = False) -> str:
    """Render a report for terminal reading.

    `include_accounts` defaults to False so that printing a report to a shared
    terminal, a log, or a screen share does not leak handles by accident. The
    caller must opt in.
    """
    lines: list[str] = []
    m = report["metrics"]

    lines.append("INGESTION COVERAGE REPORT")
    lines.append("=" * 52)
    lines.append("")
    lines.append("Volume")
    lines.append(f"  raw media entries        {report['diagnostics'].get('raw_media_entries', '?')}")
    lines.append(f"  normalized records       {m['total_records']}")
    lines.append(f"  unique permalinks        {m['unique_permalinks']}")
    lines.append(f"  duplicates removed       {m['duplicate_shortcodes']}")
    lines.append(f"  dropped (no permalink)   {m['dropped_no_permalink']}")
    lines.append("")
    lines.append("Classification")
    for key in ["rental_listing", "rental_source_candidate", "uncertain", "other_shop"]:
        share = _pct(m[key], m["total_records"])
        lines.append(f"  {key:26} {m[key]:5}  ({share:5.1f}%)")
    lines.append("")
    lines.append("Dresses-only boundary")
    lines.append(f"  dresses among rental      {m['dress_count_in_rental']}")
    lines.append(f"  final dress inventory     {m['dress_inventory_final']}")
    lines.append("")
    lines.append("Field coverage (% of records)")
    for key in ["pct_with_owner_username", "pct_with_caption",
                "pct_caption_mojibake_repaired", "pct_with_usable_size",
                "pct_with_usable_rental_price", "pct_with_saved_at"]:
        lines.append(f"  {key:32} {m[key]:5.1f}%")
    lines.append("")
    lines.append("Accounts")
    lines.append(f"  distinct accounts         {m['distinct_accounts']}")
    lines.append(f"  rental-source accounts    {m['rental_source_accounts']}")
    lines.append("")
    lines.append("Signals fired (frequency)")
    for name, count in report["signal_frequency"].items():
        lines.append(f"  {name:26} {count}")
    lines.append("")
    lines.append("Parse warnings")
    lines.append(f"  records with warnings     {m['records_with_parse_warnings']}")
    for name, count in report["parse_warning_frequency"].items():
        lines.append(f"  {name:34} {count}")

    if include_accounts:
        lines.append("")
        lines.append("PRIVATE: per-account post counts")
        for user, count in report["account_post_counts"].items():
            lines.append(f"  {user:30} {count}")

    return "\n".join(lines)
