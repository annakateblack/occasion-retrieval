"""Deterministic extraction of structured fields from a rental caption.

The governing rule: **never invent a value.** If a pattern does not fire with
confidence, the normalized field stays None and the raw caption is preserved.
A null we can explain is worth more than a guess we cannot audit.

Every function returns candidates with the literal `raw_text` they matched, so
any normalized value can be traced back to its input.

PUBLIC-SAFE. Patterns are written against the general grammar of rental
captions, not against any specific account or post.
"""

from __future__ import annotations

import re

from rental_retrieval.schemas.instagram import GarmentType, PriceCandidate, SizeCandidate

# --- Size --------------------------------------------------------------------

ALPHA_SIZES = {"xxs", "xs", "s", "m", "l", "xl", "xxl",
               "small", "medium", "large", "x-small", "x-large"}
_ALPHA_CANON = {"small": "S", "medium": "M", "large": "L",
                "x-small": "XS", "x-large": "XL"}

# Ordered most-specific first. The first rule to match a region wins.
_SIZE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("labelled_range",
     re.compile(r"\bsizes?\s*[:\-]?\s*(\d{1,2})\s*(?:-|/|to)\s*(\d{1,2})\b", re.I)),
    ("labelled_alpha_range",
     re.compile(r"\bsizes?\s*[:\-]?\s*(xxs|xs|s|m|l|xl|xxl)\s*(?:-|/)\s*(xxs|xs|s|m|l|xl|xxl)\b", re.I)),
    ("labelled_numeric",
     re.compile(r"\bsizes?\s*[:\-]?\s*(\d{1,2})\b", re.I)),
    ("labelled_alpha",
     re.compile(r"\bsizes?\s*[:\-]?\s*(xxs|xs|small|s|medium|m|large|l|xl|xxl)\b", re.I)),
    ("bare_alpha_slash",
     re.compile(r"\b(xxs|xs|s|m|l|xl)\s*/\s*(xxs|xs|s|m|l|xl)\b", re.I)),
]


def extract_sizes(caption: str) -> tuple[list[SizeCandidate], str | None]:
    """Return (candidates, raw_size_text).

    A bare 'S' or 'M' with no 'size' keyword is deliberately NOT matched. In
    real captions those letters appear constantly inside ordinary words and
    sentences, and matching them produces noise that looks like data.
    """
    if not caption:
        return [], None

    out: list[SizeCandidate] = []
    consumed: list[tuple[int, int]] = []
    raw_bits: list[str] = []

    def overlaps(span: tuple[int, int]) -> bool:
        return any(not (span[1] <= s or span[0] >= e) for s, e in consumed)

    for name, pattern in _SIZE_RULES:
        for m in pattern.finditer(caption):
            if overlaps(m.span()):
                continue
            consumed.append(m.span())
            raw = m.group(0).strip()
            raw_bits.append(raw)

            if name in ("labelled_range",):
                for g in m.groups():
                    out.append(SizeCandidate(raw_text=raw, numeric=int(g), pattern=name))
            elif name in ("labelled_alpha_range", "bare_alpha_slash"):
                for g in m.groups():
                    out.append(SizeCandidate(raw_text=raw, alpha=_canon_alpha(g), pattern=name))
            elif name == "labelled_numeric":
                out.append(SizeCandidate(raw_text=raw, numeric=int(m.group(1)), pattern=name))
            else:
                out.append(SizeCandidate(raw_text=raw, alpha=_canon_alpha(m.group(1)), pattern=name))

    return out, ("; ".join(raw_bits) if raw_bits else None)


def _canon_alpha(token: str) -> str:
    t = token.strip().lower()
    return _ALPHA_CANON.get(t, t.upper())


# --- Price -------------------------------------------------------------------

_MONEY = re.compile(r"(?:\$\s?(\d{1,4}(?:\.\d{2})?)|(\d{1,4}(?:\.\d{2})?)\s?\$)")

# Loose cues, used only as a proximity fallback. Longest alternatives first,
# because Python's alternation takes the first that matches, not the longest.
_RENTAL_CUE = re.compile(
    r"\b(rent(?:al)?\s*(?:price|fee|rate)|to rent|per rent|renting|rentals?|rents?)\b", re.I
)
_RETAIL_CUE = re.compile(
    r"\b(retails?\s+for|original(?:ly)?\s*price|originally|retails?|msrp|valued at)\b", re.I
)
_DEPOSIT_CUE = re.compile(r"\b(deposit|late fee|cleaning fee)\b", re.I)

