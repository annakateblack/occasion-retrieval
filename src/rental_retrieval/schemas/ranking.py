"""Output contract for a baseline ranking run.

Two design choices worth stating, because both are load-bearing:

**`None` means unknown, not zero.** Every score component is `float | None`. A
component that could not be evaluated returns None and is excluded from BOTH the
numerator and the denominator of the weighted average. Scoring unknowns as zero
would rank listings by how much their renter wrote, not by how well they fit.

**Confidence is reported, never hidden.** `coverage` is the share of total
scoring weight that was actually evaluable. A listing with a two-word caption
can still rank highly on the signals it does have, and the reader can see that
the score rests on thin evidence.

PUBLIC-SAFE. Definitions only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AvailabilityStatus = Literal["available", "unavailable", "unknown"]

FilterOutcome = Literal["pass", "fail", "not_applicable"]


class HardFilterResult(BaseModel):
    """One hard filter's verdict on one listing, with its reason."""

    name: str
    outcome: FilterOutcome
    reason: str
    evidence: str | None = None


class ScoreComponent(BaseModel):
    """One soft signal's contribution.

    `score` is None when the caption did not let us evaluate the signal.

    `neutral_fallback` distinguishes two kinds of unknown, which is a real
    distinction and not a technicality:

    - **Unobservable attribute** (neckline, colour, fabric). The garment may not
      have the feature at all, and there is no sensible default. fallback=None,
      so the component leaves the average entirely.

    - **Unread transactional fact** (price, size, availability). Every rental
      dress has a price and a size; we simply could not read them. fallback=0.5,
      so the component stays in the average at a neutral value.

    Without this split, not stating a price RAISES a listing's score, because
    the price component drops out and the remaining components average higher.
    That inversion showed up immediately on real data: the top five results were
    all listings with no price.

    `coverage` still counts only genuinely observed components, so a
    neutral-filled score never masquerades as evidence.
    """

    name: str
    score: float | None = Field(description="0..1, or None when not evaluable")
    weight: float
    reason: str
    evidence: str | None = None
    neutral_fallback: float | None = Field(
        default=None,
        description="Value used in the average when score is None; None means drop it",
    )

    @property
    def is_known(self) -> bool:
        """True only when the signal was actually observed."""
        return self.score is not None

    @property
    def effective_score(self) -> float | None:
        """The value the average should use, or None to be excluded."""
        return self.score if self.score is not None else self.neutral_fallback

    @property
    def counts_toward_average(self) -> bool:
        return self.effective_score is not None

    @property
    def weighted(self) -> float:
        value = self.effective_score
        return 0.0 if value is None else value * self.weight


class RankedDress(BaseModel):
    """One result row."""

    rank: int

    # Identity and provenance
    permalink: str
    shortcode: str
    owner_username: str | None
    raw_caption: str | None

    # Listing facts
    rental_price_cents: int | None
    garment_type: str
    dress_length: str
    size_summary: str | None = Field(default=None, description="Human-readable size/fit note")
    size_band: str | None = None
    availability_status: AvailabilityStatus
    availability_note: str | None = None

    # Attributes the caption actually supported, unknowns preserved
    attributes: dict[str, str | None] = Field(default_factory=dict)
    attribute_provenance: dict[str, list[dict]] = Field(default_factory=dict)
    attribute_conflicts: list[str] = Field(default_factory=list)

    # Filters and scores
    hard_filters: list[HardFilterResult] = Field(default_factory=list)
    passed_hard_filters: bool

    fit_score: float | None = None
    style_score: float | None = None
    event_score: float | None = None
    seasonality_score: float | None = None
    price_score: float | None = None
    availability_score: float | None = None

    components: list[ScoreComponent] = Field(default_factory=list)
    overall_score: float
    coverage: float = Field(description="Share of scoring weight that was evaluable, 0..1")

    explanation: str
    flags: list[str] = Field(default_factory=list)


class RankingRun(BaseModel):
    """A complete run, including everything that was filtered out and why.

    Rejected counts are part of the output rather than a log line, because
    "how many survived each filter" is the diagnostic that tells you whether a
    thin result set is a ranking problem or an inventory problem.
    """

    event_name: str
    profile_id: str
    weights: dict[str, float]

    total_candidates: int
    passed_hard_filters: int
    returned: int

    rejected_by_filter: dict[str, int] = Field(default_factory=dict)
    results: list[RankedDress] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
