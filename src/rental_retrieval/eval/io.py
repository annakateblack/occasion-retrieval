"""Privacy-gated loading for real evaluation labels."""

from __future__ import annotations

import json
from pathlib import Path

from rental_retrieval.eval.metrics import QueryJudgments


def private_eval_root(repo_root: Path) -> Path:
    return (repo_root / "data" / "local" / "eval").resolve()


def load_private_judgments(path: Path, *, repo_root: Path) -> QueryJudgments:
    """Load labels only from the gitignored private evaluation directory."""
    resolved = path.resolve()
    allowed = private_eval_root(repo_root)
    if not resolved.is_relative_to(allowed):
        raise ValueError(f"Private evaluation labels must stay under {allowed}")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    judgments = QueryJudgments.model_validate(payload)
    if not judgments.is_measured:
        raise ValueError("Private human labels must set is_measured=true")
    if judgments.relevance_scale_max is None:
        raise ValueError("Private human labels must declare relevance_scale_max as 2 or 3")
    return judgments
