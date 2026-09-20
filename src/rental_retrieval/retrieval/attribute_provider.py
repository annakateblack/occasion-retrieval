"""Stable boundary between attribute extraction and deterministic ranking."""

from __future__ import annotations

from typing import Protocol

from rental_retrieval.retrieval import attributes as caption_attributes
from rental_retrieval.schemas.enrichment import AttributeObservation, AttributeResolution
from rental_retrieval.schemas.instagram import InstagramSavedPost

RANKING_ATTRIBUTE_KEYS = frozenset(
    {
        "length",
        "colour_family",
        "sleeve",
        "neckline",
        "silhouette",
        "sparkle",
        "fabric",
        "availability",
    }
)


class AttributeProvider(Protocol):
    """Callable implemented by caption-only, stub, cached, or live providers."""

    def __call__(self, post: InstagramSavedPost) -> AttributeResolution: ...


def caption_resolution(post: InstagramSavedPost) -> AttributeResolution:
    """Return the current deterministic caption arm with field provenance."""
    extracted = caption_attributes.extract_all(post.raw_caption)
    values = {key: value for key, value in extracted.items() if not key.endswith("_evidence")}
    observations: dict[str, list[AttributeObservation]] = {}

    for key, value in values.items():
        # ``unknown`` is a sentinel, not positive evidence for a visual fact.
        if value is None or value == "unknown":
            continue
        evidence_key = "colour_evidence" if key == "colour_family" else f"{key}_evidence"
        observations[key] = [
            AttributeObservation(
                value=value,
                source="caption",
                confidence=1.0,
                evidence=extracted.get(evidence_key),
                provider="deterministic_caption_rules",
            )
        ]

    return AttributeResolution(values=values, observations=observations)


def validate_ranking_contract(resolution: AttributeResolution) -> None:
    """Fail fast when a provider omits a field expected by the fixed ranker."""
    missing = sorted(RANKING_ATTRIBUTE_KEYS - resolution.values.keys())
    if missing:
        raise ValueError("Attribute provider omitted required fields: " + ", ".join(missing))

