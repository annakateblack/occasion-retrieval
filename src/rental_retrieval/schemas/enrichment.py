"""Provider-neutral contracts for garment-attribute enrichment.

The ranker consumes resolved attribute values.  This schema keeps the evidence
used to resolve those values beside them so a model-backed implementation does
not have to discard confidence, provenance, or conflicts at the integration
boundary.

PUBLIC-SAFE. Definitions only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AttributeSource = Literal["caption", "vision"]


class AttributeObservation(BaseModel):
    """One provider's observation for one normalized attribute."""

    value: str | None
    source: AttributeSource
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: str | None = None
    provider: str | None = None
    model_version: str | None = None


class AttributeResolution(BaseModel):
    """Resolved ranker input plus the observations that produced it.

    ``values`` deliberately matches the existing flat caption-attribute shape.
    An enrichment provider may therefore be added without changing any scorer.
    ``observations`` is keyed by the same normalized field names.  Conflicts
    remain visible even when a merge policy selects one value for ranking.
    """

    values: dict[str, str | None]
    observations: dict[str, list[AttributeObservation]] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)

