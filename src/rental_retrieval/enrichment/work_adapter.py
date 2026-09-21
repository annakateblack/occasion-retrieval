"""THE adapter. Satisfies Work's `AttributeProvider` contract, nothing more.

    class AttributeProvider(Protocol):
        def __call__(self, post: InstagramSavedPost) -> AttributeResolution: ...

`VisionAttributeProvider` is that callable. It:

    1. calls Work's `caption_resolution(post)`
    2. copies values / observations / conflicts
    3. applies the controlled-experiment fill policy
    4. returns a new Work `AttributeResolution`

Work's classes are imported once at construction through
`work_contracts.load_work_contracts()` and injected into the policy, so the
policy module stays importable and unit-testable standing alone while the
adapter is bound to the real types. There is no container detection and no
fallback resolution type: if Work is absent, construction raises
`WorkContractsUnavailable` naming the exact expected paths.

WHAT THE VISION ARM CAN AND CANNOT CHANGE
-----------------------------------------
Both arms call the same `rank(...)` with the same profile, event, weights,
filters and `quality_threshold`. The vision arm differs only in that some
previously-unknown values in `AttributeResolution.values` now carry a value.
No known value is overwritten, `availability` is never written, and a listing
with no enrichment yields a resolution equal to the baseline arm's. Tests assert
all three.

PUBLIC-SAFE. No credentials, no data.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rental_retrieval.enrichment.resolution_policy import (
    DEFAULT_MIN_CONFIDENCE,
    VISION_FIELD_TO_KEY,
    FillOutcome,
    apply_vision,
    outcome_report,
)
from rental_retrieval.enrichment.work_contracts import load_work_contracts
from rental_retrieval.schemas.visual import EnrichedListing


@dataclass
class VisionAttributeProvider:
    """An `AttributeProvider` that fills unknown visual attributes from vision.

    Built by `make_vision_attribute_provider`. Held as a class rather than a
    closure so the run report can read `.outcomes` after the arms have run.
    """

    enriched_by_code: dict[str, EnrichedListing]
    caption_resolution: Callable[[Any], Any]
    resolution_cls: Any
    observation_cls: Any
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    field_map: dict[str, str] = field(
        default_factory=lambda: dict(VISION_FIELD_TO_KEY))

    outcomes: dict[str, FillOutcome] = field(default_factory=dict)

    def __call__(self, post: Any) -> Any:
        base = self.caption_resolution(post)

        shortcode = getattr(post, "shortcode", "") or ""
        enriched = self.enriched_by_code.get(shortcode)

        resolution, outcome = apply_vision(
            base,
            enriched,
            resolution_cls=self.resolution_cls,
            observation_cls=self.observation_cls,
            min_confidence=self.min_confidence,
            field_map=self.field_map,
        )
        self.outcomes[shortcode] = outcome
        return resolution

    def report(self) -> dict[str, Any]:
        """Aggregate audit of what vision actually changed. Counts only."""
        summary = outcome_report(self.outcomes)
        summary["enrichment_available"] = len(self.enriched_by_code)
        summary["min_confidence"] = self.min_confidence
        return summary


def make_vision_attribute_provider(
    enriched_by_code: dict[str, EnrichedListing],
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    field_map: dict[str, str] | None = None,
    caption_resolution: Callable[[Any], Any] | None = None,
    resolution_cls: Any = None,
    observation_cls: Any = None,
) -> VisionAttributeProvider:
    """Build the enriched provider for `run_controlled_arms`.

    Loads Work's `caption_resolution`, `AttributeResolution` and
    `AttributeObservation` unless they are supplied. The keyword arguments exist
    for tests, which inject an exact double of Work's models; production callers
    pass none of them.

    Raises `WorkContractsUnavailable` when Work is not importable and nothing
    was injected.
    """
    if caption_resolution is None or resolution_cls is None or observation_cls is None:
        work_caption_resolution, work_resolution, work_observation = load_work_contracts()
        caption_resolution = caption_resolution or work_caption_resolution
        resolution_cls = resolution_cls or work_resolution
        observation_cls = observation_cls or work_observation

    return VisionAttributeProvider(
        enriched_by_code=enriched_by_code,
        caption_resolution=caption_resolution,
        resolution_cls=resolution_cls,
        observation_cls=observation_cls,
        min_confidence=min_confidence,
        field_map=dict(field_map or VISION_FIELD_TO_KEY),
    )
