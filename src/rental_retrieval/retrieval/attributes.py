"""Deterministic garment attributes from caption text.

Every function here returns a value or None. None means "the caption does not
say", and it is never replaced by a default, a guess, or an inference from a
different attribute.

MEASURED COVERAGE on the real 157-dress inventory, which is why the ranker is
built the way it is:

    length        36.9%   (mini 19%, maxi 12%, midi 3%)
    colour        15.3%
    sleeve         5.1%
    neckline       2.5%    <- the stated style preference is near-unobservable
    sparkle/tex.   1.3%
    fabric         3.2%

Captions describe price and size. They do not describe garments. That is a
finding about the data, not a deficiency in these patterns, and it is the
strongest argument for adding vision in a later phase.

PUBLIC-SAFE. Patterns only.
"""

from __future__ import annotations

import re

from rental_retrieval.schemas.event import DressLength

# --- Length ------------------------------------------------------------------
# Ordered: specific before generic. 'gown' implies floor length in this domain.

_LENGTH_RULES: list[tuple[DressLength, re.Pattern[str]]] = [
    ("mini", re.compile(r"\b(mini|micro[- ]?mini|short dress)\b", re.I)),
    ("midi", re.compile(r"\b(midi|tea[- ]?length|knee[- ]?length|below[- ]the[- ]knee)\b", re.I)),
    ("maxi", re.compile(r"\b(maxi|floor[- ]?length|full[- ]?length|gown|long dress|to the floor)\b", re.I)),
]


def extract_length(caption: str | None) -> tuple[DressLength, str | None]:
    """Return (length, evidence). 'unknown' when the caption is silent."""
    if not caption:
        return "unknown", None
    for length, pattern in _LENGTH_RULES:
        m = pattern.search(caption)
        if m:
            return length, m.group(0)
    return "unknown", None


# --- Colour ------------------------------------------------------------------
# Mapped to families, because a vibe cares about "bright" or "jewel tone" rather
# than about "cerulean".

_COLOUR_FAMILIES: dict[str, tuple[str, ...]] = {
    "black": ("black", "noir", "onyx"),
    "white": ("white", "ivory", "cream", "off[- ]white", "winter white"),
    "red": ("red", "crimson", "cherry", "scarlet", "ruby", "wine", "burgundy", "merlot"),
    "pink": ("pink", "blush", "rose", "fuchsia", "magenta", "hot pink"),
    "green": ("green", "emerald", "sage", "olive", "kelly green", "forest"),
    "blue": ("blue", "navy", "cobalt", "periwinkle", "sky blue", "baby blue"),
    "purple": ("purple", "lavender", "lilac", "violet", "plum"),
    "yellow": ("yellow", "butter", "lemon", "marigold"),
    "orange": ("orange", "coral", "peach", "apricot", "rust"),
    "gold": ("gold", "golden", "champagne"),
    "silver": ("silver", "pewter", "chrome"),
    "neutral": ("beige", "tan", "taupe", "nude", "camel", "brown", "chocolate", "khaki"),
}

_BRIGHT_FAMILIES = {"red", "pink", "yellow", "orange", "green", "blue", "purple"}
_JEWEL_FAMILIES = {"red", "green", "blue", "purple"}
_METALLIC_FAMILIES = {"gold", "silver"}


def extract_colour(caption: str | None) -> tuple[str | None, str | None]:
    """Return (colour_family, evidence)."""
    if not caption:
        return None, None
    for family, terms in _COLOUR_FAMILIES.items():
        pattern = re.compile(r"\b(" + "|".join(terms) + r")\b", re.I)
        m = pattern.search(caption)
        if m:
            return family, m.group(0)
    return None, None


def colour_is_bright(family: str | None) -> bool | None:
    return None if family is None else family in _BRIGHT_FAMILIES


def colour_is_jewel(family: str | None) -> bool | None:
    return None if family is None else family in _JEWEL_FAMILIES


def colour_is_metallic(family: str | None) -> bool | None:
    return None if family is None else family in _METALLIC_FAMILIES


# --- Sleeve ------------------------------------------------------------------

_SLEEVE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("long_sleeve", re.compile(r"\b(long[- ]sleeve[ds]?|full sleeve)\b", re.I)),
    ("three_quarter", re.compile(r"\b(3/4 sleeve|three[- ]quarter sleeve)\b", re.I)),
    ("short_sleeve", re.compile(r"\b(short[- ]sleeve[ds]?|cap sleeve|puff sleeve)\b", re.I)),
    ("strapless", re.compile(r"\b(strapless|bandeau|tube)\b", re.I)),
    ("one_shoulder", re.compile(r"\b(one[- ]shoulder|single shoulder|asymmetric(?:al)? shoulder)\b", re.I)),
    ("halter", re.compile(r"\bhalter\b", re.I)),
    ("sleeveless", re.compile(r"\b(sleeveless|tank|spaghetti strap|thin strap)\b", re.I)),
]


def extract_sleeve(caption: str | None) -> tuple[str | None, str | None]:
    if not caption:
        return None, None
    for sleeve, pattern in _SLEEVE_RULES:
        m = pattern.search(caption)
        if m:
            return sleeve, m.group(0)
    return None, None


# --- Neckline ----------------------------------------------------------------
# The controlled vocabulary that StylePreferences refers to.

