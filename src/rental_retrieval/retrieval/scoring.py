"""Soft scoring: six interpretable signals, coverage-weighted.

THE CENTRAL DESIGN DECISION
--------------------------
Every scorer returns a float in [0, 1] **or None**. None means the caption did
not support evaluating that signal.

The overall score is a weighted average over CONTRIBUTING components:

    overall = sum(w_i * v_i for contributing i) / sum(w_i for contributing i)

where v_i is the observed score, or the component's neutral fallback when it was
not observed. A component with no fallback leaves both sums entirely.

Why unknowns are not zero-filled: neckline is observable in 2.5% of the real
inventory and colour in 15%. Under a zero-fill, a dress whose renter wrote
"size 4, $45" would score near zero on four of six signals and could never rank,
while an identical dress with a chatty caption would win. The ranking would be
measuring caption verbosity.

Why SOME unknowns are not dropped either: dropping price entirely produced the
opposite failure on real data. The five top-ranked dresses for the My Tie event
were all listings with no stated price, because a missing component meant a
higher average over the rest. Not knowing a price became an advantage.

So unknowns split two ways, per NEUTRAL_FALLBACK below: transactional facts
every listing has (price, size, availability) stay in at 0.5, and garment
attributes that may genuinely not apply (neckline, fabric) drop out. `coverage`
counts only OBSERVED components either way, so a neutral value never
masquerades as evidence.

WEIGHTS ARE A PRIOR, NOT A RESULT
---------------------------------
Per ADR 003, these are hand-set starting values, chosen from measured attribute
coverage and consequence. They are to be replaced by weights fitted on labelled
data once an evaluation set exists. They are not claimed to be optimal.

PUBLIC-SAFE. Logic only.
"""

from __future__ import annotations

from rental_retrieval.retrieval import attributes as attr
from rental_retrieval.schemas.event import EventContext
from rental_retrieval.schemas.profile import PersonalFitProfile
from rental_retrieval.schemas.ranking import ScoreComponent

DEFAULT_WEIGHTS: dict[str, float] = {
    "fit": 0.35,           # 92% coverage, and the most consequential signal:
                           # a dress that does not fit is not a recommendation
    "event": 0.25,         # length preference plus occasion tag match
    "price": 0.15,         # 79% coverage, real budget constraint
    "seasonality": 0.10,   # thin coverage, but cheap and occasionally decisive
    "style": 0.10,         # 2.5% coverage today; carries the neckline preference
                           # and will matter once vision supplies the attribute
    "availability": 0.05,  # mostly a confidence signal, not a quality one
}

_FIT_BAND_SCORE = {"ideal": 1.0, "workable": 0.6, "unlikely": 0.0}

NEUTRAL_FALLBACK: dict[str, float | None] = {
    # Transactional facts every listing has; unknown means unread, so hold the
    # component in the average at a neutral value.
    "fit": 0.5,
    "price": 0.5,
    "availability": 0.5,
    # Garment attributes that may genuinely not apply; drop them entirely.
    "style": None,
    "seasonality": None,
    # Always computable: length has a defined neutral weight.
    "event": None,
}
"""Which unknowns stay in the weighted average and at what value.

Set from a failure observed on real data: with price dropping out entirely, the
five highest-ranked dresses for the My Tie event were all listings with no
stated price, because fewer components meant a higher average. Not knowing a
price must not be an advantage."""

_SEASON_WARM = {"spring", "summer"}
_SEASON_COLD = {"fall", "winter"}


# --- 1. Fit ------------------------------------------------------------------