# Anchored patterns: the cue and the amount in one expression, so the binding
# is syntactic rather than merely nearby. These take priority over proximity.
#
# "rental price $60, retails for $395" is the case that forces this. Measuring
# raw distance puts "retails for" 2 characters from $60 and "rental" 7 away, so
# proximity alone mislabels the rental price as retail. Anchoring resolves it
# because "rental price $60" matches as a single unit.
_RENTAL_ANCHORED = re.compile(
    r"(?:\b(?:rent(?:al)?)\s*(?:price|fee|rate)?\s*[:\-]?\s*\$\s?\d{1,4}(?:\.\d{2})?)"
    r"|(?:\$\s?\d{1,4}(?:\.\d{2})?\s*(?:/\s*)?(?:to\s+rent|per\s+rent|rental|rent\b))",
    re.I,
)
_RETAIL_ANCHORED = re.compile(
    r"(?:\b(?:retails?\s+for|retails?|originally|original\s*price|msrp|valued\s+at)"
    r"\s*[:\-]?\s*\$\s?\d{1,4}(?:\.\d{2})?)"
    r"|(?:\$\s?\d{1,4}(?:\.\d{2})?\s*(?:retail|original)\b)",
    re.I,
)
_DEPOSIT_ANCHORED = re.compile(
    r"(?:\b(?:deposit|late\s*fee|cleaning\s*fee)\s*[:\-]?\s*\$\s?\d{1,4}(?:\.\d{2})?)"
    r"|(?:\$\s?\d{1,4}(?:\.\d{2})?\s*(?:deposit|late\s*fee|cleaning\s*fee)\b)",
    re.I,
)

_ANCHORED = [
    ("rental", _RENTAL_ANCHORED),
    ("retail", _RETAIL_ANCHORED),
    ("deposit", _DEPOSIT_ANCHORED),
]

_CONTEXT_WINDOW = 40  # characters either side of the amount, for the fallback


def _nearest_cue_distance(caption: str, cue: re.Pattern[str],
                          start: int, end: int) -> int | None:
    """Character distance from an amount to the closest match of `cue`.

    Returns None when the cue does not appear within the context window.
    Distance is measured from whichever edge of the amount is nearer, so a cue
    immediately before and one immediately after are treated equally.
    """
    best: int | None = None
    for m in cue.finditer(caption):
        if m.end() <= start:
            distance = start - m.end()
        elif m.start() >= end:
            distance = m.start() - end
        else:
            distance = 0  # overlapping the amount itself
        if distance <= _CONTEXT_WINDOW and (best is None or distance < best):
            best = distance
    return best


def extract_prices(caption: str) -> tuple[list[PriceCandidate], str | None]:
    """Find money amounts and label each by its NEAREST cue.

    "rental price $60, retails for $395" puts both cues inside a single window,
    so an any-cue-in-window test labels both amounts ambiguous and loses two
    perfectly readable values. Measuring distance to the closest cue resolves it
    the way a reader does: $60 sits next to "rental price", $395 next to
    "retails for".

    Genuine ambiguity is still preserved. Equal distances, or no cue at all,
    yield 'unknown' rather than a guess. In the real export 45 of 221 Shop
    captions carried two or more amounts, so guessing would corrupt a fifth of
    the catalog.
    """
    if not caption:
        return [], None

    out: list[PriceCandidate] = []
    raw_bits: list[str] = []

    # Step 1: find amounts bound to a cue by an anchored pattern. Record the
    # character span each anchored match covers so step 2 can skip them.
    anchored_kind: dict[int, tuple[str, str]] = {}
    for kind, pattern in _ANCHORED:
        for am in pattern.finditer(caption):
            for money in _MONEY.finditer(am.group(0)):
                absolute_start = am.start() + money.start()
                anchored_kind.setdefault(absolute_start, (kind, f"anchored_{kind}"))

    for m in _MONEY.finditer(caption):
        amount = m.group(1) or m.group(2)\n        cents = int(round(float(amount) * 100))
        start, end = m.start(), m.end()

        # Step 1 result wins outright when present.
        if start in anchored_kind:
            kind, pattern_name = anchored_kind[start]
            raw_bits.append(m.group(0))
            out.append(PriceCandidate(raw_text=m.group(0), amount_cents=cents,
                                      kind=kind, pattern=pattern_name))
            continue

        # Step 2: proximity fallback for amounts with no syntactic binding.
        distances = {
            "rental": _nearest_cue_distance(caption, _RENTAL_CUE, start, end),
            "retail": _nearest_cue_distance(caption, _RETAIL_CUE, start, end),
            "deposit": _nearest_cue_distance(caption, _DEPOSIT_CUE, start, end),
        }
        present = {k: v for k, v in distances.items() if v is not None}

        if not present:
            kind, pattern = "unknown", "bare_amount"
        else:
            closest = min(present.values())
            winners = [k for k, v in present.items() if v == closest]
            if len(winners) == 1:
                kind, pattern = winners[0], f"nearest_cue:{closest}"
            else:
                # Tied cues are genuinely ambiguous. Do not pick one.
                kind, pattern = "unknown", f"tied_cues:{'+'.join(sorted(winners))}"

        raw_bits.append(m.group(0))
        out.append(PriceCandidate(raw_text=m.group(0), amount_cents=cents,
                                  kind=kind, pattern=pattern))

    return out, ("; ".join(raw_bits) if raw_bits else None)


