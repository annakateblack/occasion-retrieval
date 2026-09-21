"""Provider-neutral ranking metrics for caption and visual experiment arms."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from rental_retrieval.schemas.ranking import RankingRun


class QueryJudgments(BaseModel):
    """Graded relevance labels for one event query.

    The current real anchor set is explicitly 0..2. The generic metric keeps
    support for a separately declared future 0..3 set; it never rescales or
    rewrites existing labels.
    """

    query_id: str
    labels: dict[str, int]
    relevance_scale_max: Literal[2, 3] | None = Field(
        default=None,
        description=(
            "Declared maximum grade. The current measured anchor set declares 2; "
            "alternate scales require a different, explicitly declared label set."
        ),
    )
    is_measured: bool = Field(
        default=False,
        description="False for synthetic/stub labels; true only for private human labels",
    )

    @model_validator(mode="after")
    def validate_scale(self) -> QueryJudgments:
        if any(grade < 0 for grade in self.labels.values()):
            raise ValueError("Relevance grades must be non-negative")
        if self.relevance_scale_max is not None and any(
            grade > self.relevance_scale_max for grade in self.labels.values()
        ):
            raise ValueError("Relevance grade exceeds declared scale maximum")
        return self


class CutoffTieDiagnostics(BaseModel):
    """Tie-group evidence at a reporting cutoff.

    ``tie_count`` is the number visible in the retained rows. It is a lower
    bound until a retained row below the cutoff has a different score.
    """

    cutoff_score: float | None
    tie_count: int
    row_below_cutoff_retained: bool
    tie_group_complete: bool


class ArmMetrics(BaseModel):
    query_id: str
    ndcg_at_k: float
    attribute_coverage: dict[str, float]
    style_score_evaluability: float
    hard_filter_survivors: int
    boundary_tie_count: int
    cutoff_was_observable: bool
    boundary_tie_count_is_complete: bool


class ArmComparisonMetrics(BaseModel):
    """Provider-neutral metrics for one controlled caption/enriched pair."""

    query_id: str
    caption_only: ArmMetrics
    enriched: ArmMetrics
    top_k_overlap: float
    hard_filter_survivor_delta: int


def dcg(relevances: Sequence[int], k: int = 10) -> float:
    """Discounted cumulative gain using exponential graded relevance."""
    if k <= 0:
        raise ValueError("k must be positive")
    return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(relevances[:k]))


def ndcg_at_k(ranked_ids: Sequence[str], labels: Mapping[str, int], k: int = 10) -> float:
    """nDCG@k; unjudged results are treated as non-relevant."""
    if any(grade < 0 for grade in labels.values()):
        raise ValueError("Relevance grades must be non-negative")
    actual = [labels.get(item_id, 0) for item_id in ranked_ids[:k]]
    ideal = sorted(labels.values(), reverse=True)[:k]
    ideal_dcg = dcg(ideal, k)
    return 0.0 if ideal_dcg == 0 else dcg(actual, k) / ideal_dcg


def top_k_overlap(left: Sequence[str], right: Sequence[str], k: int = 10) -> float:
    """Share of the smaller available top-k list appearing in both arms."""
    left_set, right_set = set(left[:k]), set(right[:k])
    denominator = min(k, len(left_set), len(right_set))
    return 0.0 if denominator == 0 else len(left_set & right_set) / denominator


def field_coverage(attribute_rows: Iterable[Mapping[str, str | None]],
                   fields: Sequence[str]) -> dict[str, float]:
    rows = list(attribute_rows)
    if not rows:
        return {field: 0.0 for field in fields}
    return {
        field: sum(row.get(field) not in (None, "unknown") for row in rows) / len(rows)
        for field in fields
    }


def boundary_ties(scores: Sequence[float], k: int = 10) -> tuple[int, bool]:
    """Return ties at rank k and whether rows below k were available.

    Evaluation runs should request more than ``k`` results.  With exactly k
    rows, the count is only a lower bound because a tied item may have been
    truncated by the ranker.
    """
    diagnostic = cutoff_tie_diagnostics(scores, k)
    return diagnostic.tie_count, diagnostic.row_below_cutoff_retained


def cutoff_tie_diagnostics(scores: Sequence[float], k: int = 10) -> CutoffTieDiagnostics:
    """Return a conservative tie diagnostic for rank ``k``.

    Scores must be in ranking order. Retaining rank ``k + 1`` makes the cutoff
    observable, but the count is complete only once the retained rows cross the
    end of the tie group.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    if len(scores) < k:
        return CutoffTieDiagnostics(
            cutoff_score=None,
            tie_count=0,
            row_below_cutoff_retained=False,
            tie_group_complete=True,
        )

    cutoff = scores[k - 1]
    tied = sum(math.isclose(score, cutoff, rel_tol=0.0, abs_tol=1e-12) for score in scores)
    row_below = len(scores) > k
    crossed_tie_boundary = row_below and not math.isclose(
        scores[-1], cutoff, rel_tol=0.0, abs_tol=1e-12
    )
    return CutoffTieDiagnostics(
        cutoff_score=cutoff,
        tie_count=tied,
        row_below_cutoff_retained=row_below,
        tie_group_complete=crossed_tie_boundary,
    )


def evaluate_run(run: RankingRun, judgments: QueryJudgments,
                 fields: Sequence[str], k: int = 10) -> ArmMetrics:
    reported_rows = run.results[:k]
    ids = [row.shortcode for row in reported_rows]
    tie = cutoff_tie_diagnostics([row.overall_score for row in run.results], k)
    return ArmMetrics(
        query_id=judgments.query_id,
        ndcg_at_k=ndcg_at_k(ids, judgments.labels, k),
        attribute_coverage=field_coverage((row.attributes for row in reported_rows), fields),
        style_score_evaluability=(
            sum(row.style_score is not None for row in reported_rows) / len(reported_rows)
            if reported_rows else 0.0
        ),
        hard_filter_survivors=run.passed_hard_filters,
        boundary_tie_count=tie.tie_count,
        cutoff_was_observable=tie.row_below_cutoff_retained,
        boundary_tie_count_is_complete=tie.tie_group_complete,
    )


def evaluate_arm_pair(
    caption_only: RankingRun,
    enriched: RankingRun,
    judgments: QueryJudgments,
    fields: Sequence[str],
    k: int = 10,
) -> ArmComparisonMetrics:
    """Evaluate two already-controlled runs without importing provider code."""
    if caption_only.total_candidates != enriched.total_candidates:
        raise ValueError("Evaluation arms must use the same candidate count")
    if caption_only.weights != enriched.weights:
        raise ValueError("Evaluation arms must use identical ranking weights")

    caption_metrics = evaluate_run(caption_only, judgments, fields, k)
    enriched_metrics = evaluate_run(enriched, judgments, fields, k)
    return ArmComparisonMetrics(
        query_id=judgments.query_id,
        caption_only=caption_metrics,
        enriched=enriched_metrics,
        top_k_overlap=top_k_overlap(
            [row.shortcode for row in caption_only.results],
            [row.shortcode for row in enriched.results],
            k,
        ),
        hard_filter_survivor_delta=(
            enriched.passed_hard_filters - caption_only.passed_hard_filters
        ),
    )