def score_fit(numeric: list[int], alpha: list[str],
              profile: PersonalFitProfile) -> ScoreComponent:
    """Size compatibility. The single most reliable signal in this dataset."""
    weight = DEFAULT_WEIGHTS["fit"]

    if not numeric and not alpha:
        return ScoreComponent(
            name="fit", score=None, weight=weight,
            neutral_fallback=NEUTRAL_FALLBACK["fit"],
            reason="No size stated in the caption",
        )

    band, evidence = profile.size_band(numeric, alpha)
    if band is None:
        return ScoreComponent(
            name="fit", score=None, weight=weight,
            neutral_fallback=NEUTRAL_FALLBACK["fit"],
            reason="Stated size is outside the profile's known bands",
            evidence=", ".join([str(n) for n in numeric] + alpha) or None,
        )

    score = _FIT_BAND_SCORE[band]
    labels = {"ideal": "preferred size", "workable": "workable, lower confidence",
              "unlikely": "unlikely to work"}
    return ScoreComponent(
        name="fit", score=score, weight=weight,
        reason=f"Size {evidence} is a {labels[band]}", evidence=evidence,
    )


# --- 2. Style ----------------------------------------------------------------

def score_style(neckline: str | None, silhouette: str | None,
                profile: PersonalFitProfile) -> ScoreComponent:
    """Neckline and silhouette against stated preferences.

    Returns None unless at least one attribute was observed. In the current
    inventory this is None roughly 97% of the time, which is an honest signal
    that captions do not describe necklines, not a defect in the scorer.
    """
    weight = DEFAULT_WEIGHTS["style"]
    style = profile.style
    parts: list[float] = []
    reasons: list[str] = []
    evidence: list[str] = []

    if neckline:
        evidence.append(neckline)
        if neckline in style.preferred_necklines:
            parts.append(1.0)
            reasons.append(f"{neckline} is a preferred neckline")
        elif neckline in style.lower_preference_necklines:
            parts.append(0.25)
            reasons.append(f"{neckline} is a lower-preference neckline")
        else:
            parts.append(0.5)
            reasons.append(f"{neckline} neckline, no stated preference")

    if silhouette:
        evidence.append(silhouette)
        if silhouette in style.preferred_silhouettes:
            parts.append(1.0)
            reasons.append(f"{silhouette} silhouette is preferred")
        elif silhouette in style.lower_preference_silhouettes:
            parts.append(0.25)
            reasons.append(f"{silhouette} silhouette is a lower preference")
        else:
            parts.append(0.5)
            reasons.append(f"{silhouette} silhouette, no stated preference")

    if not parts:
        return ScoreComponent(
            name="style", score=None, weight=weight,
            neutral_fallback=NEUTRAL_FALLBACK["style"],
            reason="Caption describes no neckline or silhouette",
        )

    return ScoreComponent(
        name="style", score=sum(parts) / len(parts), weight=weight,
        reason="; ".join(reasons), evidence=", ".join(evidence),
    )


# --- 3. Event ----------------------------------------------------------------

