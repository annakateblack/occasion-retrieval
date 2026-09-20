"""Normalized record for one post taken from a Meta (Instagram) data export.

WHY THIS IS A SEPARATE TYPE FROM `RawDressListing`
--------------------------------------------------
ADR 001 defines `RawDressListing` as the contract every catalog source must
produce. A Meta export cannot satisfy it: the export contains **no image URLs
at all**. `RawDressListing` requires `image_url` and `image_sha256`, because a
listing with no picture is useless to a visual retrieval system.

So this adapter stops one step short. It produces `InstagramSavedPost`, which is
everything the export genuinely contains, and marks each record with whether it
is eligible to become a listing later. Promotion to `RawDressListing` happens in
a separate, still-unbuilt step once images are resolved from permalinks.

Flagging this rather than quietly widening `RawDressListing` keeps ADR 001
honest: the contract still means "a listing we can actually rank."

PUBLIC-SAFE. This file contains no personal data, only field definitions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# --- Controlled vocabularies -------------------------------------------------

Classification = Literal[
    "rental_listing",           # direct evidence this post rents a specific garment
    "rental_source_candidate",  # account behaves like a rental account; this post is
                                # plausibly inventory but lacks direct evidence
    "other_shop",               # ordinary shopping/inspiration content
    "uncertain",                # signals present but insufficient; never silently dropped
]

GarmentType = Literal["dress", "set", "top", "skirt", "jumpsuit", "other", "unknown"]

PostType = Literal["post", "reel", "igtv", "unknown"]


class SizeCandidate(BaseModel):
    """One size reading, with the exact text it came from.

    A caption may legitimately offer several sizes. We never collapse them into
    one value and never guess. `raw_text` is the literal substring matched, so
    every normalized value is traceable to its input.
    """

    raw_text: str
    numeric: int | None = None      # US numeric, e.g. 4
    alpha: str | None = None        # 'XXS'|'XS'|'S'|'M'|'L'|'XL'|'XXL'
    pattern: str = Field(description="Name of the extraction rule that fired")


class PriceCandidate(BaseModel):
    """One money amount found in a caption.

    `kind` is only set when the surrounding text makes it unambiguous. A bare
    '$45' with no rental or retail context stays `kind='unknown'` rather than
    being assumed to be the rental price.
    """

    raw_text: str
    amount_cents: int
    kind: Literal["rental", "retail", "deposit", "unknown"] = "unknown"
    pattern: str


class InstagramSavedPost(BaseModel):
    """One saved Instagram post, normalized from a Meta export.

    Every normalized field sits next to the raw text it derives from, per the
    project's raw-plus-normalized convention. Where deterministic extraction is
    not confident, the normalized field is None and the raw caption is retained.
    """

    # --- provenance ---------------------------------------------------------
    source: Literal["ig_saved_collection", "ig_saved_posts"] = "ig_saved_collection"
    source_collection: str | None = Field(
        default=None, description="Collection name the post was found in"
    )
    shortcode: str = Field(description="Instagram shortcode; the join + dedup key")
    permalink: str
    post_type: PostType = "unknown"

    # Collection entries carry no per-post timestamp. This is filled only when
    # the post is also present in saved_posts.json, which does carry one.
    saved_at: datetime | None = None
    saved_at_source: Literal["saved_posts_join", "none"] = "none"

    # --- owner --------------------------------------------------------------
    owner_username: str | None = None
    owner_name: str | None = None
    owner_url: str | None = None

    # --- raw content --------------------------------------------------------
    raw_caption: str | None = None
    caption_was_mojibake: bool = Field(
        default=False,
        description="True if the caption needed latin-1 -> utf-8 repair on load",
    )

    # --- classification -----------------------------------------------------
    classification: Classification
    classification_signals: list[str] = Field(
        default_factory=list, description="Names of every signal that fired, for audit"
    )
    classification_score: int = Field(
        default=0, description="Sum of fired signal weights; the threshold input"
    )
    account_is_rental_source: bool = Field(
        default=False, description="Set by the account-level second pass"
    )

    # --- deterministic extraction (all optional, never invented) ------------
    garment_type: GarmentType = "unknown"
    garment_type_evidence: str | None = None

    raw_size_text: str | None = None
    size_candidates: list[SizeCandidate] = Field(default_factory=list)

    raw_price_text: str | None = None
    price_candidates: list[PriceCandidate] = Field(default_factory=list)
    rental_price_cents: int | None = Field(
        default=None, description="Set only when exactly one amount is unambiguously rental"
    )
    retail_price_cents: int | None = None

    brand: str | None = Field(default=None, description="Null unless deterministically certain")
    item_name: str | None = Field(default=None, description="Null unless deterministically certain")

    availability_text: str | None = None
    contact_text: str | None = None
    requires_dryclean: bool | None = None

    # --- diagnostics --------------------------------------------------------
    parse_warnings: list[str] = Field(default_factory=list)

    @property
    def is_dress_candidate(self) -> bool:
        """Downstream inventory is dresses-only. Non-dress records are kept and
        classified, never destroyed, and excluded at this boundary instead."""
        return (
            self.classification in ("rental_listing", "rental_source_candidate")
            and self.garment_type == "dress"
        )
