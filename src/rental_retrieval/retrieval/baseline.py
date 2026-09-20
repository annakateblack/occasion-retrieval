"""Baseline ranker: hard filters, then interpretable weighted scoring.

Deterministic end to end. No model, no embedding, no network call, no randomness.
Two runs over the same inputs produce byte-identical output, which is what makes
this a baseline that later approaches can be measured against.

Pipeline:

    dress inventory
      -> deterministic attribute extraction   (unknowns preserved)
      -> hard filters                         (positive evidence only)
      -> six weighted soft scores             (coverage-weighted)
      -> deterministic sort
      -> explanation generation               (from the components, not prose)
      -> top K, or fewer

PUBLIC-SAFE. Logic only.
"""

from __future__ import annotations

from rental_retrieval.retrieval import attributes as attr
from rental_retrieval.retrieval import filters, scoring
from rental_retrieval.retrieval.vibe import resolve_event_tags, unknown_tags
from rental_retrieval.schemas.event import EventContext
from rental_retrieval.schemas.instagram import InstagramSavedPost
from rental_retrieval.schemas.profile import PersonalFitProfile
from rental_retrieval.schemas.ranking import RankedDress, RankingRun

QUALITY_THRESHOLD = 0.35
"""Minimum overall score to be returned at all.

Set deliberately low. The point of a threshold here is to avoid padding a list
with items that clearly do not work, not to enforce a quality bar the data
cannot support. Returning six good results beats returning ten where four are
filler.
"""


def prepare_event(event: EventContext) -> tuple[EventContext, list[str]]:
    """Resolve vibe text and explicit tags into the controlled vocabulary."""
    notes: list[str] = []
    merged, discouraged, evidence = resolve_event_tags(event.vibe_tags, event.vibe_text)

    unknown = unknown_tags(event.vibe_tags)
    if unknown:
        notes.append(
            f"Ignored {len(unknown)} tag(s) outside the controlled vocabulary: "
            f"{', '.join(sorted(unknown))}"
        )

    prepared = event.model_copy(update={
        "resolved_vibe_tags": merged,
        "discouraged_tags": list(dict.fromkeys(event.discouraged_tags + discouraged)),
        "vibe_parse_evidence": evidence,
    })
    return prepared, notes


def _sizes_from(post: InstagramSavedPost) -> tuple[list[int], list[str]]:
    numeric = sorted({s.numeric for s in post.size_candidates if s.numeric is not None})
    alpha = sorted({s.alpha.upper() for s in post.size_candidates if s.alpha})
    return list(numeric), list(alpha)


def _size_summary(numeric: list[int], alpha: list[str], raw: str | None) -> str | None:
    parts: list[str] = []
    if numeric:
        parts.append("size " + "/".join(str(n) for n in numeric))
    if alpha:
        parts.append("/".join(alpha))
    if not parts:
        return None
    summary = ", ".join(parts)
    return f"{summary} (from: {raw})" if raw else summary


def build_explanation(result_components, length: str, size_band: str | None,
                      coverage: float, flags: list[str]) -> str:
    """Compose the explanation from the score components themselves.

    Generated from the same objects that produced the number, so the text can
    never disagree with the score. It names the strongest known contributors,
    then what could not be evaluated.
    """
    known = [c for c in result_components if c.is_known]
    unknown = [c for c in result_components if not c.is_known]

    ranked = sorted(known, key=lambda c: c.weighted, reverse=True)
    leading = [c for c in ranked if c.score is not None and c.score >= 0.6][:3]
    dragging = [c for c in ranked if c.score is not None and c.score <= 0.35][:2]

    sentences: list[str] = []

    if leading:
        sentences.append("Ranked on " + "; ".join(c.reason for c in leading) + ".")
    elif known:
        sentences.append("Ranked on " + ranked[0].reason + ".")

    if dragging:
        sentences.append("Held back by " + "; ".join(c.reason for c in dragging) + ".")

    if unknown:
        names = ", ".join(c.name for c in unknown)
        sentences.append(
            f"Not evaluable from the caption: {names} "
            f"(scored on {coverage:.0%} of available signal weight)."
        )

    if flags:
        sentences.append("Flags: " + "; ".join(flags) + ".")

    return " ".join(sentences)