def score_event(length: str, colour_family: str | None, sparkle: str | None,
                sleeve: str | None, event: EventContext) -> ScoreComponent:
    """Length preference plus vibe-tag match.

    Length always contributes, because the event always expresses a length
    preference and 'unknown' has a defined neutral weight. Vibe contributes only
    when a garment attribute was actually observed.
    """
    weight = DEFAULT_WEIGHTS["event"]
    tags = set(event.resolved_vibe_tags)
    discouraged = set(event.discouraged_tags)

    length_score = event.length_preference.weight_for(length)  # type: ignore[arg-type]
    reasons = [
        f"{length} length scores {length_score:.2f} for this event"
        if length != "unknown"
        else "length not stated, scored neutral"
    ]
    evidence: list[str] = [length]

    vibe_hits: list[float] = []

    if colour_family:
        evidence.append(colour_family)
        if colour_family in tags:
            vibe_hits.append(1.0)
            reasons.append(f"{colour_family} is an event colour")
        elif attr.colour_is_bright(colour_family) and "bright" in tags:
            vibe_hits.append(1.0)
            reasons.append(f"{colour_family} reads bright, which the event wants")
        elif attr.colour_is_jewel(colour_family) and "jewel_tone" in tags:
            vibe_hits.append(1.0)
            reasons.append(f"{colour_family} is a jewel tone")
        elif attr.colour_is_metallic(colour_family) and "metallic" in tags:
            vibe_hits.append(1.0)
            reasons.append(f"{colour_family} is metallic")
        elif attr.colour_is_bright(colour_family) is False and "bright" in tags:
            vibe_hits.append(0.3)
            reasons.append(f"{colour_family} is muted where the event wants bright")
        else:
            vibe_hits.append(0.5)

    if sparkle:
        evidence.append("sparkle")
        if "sparkle" in tags or "metallic" in tags or "festive" in tags:
            vibe_hits.append(1.0)
            reasons.append("sparkle matches the event vibe")
        elif "sparkle" in discouraged:
            vibe_hits.append(0.2)
            reasons.append("sparkle is discouraged for this event")
        else:
            vibe_hits.append(0.5)

    if sleeve:
        evidence.append(sleeve)
        if sleeve == "long_sleeve" and "long_sleeve_bonus" in tags:
            vibe_hits.append(1.0)
            reasons.append("long sleeves earn this event's bonus")
        elif sleeve in ("sleeveless", "strapless") and "sleeveless_bonus" in tags:
            vibe_hits.append(1.0)
            reasons.append("sleeveless matches the event")
        else:
            vibe_hits.append(0.5)

    combined = length_score if not vibe_hits else (
        0.6 * length_score + 0.4 * (sum(vibe_hits) / len(vibe_hits))
    )
    return ScoreComponent(
        name="event", score=combined, weight=weight,
        reason="; ".join(reasons), evidence=", ".join(evidence),
    )


# --- 4. Seasonality ----------------------------------------------------------

def score_seasonality(colour_family: str | None, sleeve: str | None,
                      fabric: str | None, event: EventContext) -> ScoreComponent:
    """Whether the garment's styling suits the event's season.

    None unless a season is known AND at least one seasonal attribute was
    observed. Roughly 85% of the current inventory returns None.
    """
    weight = DEFAULT_WEIGHTS["seasonality"]
    season = event.resolved_season()

    if season is None:
        return ScoreComponent(
            name="seasonality", score=None, weight=weight,
            neutral_fallback=NEUTRAL_FALLBACK["seasonality"],
            reason="Event has no date or season",
        )

    parts: list[float] = []
    reasons: list[str] = []
    evidence: list[str] = []

    warm_fabrics = {"velvet", "knit"}
    cool_fabrics = {"linen", "cotton", "chiffon", "organza"}

    if fabric:
        evidence.append(fabric)
        if season in _SEASON_COLD and fabric in warm_fabrics:
            parts.append(1.0); reasons.append(f"{fabric} suits {season}")
        elif season in _SEASON_WARM and fabric in cool_fabrics:
            parts.append(1.0); reasons.append(f"{fabric} suits {season}")
        elif season in _SEASON_WARM and fabric in warm_fabrics:
            parts.append(0.2); reasons.append(f"{fabric} is heavy for {season}")
        elif season in _SEASON_COLD and fabric in cool_fabrics:
            parts.append(0.3); reasons.append(f"{fabric} is light for {season}")
        else:
            parts.append(0.5)

    if sleeve:
        evidence.append(sleeve)
        if season in _SEASON_COLD and sleeve == "long_sleeve":
            parts.append(1.0); reasons.append(f"long sleeves suit {season}")
        elif season in _SEASON_WARM and sleeve in ("sleeveless", "strapless", "short_sleeve"):
            parts.append(1.0); reasons.append(f"bare or short sleeves suit {season}")
        elif season in _SEASON_WARM and sleeve == "long_sleeve":
            parts.append(0.35); reasons.append(f"long sleeves are warm for {season}")
        else:
            parts.append(0.5)

    if colour_family:
        evidence.append(colour_family)
        if season in _SEASON_WARM and attr.colour_is_bright(colour_family):
            parts.append(0.9); reasons.append(f"{colour_family} reads warm-season")
        elif season == "winter" and colour_family in ("red", "green", "gold", "silver"):
            parts.append(0.9); reasons.append(f"{colour_family} reads winter-holiday")
        else:
            parts.append(0.5)

    if not parts:
        return ScoreComponent(
            name="seasonality", score=None, weight=weight,
            neutral_fallback=NEUTRAL_FALLBACK["seasonality"],
            reason=f"No seasonal attributes stated to judge against {season}",
        )

    return ScoreComponent(
        name="seasonality", score=sum(parts) / len(parts), weight=weight,
        reason="; ".join(reasons), evidence=", ".join(evidence),
    )