def resolve_rental_price(candidates: list[PriceCandidate]) -> tuple[int | None, list[str]]:
    """Pick a single rental price, or decline to.

    Rules, in order:
      1. Exactly one candidate explicitly labelled 'rental' -> use it.
      2. No rental label, exactly one amount overall, and nothing labelled
         retail -> use it, and warn that it was inferred from being the only
         amount present.
      3. Anything else -> None, with a warning naming the ambiguity.
    """
    warnings: list[str] = []
    rentals = [c for c in candidates if c.kind == "rental"]

    if len(rentals) == 1:
        return rentals[0].amount_cents, warnings
    if len(rentals) > 1:
        distinct = {c.amount_cents for c in rentals}
        if len(distinct) == 1:
            return rentals[0].amount_cents, warnings
        warnings.append(f"multiple_distinct_rental_prices:{sorted(distinct)}")
        return None, warnings

    if len(candidates) == 1 and candidates[0].kind == "unknown":
        warnings.append("rental_price_inferred_from_sole_amount")
        return candidates[0].amount_cents, warnings

    if len(candidates) > 1:
        warnings.append(f"ambiguous_price_count:{len(candidates)}")
    return None, warnings


def resolve_retail_price(candidates: list[PriceCandidate]) -> int | None:
    retails = [c for c in candidates if c.kind == "retail"]
    if len(retails) == 1:
        return retails[0].amount_cents
    if retails and len({c.amount_cents for c in retails}) == 1:
        return retails[0].amount_cents
    return None


# --- Garment type ------------------------------------------------------------
# Ordered: the first match wins, so more specific garments are tested before
# generic ones. 'set' precedes 'top' because "two piece set with a top" is a set.

_GARMENT_RULES: list[tuple[GarmentType, re.Pattern[str]]] = [
    ("dress", re.compile(r"\b(dress(?:es)?|gown|maxi|midi|slip\s?dress|sundress)\b", re.I)),
    ("set", re.compile(r"\b(two[\s-]?piece|2[\s-]?piece|co[\s-]?ord|matching set|\bset\b)\b", re.I)),
    ("jumpsuit", re.compile(r"\b(jumpsuit|romper|playsuit)\b", re.I)),
    ("skirt", re.compile(r"\b(skirt|maxi skirt|mini skirt)\b", re.I)),
    ("top", re.compile(r"\b(top|blouse|bodysuit|corset|cami)\b", re.I)),
    ("other", re.compile(r"\b(pants|trousers|blazer|jacket|coat|shoes|heels|bag|purse)\b", re.I)),
]


def extract_garment_type(caption: str) -> tuple[GarmentType, str | None]:
    """Classify the garment, preserving the matched evidence.

    Returns 'unknown' rather than guessing when nothing matches. Non-dress
    garments are classified honestly and filtered downstream, never discarded
    here: the dresses-only rule is a query boundary, not a parsing boundary.
    """
    if not caption:
        return "unknown", None
    for garment, pattern in _GARMENT_RULES:
        m = pattern.search(caption)
        if m:
            return garment, m.group(0)
    return "unknown", None


# --- Free-text fields --------------------------------------------------------

_AVAILABILITY = re.compile(
    r"[^.\n]*\b(available|unavailable|booked|taken|reserved|pending|on hold)\b[^.\n]*", re.I
)
_CONTACT = re.compile(
    r"[^.\n]*\b(dm|d\.m\.|message me|inquire|comment below|text)\b[^.\n]*", re.I
)
_DRYCLEAN = re.compile(r"\b(dry[\s-]?clean(?:ing|ed)?)\b", re.I)


def extract_availability_text(caption: str) -> str | None:
    if not caption:
        return None
    m = _AVAILABILITY.search(caption)
    return m.group(0).strip() if m else None


def extract_contact_text(caption: str) -> str | None:
    if not caption:
        return None
    m = _CONTACT.search(caption)
    return m.group(0).strip() if m else None


def extract_dryclean(caption: str) -> bool | None:
    """True if mentioned, None if not. Never False: absence of a mention is not
    evidence that dry cleaning is not required."""
    if not caption:
        return None
    return True if _DRYCLEAN.search(caption) else None


# --- Deliberately not extracted ----------------------------------------------
#
# brand and item_name stay None in V1.
#
# Captions name brands inconsistently (in hashtags, mid-sentence, abbreviated,
# misspelled) and there is no deterministic rule that separates a brand from any
# other proper noun without a brand list. A brand list built from this corpus
# would be fitted to it and would not generalize to a future export.
#
# The honest options are a curated brand gazetteer or an LLM extraction pass.
# Both are real, both belong in the later extraction phase, and neither is
# deterministic. Until then the raw caption is preserved and these stay null.