def rank(
    inventory: list[InstagramSavedPost],
    profile: PersonalFitProfile,
    event: EventContext,
    top_k: int = 10,
    quality_threshold: float = QUALITY_THRESHOLD,
) -> RankingRun:
    """Rank a dress inventory for one event and one profile."""
    prepared, notes = prepare_event(event)

    rejected: dict[str, int] = {}
    scored: list[tuple[float, float, str, RankedDress]] = []
    passed_count = 0

    for post in inventory:
        caption = post.raw_caption
        a = attr.extract_all(caption)
        length = a["length"] or "unknown"
        availability = a["availability"] or "unknown"
        numeric, alpha = _sizes_from(post)

        filter_results, passed = filters.apply_all(
            numeric, alpha, post.rental_price_cents, length, availability, profile, prepared
        )

        if not passed:
            for r in filter_results:
                if r.outcome == "fail":
                    rejected[r.name] = rejected.get(r.name, 0) + 1
            continue
        passed_count += 1

        components = [
            scoring.score_fit(numeric, alpha, profile),
            scoring.score_style(a["neckline"], a["silhouette"], profile),
            scoring.score_event(length, a["colour_family"], a["sparkle"], a["sleeve"], prepared),
            scoring.score_seasonality(a["colour_family"], a["sleeve"], a["fabric"], prepared),
            scoring.score_price(post.rental_price_cents, prepared),
            scoring.score_availability(availability),
        ]
        overall, coverage = scoring.combine(components)

        flags: list[str] = []
        if availability == "unknown":
            flags.append("confirm with renter")
        if not numeric and not alpha:
            flags.append("size not stated")
        if post.rental_price_cents is None:
            flags.append("price not stated")
        if length == "unknown":
            flags.append("length not stated")
        if any(w.startswith("rental_price_inferred") for w in post.parse_warnings):
            flags.append("price inferred from the only amount in the caption")

        band, _ = profile.size_band(numeric, alpha)
        by_name = {c.name: c for c in components}

        row = RankedDress(
            rank=0,
            permalink=post.permalink,
            shortcode=post.shortcode,
            owner_username=post.owner_username,
            raw_caption=caption,
            rental_price_cents=post.rental_price_cents,
            garment_type=post.garment_type,
            dress_length=length,
            size_summary=_size_summary(numeric, alpha, post.raw_size_text),
            size_band=band,
            availability_status=availability,  # type: ignore[arg-type]
            availability_note=a["availability_evidence"],
            attributes={k: v for k, v in a.items() if not k.endswith("_evidence")},
            hard_filters=filter_results,
            passed_hard_filters=True,
            fit_score=by_name["fit"].score,
            style_score=by_name["style"].score,
            event_score=by_name["event"].score,
            seasonality_score=by_name["seasonality"].score,
            price_score=by_name["price"].score,
            availability_score=by_name["availability"].score,
            components=components,
            overall_score=overall,
            coverage=coverage,
            explanation=build_explanation(components, length, band, coverage, flags),
            flags=flags,
        )

        # Sort key: score, then coverage (prefer better-evidenced ties), then
        # shortcode so ordering is total and reproducible.
        scored.append((-overall, -coverage, post.shortcode, row))

    scored.sort(key=lambda t: (t[0], t[1], t[2]))

    results: list[RankedDress] = []
    for row in (t[3] for t in scored):
        if row.overall_score < quality_threshold:
            continue
        if len(results) >= top_k:
            break
        row.rank = len(results) + 1
        results.append(row)

    if len(results) < top_k:
        notes.append(
            f"Returned {len(results)} of a requested {top_k}: "
            f"{passed_count} passed hard filters and "
            f"{sum(1 for t in scored if t[3].overall_score >= quality_threshold)} "
            f"met the {quality_threshold} quality threshold."
        )

    return RankingRun(
        event_name=prepared.name,
        profile_id=profile.profile_id,
        weights=dict(scoring.DEFAULT_WEIGHTS),
        total_candidates=len(inventory),
        passed_hard_filters=passed_count,
        returned=len(results),
        rejected_by_filter=rejected,
        results=results,
        notes=notes,
    )