# --- 5. Price ----------------------------------------------------------------

def score_price(price_cents: int | None, event: EventContext) -> ScoreComponent:
    """Cheaper scores higher, on a linear ramp under the event's ceiling.

    With no ceiling, a $150 reference is used so the signal still has a scale.
    Note this rewards cheapness rather than value, which is a real limitation:
    the system cannot see quality, so it cannot trade quality against price.
    """
    weight = DEFAULT_WEIGHTS["price"]

    if price_cents is None:
        return ScoreComponent(
            name="price", score=None, weight=weight,
            neutral_fallback=NEUTRAL_FALLBACK["price"],
            reason="No rental price parsed from the caption",
        )

    ceiling = event.price_rule.max_rental_price_cents or 15000
    if price_cents >= ceiling:
        score = 0.0
        reason = f"${price_cents/100:.0f} is at or above the ${ceiling/100:.0f} reference"
    else:
        score = 1.0 - (price_cents / ceiling)
        reason = f"${price_cents/100:.0f} against a ${ceiling/100:.0f} reference"

    return ScoreComponent(
        name="price", score=round(score, 4), weight=weight,
        reason=reason, evidence=f"${price_cents/100:.0f}",
    )


# --- 6. Availability ---------------------------------------------------------

def score_availability(availability: str) -> ScoreComponent:
    """Confidence that the item can actually be rented.

    Every listing has an availability state; an unknown one is unread rather
    than inapplicable. So it takes the same shape as price and size: score=None
    (it was not observed, and coverage says so) with a 0.5 neutral fallback (it
    still participates in the average). A stated-available listing keeps a real
    edge over an unverified one without unknown pretending to be evidence.
    """
    weight = DEFAULT_WEIGHTS["availability"]
    if availability == "available":
        return ScoreComponent(name="availability", score=1.0, weight=weight,
                              reason="Caption states the item is available")
    if availability == "unavailable":
        return ScoreComponent(name="availability", score=0.0, weight=weight,
                              reason="Caption states the item is unavailable")
    return ScoreComponent(name="availability", score=None, weight=weight,
                          neutral_fallback=NEUTRAL_FALLBACK["availability"],
                          reason="Availability not stated; confirm with renter")


# --- Fusion ------------------------------------------------------------------

def combine(components: list[ScoreComponent]) -> tuple[float, float]:
    """Coverage-weighted average. Returns (overall_score, coverage).

    coverage = share of total possible weight that was evaluable. It is reported
    rather than folded into the score, so a thin-evidence listing is visibly
    uncertain instead of silently penalised.
    """
    total_weight = sum(c.weight for c in components)

    # The average runs over components that carry a value, whether observed or
    # neutral-filled. Coverage runs only over OBSERVED ones, so a neutral value
    # never inflates the confidence figure.
    contributing = [c for c in components if c.counts_toward_average]
    contributing_weight = sum(c.weight for c in contributing)

    observed_weight = sum(c.weight for c in components if c.is_known)

    if contributing_weight == 0:
        return 0.0, 0.0

    overall = sum(c.weighted for c in contributing) / contributing_weight
    coverage = observed_weight / total_weight if total_weight else 0.0
    return round(overall, 4), round(coverage, 4)
