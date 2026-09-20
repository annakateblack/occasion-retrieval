"""Provider-neutral ranking metrics for caption and visual experiment arms."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

from pydantic import BaseModel, Field

from rental_retrieval.schemas.ranking import RankingRun


class QueryJudgments(BaseModel):
    """Graded relevance labels for one event query.

    The metric code accepts any non-negative integer scale, covering the
    currently documented 0..2 and 0..3 alternatives without baking the open
    documentation decision into code.
    """

    query_id: str
    labels: dict[str, int]
    is_measured: bool = Field(
        default=False,
        description="False for synthetic/stub labels; true only for private human labels",
    )


class ArmMetrics(BaseModel):
    query_id: str
    ndcg_at_k: float
    attribute_coverage: dict[str, float]
    style_score_evaluability: float
    hard_filter_survivors: int
    boundary_tie_count: int
    cutoff_was_observable: bool


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
    if k <= 0:
        raise ValueError("k must be positive")
    if len(scores) < k:
        return 0, True
    cutoff = scores[k - 1]
    tied = sum(math.isclose(score, cutoff, rel_tol=0.0, abs_tol=1e-12) for score in scores)
    return tied, len(scores) > k


def evaluate_run(run: RankingRun, judgments: QueryJudgments,
                 fields: Sequence[str], k: int = 10) -> ArmMetrics:
    ids = [row.shortcode for row in run.results]
    tie_count, observable = boundary_ties([row.overall_score for row in run.results], k)
    return ArmMetrics(
        query_id=judgments.query_id,
        ndcg_at_k=ndcg_at_k(ids, judgments.labels, k),
        attribute_coverage=field_coverage((row.attributes for row in run.results), fields),
        style_score_evaluability=(
            sum(row.style_score is not None for row in run.results) / len(run.results)
            if run.results else 0.0
        ),
        hard_filter_survivors=run.passed_hard_filters,
        boundary_tie_count=tie_count,
        cutoff_was_observable=observable,
    )