_NECKLINE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("plunge_v", re.compile(r"\b(deep v|plunge|plunging|low v|v[- ]?neck)\b", re.I)),
    ("cowl", re.compile(r"\bcowl\b", re.I)),
    ("asymmetric", re.compile(r"\b(asymmetric(?:al)?|one[- ]shoulder|diagonal)\b", re.I)),
    ("sweetheart", re.compile(r"\bsweetheart\b", re.I)),
    ("halter", re.compile(r"\bhalter\b", re.I)),
    ("square", re.compile(r"\bsquare[- ]?neck\b", re.I)),
    ("scoop", re.compile(r"\bscoop[- ]?neck\b", re.I)),
    ("straight_strapless", re.compile(r"\b(straight[- ]across|bandeau|tube top|straight strapless)\b", re.I)),
    ("strapless", re.compile(r"\bstrapless\b", re.I)),
    ("high_neck", re.compile(r"\b(high[- ]?neck|mock neck|turtleneck)\b", re.I)),
    ("open_back", re.compile(r"\b(open back|backless|low back)\b", re.I)),
]


def extract_neckline(caption: str | None) -> tuple[str | None, str | None]:
    if not caption:
        return None, None
    for neckline, pattern in _NECKLINE_RULES:
        m = pattern.search(caption)
        if m:
            return neckline, m.group(0)
    return None, None


# --- Silhouette --------------------------------------------------------------

_SILHOUETTE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("flowing", re.compile(r"\b(flow(?:y|ing)|a[- ]?line|fit and flare|tiered|ruffle|drape[ds]?|babydoll|empire)\b", re.I)),
    ("structured", re.compile(r"\b(structured|corset|tailored|column|sheath)\b", re.I)),
    ("bodycon", re.compile(r"\b(bodycon|body[- ]?con|fitted|slinky)\b", re.I)),
    ("slip", re.compile(r"\bslip\b", re.I)),
    ("wrap", re.compile(r"\bwrap\b", re.I)),
]


def extract_silhouette(caption: str | None) -> tuple[str | None, str | None]:
    if not caption:
        return None, None
    for silhouette, pattern in _SILHOUETTE_RULES:
        m = pattern.search(caption)
        if m:
            return silhouette, m.group(0)
    return None, None


# --- Texture -----------------------------------------------------------------

_SPARKLE = re.compile(r"\b(sequin(?:s|ned)?|sparkl\w*|glitter\w*|metallic|shimmer\w*|beaded|rhinestone|disco|holograph\w*)\b", re.I)
_FABRIC = re.compile(r"\b(satin|silk|velvet|lace|chiffon|tulle|linen|knit|crepe|cotton|mesh|taffeta|organza)\b", re.I)


def extract_sparkle(caption: str | None) -> tuple[bool | None, str | None]:
    """True when explicitly mentioned, None when silent. Never False.

    Absence of the word 'sequin' is not evidence a dress has no sequins, so a
    False here would be an invented fact.
    """
    if not caption:
        return None, None
    m = _SPARKLE.search(caption)
    return (True, m.group(0)) if m else (None, None)


def extract_fabric(caption: str | None) -> tuple[str | None, str | None]:
    if not caption:
        return None, None
    m = _FABRIC.search(caption)
    return (m.group(0).lower(), m.group(0)) if m else (None, None)


# --- Availability ------------------------------------------------------------

_UNAVAILABLE = re.compile(
    r"\b(booked|taken|unavailable|reserved|rented out|no longer available|sold|not available|on hold)\b",
    re.I,
)
_AVAILABLE = re.compile(r"\b(available|open|still up|up for rent|free)\b", re.I)


def extract_availability(caption: str | None) -> tuple[str, str | None]:
    """Return (status, evidence) in {'available','unavailable','unknown'}.

    Unavailable is checked first: "available Oct 1, booked Oct 8" should be
    treated as constrained rather than freely available, and V1 has no date
    reasoning to resolve it properly. The conservative read is flagged, not
    silently dropped.
    """
    if not caption:
        return "unknown", None
    m = _UNAVAILABLE.search(caption)
    if m:
        return "unavailable", m.group(0)
    m = _AVAILABLE.search(caption)
    if m:
        return "available", m.group(0)
    return "unknown", None


# --- Bundle ------------------------------------------------------------------

def extract_all(caption: str | None) -> dict[str, str | None]:
    """Every attribute in one pass, as a flat dict of value-or-None.

    Keys ending in '_evidence' hold the literal matched substring, so any
    attribute can be traced back to the words that produced it.
    """
    length, length_ev = extract_length(caption)
    colour, colour_ev = extract_colour(caption)
    sleeve, sleeve_ev = extract_sleeve(caption)
    neckline, neckline_ev = extract_neckline(caption)
    silhouette, silhouette_ev = extract_silhouette(caption)
    sparkle, sparkle_ev = extract_sparkle(caption)
    fabric, fabric_ev = extract_fabric(caption)
    availability, availability_ev = extract_availability(caption)

    return {
        "length": length,
        "length_evidence": length_ev,
        "colour_family": colour,
        "colour_evidence": colour_ev,
        "sleeve": sleeve,
        "sleeve_evidence": sleeve_ev,
        "neckline": neckline,
        "neckline_evidence": neckline_ev,
        "silhouette": silhouette,
        "silhouette_evidence": silhouette_ev,
        "sparkle": "yes" if sparkle else None,
        "sparkle_evidence": sparkle_ev,
        "fabric": fabric,
        "fabric_evidence": fabric_ev,
        "availability": availability,
        "availability_evidence": availability_ev,
    }
