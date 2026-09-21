"""Direct binding to the Work branch contracts. No heuristics, no detection.

Canonical source of truth, `prep-vlm-evaluation`:

    src/rental_retrieval/retrieval/attribute_provider.py
        class AttributeProvider(Protocol):
            def __call__(self, post: InstagramSavedPost) -> AttributeResolution: ...
        def caption_resolution(post) -> AttributeResolution

    src/rental_retrieval/schemas/enrichment.py
        class AttributeObservation(BaseModel):
            value, source, confidence, evidence, provider, model_version
        class AttributeResolution(BaseModel):
            values: dict[str, str | None]
            observations: dict[str, list[AttributeObservation]]
            conflicts: list[str]

This module imports those symbols and nothing else. When they are absent (this
package standing alone), the import fails loudly at point of use rather than
degrading to a guess. That is a deliberate change from the previous integration
pass, which probed for container names: with the contracts known, probing is
strictly worse than binding.

PUBLIC-SAFE.
"""

from __future__ import annotations

from typing import Any

REQUIRED_KEYS: tuple[str, ...] = (
    "length",
    "colour_family",
    "sleeve",
    "neckline",
    "silhouette",
    "sparkle",
    "fabric",
    "availability",
)
"""Every key Work's `AttributeResolution.values` carries.

The adapter must return a resolution containing all eight. Seven are visual and
fillable; `availability` is required but never written by vision.
"""

VISION_FILLABLE_KEYS: tuple[str, ...] = (
    "length",
    "colour_family",
    "sleeve",
    "neckline",
    "silhouette",
    "sparkle",
    "fabric",
)
"""The seven Work keys vision may fill. `availability` is excluded by rule."""

UNKNOWN_VALUES: frozenset[str | None] = frozenset({None, "unknown"})
"""What counts as an unfilled slot in `AttributeResolution.values`."""


def is_unknown(value: str | None) -> bool:
    """True when a Work resolution slot is unfilled."""
    return value in UNKNOWN_VALUES


class WorkContractsUnavailable(ImportError):
    """Work's modules are not importable. Raised at point of use, never swallowed."""


def load_work_contracts() -> tuple[Any, Any, Any]:
    """Return (caption_resolution, AttributeResolution, AttributeObservation).

    Raises `WorkContractsUnavailable` with the exact expected paths when the
    branch is not present. There is no fallback: a silently-substituted
    resolution type would produce a run that looks Work-native and is not.
    """
    try:
        from rental_retrieval.retrieval.attribute_provider import (  # noqa: PLC0415
            caption_resolution,
        )
        from rental_retrieval.schemas.enrichment import (  # noqa: PLC0415
            AttributeObservation,
            AttributeResolution,
        )
    except ImportError as exc:
        raise WorkContractsUnavailable(
            "Could not import the Work contracts. Expected:\n"
            "  src/rental_retrieval/retrieval/attribute_provider.py"
            " -> caption_resolution\n"
            "  src/rental_retrieval/schemas/enrichment.py"
            " -> AttributeResolution, AttributeObservation\n"
            "Copy this package's enrichment/ modules onto "
            "prep-vlm-evaluation and run there."
        ) from exc

    return caption_resolution, AttributeResolution, AttributeObservation
