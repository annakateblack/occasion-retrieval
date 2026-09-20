"""Personal fit and style profile, as structured data.

Nothing here is hardcoded into ranking logic. The ranker reads an instance of
`PersonalFitProfile` and has no knowledge of any particular person's sizes or
preferences, which is what makes the ranker itself public-safe and what will let
a fitted or learned profile replace a hand-written one later without touching
the scorer.

Preferences are expressed as properties of GARMENTS: how a cut hangs, whether a
bodice holds its shape without structured support, how a neckline is shaped.
They are never expressed as statements about a body.

PUBLIC-SAFE. Field definitions only. A real profile is personal data and lives
in data/local/profile.json, which is gitignored. config/profile.example.json
holds a fictional one.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FitBand = Literal["ideal", "workable", "unlikely"]
"""How well a labelled size is expected to work.

'unlikely' is the only band that can drive a hard exclusion, and only when the
garment offers no other size that fits better.
"""


class SizeRule(BaseModel):
    """Which labelled sizes fall in which band, for one garment category."""

    ideal_numeric: list[int] = Field(default_factory=list)
    workable_numeric: list[int] = Field(default_factory=list)
    unlikely_numeric: list[int] = Field(default_factory=list)

    ideal_alpha: list[str] = Field(default_factory=list)
    workable_alpha: list[str] = Field(default_factory=list)
    unlikely_alpha: list[str] = Field(default_factory=list)

    def band_for_numeric(self, size: int) -> FitBand | None:
        if size in self.ideal_numeric:
            return "ideal"
        if size in self.workable_numeric:
            return "workable"
        if size in self.unlikely_numeric:
            return "unlikely"
        return None

    def band_for_alpha(self, size: str) -> FitBand | None:
        token = size.strip().upper()
        if token in [s.upper() for s in self.ideal_alpha]:
            return "ideal"
        if token in [s.upper() for s in self.workable_alpha]:
            return "workable"
        if token in [s.upper() for s in self.unlikely_alpha]:
            return "unlikely"
        return None


class StylePreferences(BaseModel):
    """Garment-construction preferences, scored when deterministically observable.

    Each list holds tokens from the controlled neckline/silhouette vocabulary in
    retrieval/attributes.py. A garment matching nothing in any list scores
    neutral, never negative: absence of evidence is not evidence of a bad match.
    """

    preferred_necklines: list[str] = Field(default_factory=list)
    lower_preference_necklines: list[str] = Field(default_factory=list)

    preferred_silhouettes: list[str] = Field(default_factory=list)
    lower_preference_silhouettes: list[str] = Field(default_factory=list)

    construction_notes: list[str] = Field(
        default_factory=list,
        description="Free-text garment-construction considerations, for display only",
    )


class PersonalFitProfile(BaseModel):
    """One person's garment fit and style profile."""

    profile_id: str = "default"
    version: int = 1

    dress_sizes: SizeRule
    top_sizes: SizeRule | None = None
    bottom_sizes: SizeRule | None = None

    height_inches_min: int | None = None
    height_inches_max: int | None = None
    height_note: str | None = Field(
        default=None,
        description=(
            "Displayed alongside length information. Height is NOT scored in V1: "
            "nothing in the caption data supports a defensible height-to-hem rule."
        ),
    )

    style: StylePreferences = Field(default_factory=StylePreferences)

    def size_band(self, numeric: list[int], alpha: list[str]) -> tuple[FitBand | None, str | None]:
        """Best band across every size a listing offers, with the evidence.

        A listing offering sizes 2 and 4 is an 'ideal' match, not an 'unlikely'
        one: the presence of a workable size is what matters, so the best band
        wins rather than the worst.
        """
        best: FitBand | None = None
        evidence: str | None = None
        order: dict[FitBand, int] = {"ideal": 3, "workable": 2, "unlikely": 1}

        for size in numeric:
            band = self.dress_sizes.band_for_numeric(size)
            if band and (best is None or order[band] > order[best]):
                best, evidence = band, str(size)
        for size in alpha:
            band = self.dress_sizes.band_for_alpha(size)
            if band and (best is None or order[band] > order[best]):
                best, evidence = band, size.upper()
        return best, evidence
