"""Hard filters. Constraints, not preferences.

THE GOVERNING RULE: a hard filter fires only on **positive evidence of
incompatibility**. Unknown never excludes.

This matters more here than it would in a richer dataset. Length is unknown for
63% of the inventory and availability is unknown for over 90%. A filter that
treated unknown as failure would delete most of the catalog for lacking a
caption detail, which is a data-completeness problem masquerading as a
suitability judgement.

PUBLIC-SAFE. Logic only.
"""

from __future__ import annotations

from rental_retrieval.schemas.event import EventContext
from rental_retrieval.schemas.profile import PersonalFitProfile
from rental_retrieval.schemas.ranking import HardFilterResult


def filter_availability(availability: str, event: EventContext) -> HardFilterResult:
    """Exclude only a listing stated to be unavailable."""
    if availability == "unavailable" and event.exclude_unavailable_on_date:
        return HardFilterResult(
            name="availability",
            outcome="fail",
            reason="Caption states the item is unavailable, booked or taken",
            evidence=availability,
        )
    if availability == "unknown":
        return HardFilterResult(
            name="availability",
            outcome="not_applicable",
            reason="Availability not stated; eligible, confirm with renter",
        )
    return HardFilterResult(
        name="availability", outcome="pass", reason="Caption indicates availability"
    )


def filter_size(numeric: list[int], alpha: list[str],
                profile: PersonalFitProfile) -> HardFilterResult:
    """Exclude only when every stated size is in the 'unlikely' band.

    A listing offering sizes 2 and 4 passes: it has a workable size. A listing
    offering only size 2 fails. A listing stating no size at all is not
    applicable, because we have no evidence either way.
    """
    if not numeric and not alpha:
        return HardFilterResult(
            name="size",
            outcome="not_applicable",
            reason="No size stated; eligible but unverified",
        )

    band, evidence = profile.size_band(numeric, alpha)

    if band is None:
        return HardFilterResult(
            name="size",
            outcome="not_applicable",
            reason="Stated size is outside the profile's known bands",
            evidence=", ".join([str(n) for n in numeric] + alpha) or None,
        )
    if band == "unlikely":
        return HardFilterResult(
            name="size",
            outcome="fail",
            reason="Every stated size falls in the profile's unlikely-fit band",
            evidence=evidence,
        )
    return HardFilterResult(
        name="size", outcome="pass",
        reason=f"Stated size falls in the {band} band", evidence=evidence,
    )


def filter_price(price_cents: int | None, length: str,
                 event: EventContext) -> HardFilterResult:
    """Apply the event's price ceiling, scoped to the lengths it covers.

    Requires a known price AND a known length when the rule is length-scoped.
    A mini-only $100 cap cannot be enforced against a dress whose length is not
    stated, so that case is 'not_applicable' and the uncertainty is flagged
    rather than resolved by assumption.
    """
    rule = event.price_rule

    if rule.max_rental_price_cents is None or not rule.is_hard_filter:
        return HardFilterResult(
            name="price_ceiling", outcome="not_applicable",
            reason="Event sets no hard price ceiling",
        )
    if price_cents is None:
        return HardFilterResult(
            name="price_ceiling", outcome="not_applicable",
            reason="No rental price parsed; ceiling cannot be enforced",
        )
    if rule.applies_to_lengths and length == "unknown":
        return HardFilterResult(
            name="price_ceiling", outcome="not_applicable",
            reason=(
                f"Ceiling applies to {'/'.join(rule.applies_to_lengths)} only and "
                "this listing's length is not stated"
            ),
        )
    if not rule.applies_to(length):  # type: ignore[arg-type]
        return HardFilterResult(
            name="price_ceiling", outcome="not_applicable",
            reason=f"Ceiling does not apply to {length} dresses for this event",
        )
    if price_cents > rule.max_rental_price_cents:
        return HardFilterResult(
            name="price_ceiling", outcome="fail",
            reason=(
                f"${price_cents/100:.0f} exceeds the ${rule.max_rental_price_cents/100:.0f} "
                f"ceiling for {length} dresses at this event"
            ),
            evidence=f"${price_cents/100:.0f}",
        )
    return HardFilterResult(
        name="price_ceiling", outcome="pass",
        reason=f"${price_cents/100:.0f} is within the ceiling",
        evidence=f"${price_cents/100:.0f}",
    )


def apply_all(numeric: list[int], alpha: list[str], price_cents: int | None,
              length: str, availability: str, profile: PersonalFitProfile,
              event: EventContext) -> tuple[list[HardFilterResult], bool]:
    """Run every filter. Returns (results, passed_all).

    All filters always run, even after one fails, so the output shows the full
    picture rather than stopping at the first rejection.
    """
    results = [
        filter_availability(availability, event),
        filter_size(numeric, alpha, profile),
        filter_price(price_cents, length, event),
    ]
    return results, not any(r.outcome == "fail" for r in results)
