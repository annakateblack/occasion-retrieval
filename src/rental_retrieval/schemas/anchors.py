"""Human relevance labels for real listings: the anchor set.

WHY THIS IS NOT THE SEED SET
----------------------------
The project now has two hand-labeled sets, and conflating them would make both
useless. They grade different components:

| | `SeedSet` (config/seed/, public) | `AnchorSet` (data/local/eval/, private) |
|---|---|---|
| Labels | expected ATTRIBUTES per item | graded RELEVANCE per (item, event) |
| Answers | "does the extractor see a deep V?" | "is this dress right for My Tie?" |
| Grades | the VLM extractor | the whole ranking pipeline |
| Contents | invented placeholders | real permalinks and real judgements |
| Committed | yes | never |

An extractor can be perfect and the ranker still wrong, and the reverse. Keeping
the label sets separate keeps the two failures distinguishable.

GRADES
------
`strong` and `plausible` mirror the labeller's own vocabulary rather than
imposing new words on it. They map onto the 0/1/2 scale the relevance_labels
table already uses, so nDCG over these needs no translation later.

`negative_associations` exists because one real label was a statement about what
must NOT be inferred: sparkle should not imply Christmas. That is an assertion
about the model's reasoning, not about a dress, and there was nowhere to put it.
Anti-rules are the labels most worth keeping, because they are the ones a system
silently violates.

PUBLIC-SAFE. This file defines shapes only. Real labels live in a gitignored
file; `config/seed/anchor_labels.template.json` shows the shape with invented
permalinks.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Grade = Literal["strong", "plausible", "weak", "not_relevant"]

GRADE_TO_SCORE: dict[Grade, int] = {
    "strong": 2,
    "plausible": 1,
    "weak": 0,
    "not_relevant": 0,
}
"""Maps the labeller's words onto the graded scale used by relevance_labels.

'weak' and 'not_relevant' both score 0 but are kept distinct: 'weak' means
considered and rejected, 'not_relevant' means out of category. The difference
matters for error analysis even though nDCG cannot see it.
"""


class EventRelevance(BaseModel):
    """One item's relevance to one event context."""

    event_key: str = Field(
        description="Canonical event key, e.g. 'my_tie' or 'christmas_cocktail'. "
                    "Matches a file in config/events/ where one exists."
    )
    grade: Grade
    note: str | None = None

    @property
    def score(self) -> int:
        return GRADE_TO_SCORE[self.grade]


class AnchorLabel(BaseModel):
    """One real listing with the labeller's judgements attached."""

    shortcode: str
    permalink: str

    relevance: list[EventRelevance] = Field(default_factory=list)

    visual_notes: str | None = Field(
        default=None,
        description="What the labeller saw, in their own words. Not a vocabulary token.",
    )
    fit_notes: str | None = Field(
        default=None,
        description="Caveats about size or fit that qualify the relevance grades",
    )
    label_confidence: Literal["high", "medium", "low"] = Field(
        default="high",
        description="Lower when the labeller flagged their own uncertainty, "
                    "so a disagreement here is discounted rather than treated as an error",
    )

    negative_associations: list[str] = Field(
        default_factory=list,
        description="Inferences this item must NOT trigger, e.g. "
                    "'sparkle_implies_holiday'. Each is a testable anti-rule.",
    )

    tags: list[str] = Field(default_factory=list, description="Free-form labeller tags")

    @field_validator("shortcode")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("shortcode must not be blank")
        return v

    def grade_for(self, event_key: str) -> Grade | None:
        for entry in self.relevance:
            if entry.event_key == event_key:
                return entry.grade
        return None

    def score_for(self, event_key: str) -> int:
        """Unlabelled means 0. The labeller saw the item and did not endorse it
        for this event, so absence is a weak negative rather than missing data."""
        grade = self.grade_for(event_key)
        return GRADE_TO_SCORE[grade] if grade else 0


class AnchorSet(BaseModel):
    """A set of real, human-labeled listings used to evaluate the pipeline."""

    name: str
    description: str
    created_at: str
    is_real: bool = Field(
        description="True only for genuine permalinks and genuine human labels. "
                    "Gates every measurement claim."
    )
    labeller: str | None = None

    relevance_scale_max: int = Field(
        default=2,
        ge=1,
        description=(
            "Top of the graded scale, matching DECISIONS.md: 2=strong, 1=plausible, "
            "0=weak/not_relevant. Recorded on the file rather than assumed by the "
            "reader, so a future 0..3 set cannot be silently scored against a 0..2 "
            "ideal DCG. Existing labels are NOT compressed: this project's real "
            "labels are already strong/plausible-oriented."
        ),
    )

    items: list[AnchorLabel]

    def by_shortcode(self) -> dict[str, AnchorLabel]:
        return {item.shortcode: item for item in self.items}

    def event_keys(self) -> list[str]:
        keys: list[str] = []
        for item in self.items:
            for entry in item.relevance:
                if entry.event_key not in keys:
                    keys.append(entry.event_key)
        return sorted(keys)

    def graded_labels(self, event_key: str) -> dict[str, int]:
        """{shortcode: 0|1|2} for one event. The input nDCG needs."""
        return {item.shortcode: item.score_for(event_key) for item in self.items}

    def validate_scale(self) -> list[str]:
        """Check that no label exceeds the declared scale maximum.

        Returns problems rather than raising: a mismatch is a data question for
        the labeller, not a crash. Nothing here rescales anything.
        """
        problems: list[str] = []
        for item in self.items:
            for entry in item.relevance:
                if entry.score > self.relevance_scale_max:
                    problems.append(
                        f"{item.shortcode}/{entry.event_key}: grade "
                        f"'{entry.grade}' scores {entry.score}, above the declared "
                        f"relevance_scale_max={self.relevance_scale_max}"
                    )
        return problems

    def positives_for(self, event_key: str, min_score: int = 1) -> list[str]:
        return [i.shortcode for i in self.items if i.score_for(event_key) >= min_score]

    def all_negative_associations(self) -> dict[str, list[str]]:
        return {i.shortcode: i.negative_associations
                for i in self.items if i.negative_associations}


class MeasurementGate(BaseModel):
    """Whether a number from this pipeline may be called a measurement.

    Both conditions must hold. Real labels scored against stub extractions are
    not evidence, and a real model scored against invented labels is not either.
    Encoding the rule stops it from depending on whoever reads the output
    remembering which inputs were synthetic.
    """

    labels_are_real: bool
    provider_is_live: bool
    provider_name: str
    label_source: str

    @property
    def is_measured(self) -> bool:
        return self.labels_are_real and self.provider_is_live

    def caveats(self) -> list[str]:
        out: list[str] = []
        if not self.labels_are_real:
            out.append(
                f"LABELS ARE SYNTHETIC ({self.label_source}): placeholder permalinks and "
                "invented labels. Numbers verify the harness runs; they are not evidence."
            )
        if not self.provider_is_live:
            out.append(
                f"PROVIDER IS NOT LIVE ({self.provider_name}): attributes come from a stub "
                "that cannot see the images. Agreement here is a random-chance floor."
            )
        return out
