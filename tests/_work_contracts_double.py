"""Exact transcription of Work's contracts, for testing this package standalone.

Copied verbatim from `prep-vlm-evaluation`:
    src/rental_retrieval/schemas/enrichment.py

If Work's models change, change this file too. It is a TEST DOUBLE, not an
implementation: nothing in `src/` imports it, and the adapter binds to Work's
real classes at construction. Its only job is to let the fill policy be tested
against the true shape without the branch present.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AttributeObservation(BaseModel):
    value: str | None
    source: Literal["caption", "vision"]
    confidence: float | None
    evidence: str | None
    provider: str | None
    model_version: str | None


class AttributeResolution(BaseModel):
    values: dict[str, str | None]
    observations: dict[str, list[AttributeObservation]]
    conflicts: list[str]


REQUIRED_KEYS = (
    "length", "colour_family", "sleeve", "neckline",
    "silhouette", "sparkle", "fabric", "availability",
)


def make_caption_resolution(**overrides: str | None) -> AttributeResolution:
    """A caption resolution with every required key, unknown unless overridden."""
    values: dict[str, str | None] = {k: None for k in REQUIRED_KEYS}
    values.update(overrides)
    observations: dict[str, list[AttributeObservation]] = {
        key: [AttributeObservation(
            value=value, source="caption", confidence=1.0,
            evidence="caption text", provider=None, model_version=None)]
        for key, value in values.items() if value not in (None, "unknown")
    }
    return AttributeResolution(values=values, observations=observations, conflicts=[])
