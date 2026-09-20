"""Controlled caption-only versus enriched ranking experiment runner."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from rental_retrieval.retrieval.attribute_provider import AttributeProvider
from rental_retrieval.retrieval.baseline import QUALITY_THRESHOLD, rank
from rental_retrieval.schemas.event import EventContext
from rental_retrieval.schemas.instagram import InstagramSavedPost
from rental_retrieval.schemas.profile import PersonalFitProfile
from rental_retrieval.schemas.ranking import RankingRun


class ControlledArmSettings(BaseModel):
    """The single settings object shared by both ranking arms."""

    reporting_k: int = Field(default=10, gt=0)
    retained_depth: int = Field(default=20, gt=0)
    quality_threshold: float = QUALITY_THRESHOLD

    @model_validator(mode="after")
    def require_rows_below_cutoff(self) -> ControlledArmSettings:
        if self.retained_depth <= self.reporting_k:
            raise ValueError("retained_depth must exceed reporting_k for cutoff diagnostics")
        return self


class ControlledArmRuns(BaseModel):
    """Two ranker outputs produced from the same inputs and settings."""

    settings: ControlledArmSettings
    caption_only: RankingRun
    enriched: RankingRun


def run_controlled_arms(
    inventory: list[InstagramSavedPost],
    profile: PersonalFitProfile,
    event: EventContext,
    enriched_provider: AttributeProvider,
    settings: ControlledArmSettings | None = None,
) -> ControlledArmRuns:
    """Run both experiment arms without allowing their rank settings to drift."""
    shared = settings or ControlledArmSettings()
    common = {
        "inventory": inventory,
        "profile": profile,
        "event": event,
        "top_k": shared.retained_depth,
        "quality_threshold": shared.quality_threshold,
    }
    caption_only = rank(**common)
    enriched = rank(**common, attribute_provider=enriched_provider)

    if caption_only.total_candidates != enriched.total_candidates:
        raise AssertionError("Controlled arms did not evaluate the same candidate count")
    if caption_only.weights != enriched.weights:
        raise AssertionError("Controlled arms did not use identical ranking weights")

    return ControlledArmRuns(
        settings=shared,
        caption_only=caption_only,
        enriched=enriched,
    )
