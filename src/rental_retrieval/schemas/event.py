"""Event context: what the person is dressing for.

An event accepts BOTH a natural-language vibe string and structured tags. The
vibe string is parsed deterministically by a keyword lexicon (see
retrieval/vibe.py), never by a model. Parsed tags are merged with any tags given
explicitly, and the record keeps both so a ranking can always be traced back to
the words that produced it.

PUBLIC-SAFE. Definitions and vocabularies only.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

EventType = Literal["date_night", "cocktail", "formal", "parents_weekend"]

DressLength = Literal["mini", "midi", "maxi", "unknown"]

Season = Literal["spring", "summer", "fall", "winter"]

VIBE_TAGS = [
    # palette / mood
    "bright", "dark", "neutral", "jewel_tone", "metallic", "sparkle", "pastel",
    # temperature of styling
    "warm_weather", "cold_weather",
    # occasion flavour
    "holiday", "festive", "elevated", "playful", "classic", "romantic", "preppy",
    # construction bonuses an event can reward
    "long_sleeve_bonus", "sleeveless_bonus",
    # specific colour families an event may favour
    "red", "green", "white", "black", "gold", "silver",
]
"""Controlled vibe vocabulary.

Closed on purpose. An open tag set makes scoring untestable, because any string
could appear and nothing guarantees a garment attribute can ever match it.
"""


class LengthPreference(BaseModel):
    """Relative desirability of each dress length for this event.

    Values are multipliers in [0, 1]. A length set to 0 is strongly discouraged
    but still eligible; excluding a length entirely is a hard filter's job, not
    a preference weight's.
    """

    mini: float = 0.5
    midi: float = 0.5
    maxi: float = 0.5
    unknown: float = 0.5
    """Unknown deliberately sits at the neutral midpoint. 63% of the real
    inventory has no stated length, and scoring those at 0 would rank on caption
    verbosity rather than on suitability."""

    def weight_for(self, length: DressLength) -> float:
        return getattr(self, length, 0.5)


class PriceRule(BaseModel):
    """A price ceiling, optionally scoped to particular lengths.

    The scoping exists because the real rule is not "nothing over $100", it is
    "a mini should not cost over $100 for a normal date night, but a formal gown
    reasonably can."
    """

    max_rental_price_cents: int | None = None
    applies_to_lengths: list[DressLength] = Field(
        default_factory=list,
        description="Empty means the ceiling applies to every length.",
    )
    is_hard_filter: bool = True

    def applies_to(self, length: DressLength) -> bool:
        if self.max_rental_price_cents is None:
            return False
        if not self.applies_to_lengths:
            return True
        return length in self.applies_to_lengths


class EventContext(BaseModel):
    """Everything the ranker knows about one occasion."""

    name: str
    event_types: list[EventType]
    event_date: date | None = None
    season: Season | None = Field(
        default=None, description="Derived from event_date when not given"
    )

    # Two input paths, both retained.
    vibe_text: str | None = None
    vibe_tags: list[str] = Field(default_factory=list, description="Explicitly supplied tags")
    resolved_vibe_tags: list[str] = Field(
        default_factory=list, description="Explicit tags plus tags parsed from vibe_text"
    )
    vibe_parse_evidence: dict[str, list[str]] = Field(
        default_factory=dict,
        description="tag -> the words in vibe_text that produced it",
    )

    length_preference: LengthPreference = Field(default_factory=LengthPreference)
    price_rule: PriceRule = Field(default_factory=PriceRule)

    exclude_unavailable_on_date: bool = True
    allow_unknown_availability: bool = True
    """Unknown availability stays eligible and is flagged 'confirm with renter'.
    Only positive evidence of unavailability excludes."""

    discouraged_tags: list[str] = Field(
        default_factory=list,
        description="Tags that reduce the vibe score when present, e.g. cold_weather in September",
    )

    def resolved_season(self) -> Season | None:
        if self.season:
            return self.season
        if not self.event_date:
            return None
        month = self.event_date.month
        if month in (3, 4, 5):
            return "spring"
        if month in (6, 7, 8):
            return "summer"
        if month in (9, 10, 11):
            return "fall"
        return "winter"
