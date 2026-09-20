"""Normalized ingestion contract.

This module defines the ONLY types that cross the boundary between a source and
the rest of the system. Every adapter in `rental_retrieval.sources` produces
these; nothing downstream knows where the data came from. See ADR 001.

These are type definitions only. No logic, no I/O, no parsing. Adapters are
implemented in Phase 1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

GarmentCategory = Literal["dress", "top", "bottom", "shoes", "accessory", "home", "other"]

ListingSourceType = Literal[
    "ig_saved_export",  # from the user's own Instagram data export
    "ig_oembed",        # resolved from a single public post permalink
    "manual",           # hand-entered CSV
    "form",             # submitted by a rental account operator
    "synthetic",        # generated fixture; never counted in reported metrics
]


class RawTasteItem(BaseModel):
    """One saved product from the user's taste corpus.

    Produced by the taste adapter from a local snapshot file. The adapter does
    no network I/O; snapshot production is a separate, manual, gitignored step.
    """

    source_item_id: str = Field(description="Stable platform id; the upsert key")
    image_url: str
    image_sha256: str = Field(description="Prefer a source-provided hash over rehashing")

    product_url: str | None = None
    title_raw: str | None = None
    brand_raw: str | None = None
    price_cents: int | None = Field(default=None, description="Sparse in practice, ~45%")
    price_currency: str | None = None

    source_collection_id: str | None = None
    saved_at: datetime | None = Field(default=None, description="Drives recency weighting")

    # Set by the Phase 1 triage step, not by the adapter. The corpus contains a
    # large volume of non-apparel saves; an unfiltered taste model would weight
    # furniture. See ADR 003.
    garment_category: GarmentCategory | None = None

    snapshot_taken_at: datetime
    raw_payload: dict = Field(description="Full source record, so re-normalization never re-fetches")


class RawTasteCollection(BaseModel):
    """A user-authored grouping of saved items.

    Collection names are the closest thing this project has to supervision:
    they are occasion labels the user wrote themselves.
    """

    source_collection_id: str
    name_raw: str
    name_normalized: str
    occasion_label: str | None = None
    is_apparel: bool | None = None
    snapshot_taken_at: datetime


class RawDressListing(BaseModel):
    """One rental dress listing, from any source.

    V1 is dresses only. Non-dress listings are dropped at the adapter boundary
    rather than filtered later, so the catalog stays semantically coherent.
    """

    account_handle: str
    source_type: ListingSourceType
    source_ref: str | None = Field(default=None, description="Post permalink or submission id")

    # True when this listing came from the user's own saved collection, which is
    # positively biased by construction. The evaluation harness reports on this
    # subset separately and labels it. See docs/DATA_HANDLING.md.
    is_dev_sample: bool = False

    image_url: str
    image_sha256: str
    raw_caption: str | None = None

    brand_raw: str | None = None
    size_label: str | None = Field(default=None, description="Verbatim; never cleaned in place")
    price_cents: int | None = None

    snapshot_taken_at: datetime | None = None
    raw_payload: dict | None = None


class RawRentalAccount(BaseModel):
    """A student-run rental account.

    `handle` is third-party personal data. It is never committed to the public
    repository, including in fixtures. See docs/DATA_HANDLING.md.
    """

    handle: str
    display_name: str | None = None
    school: str = "UGA"
    contact_method: str | None = None
    consent_status: Literal["unknown", "opted_in", "opted_out"] = "unknown"
