"""Deterministic natural-language vibe parsing.

"bright / fun / elevated / late-summer" becomes a set of controlled tags via a
keyword lexicon. No model, no embedding, no API call. Every produced tag records
the words that produced it, so a ranking can be traced back to the phrase the
person typed.

This is deliberately the dumbest thing that works. It is the baseline that a
later semantic approach has to beat, and its failure modes are the argument for
that later approach: it cannot handle a synonym not in the lexicon, it cannot
handle negation, and it cannot handle a vibe expressed as a comparison.

PUBLIC-SAFE. Lexicon only.
"""

from __future__ import annotations

import re

from rental_retrieval.schemas.event import VIBE_TAGS

VIBE_LEXICON: dict[str, list[str]] = {
    "bright": ["bright", "vibrant", "bold", "colorful", "colourful", "punchy", "vivid", "poppy"],
    "dark": ["dark", "moody", "sultry", "vampy"],
    "neutral": ["neutral", "understated", "minimal", "muted", "quiet"],
    "jewel_tone": ["jewel tone", "jewel-tone", "emerald", "sapphire", "ruby", "rich color"],
    "metallic": ["metallic", "gold", "silver", "chrome", "liquid"],
    "sparkle": ["sparkle", "sparkly", "sequin", "sequins", "glitter", "shine", "shiny", "disco", "shimmer"],
    "pastel": ["pastel", "soft color", "baby blue", "blush"],
    "warm_weather": ["late summer", "late-summer", "summer", "warm", "hot", "tropical", "beachy", "spring"],
    "cold_weather": ["winter", "wintery", "wintry", "cold", "chilly", "cozy", "fall", "autumn"],
    "holiday": ["holiday", "christmas", "xmas", "festive season", "new year", "nye"],
    "festive": ["festive", "celebratory", "party", "celebration"],
    "elevated": ["elevated", "dressy", "upscale", "polished", "refined", "chic", "sophisticated"],
    "playful": ["fun", "playful", "flirty", "cute", "whimsical"],
    "classic": ["classic", "timeless", "traditional", "simple"],
    "romantic": ["romantic", "feminine", "soft", "delicate", "pretty"],
    "preppy": ["preppy", "collegiate", "southern"],
    "long_sleeve_bonus": ["long sleeve", "long-sleeve", "sleeves", "covered arms"],
    "sleeveless_bonus": ["sleeveless", "strappy", "bare arms"],
    "red": ["red", "crimson", "cherry"],
    "green": ["green", "emerald", "kelly"],
    "white": ["white", "ivory", "cream"],
    "black": ["black"],
    "gold": ["gold", "golden"],
    "silver": ["silver"],
}

# Phrases that mean "steer away from", mapped to the tag they discourage.
NEGATIVE_LEXICON: dict[str, list[str]] = {
    "cold_weather": ["avoid heavy winter", "avoid wintery", "not wintery", "nothing wintery",
                     "avoid heavy wintery", "no winter", "avoid winter"],
    "dark": ["avoid dark", "nothing too dark", "not too dark"],
    "sparkle": ["avoid sparkle", "no sequins", "nothing sparkly"],
    "romantic": ["not too girly", "avoid girly", "nothing too girly", "not girly"],
    "playful": ["not too casual", "avoid casual"],
}


def parse_vibe_text(text: str | None) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """Return (positive_tags, discouraged_tags, evidence).

    Negatives are checked first and remove their tag from the positive set, so
    "avoid heavy wintery styles" does not also register 'cold_weather' as a
    positive because it contains the word "wintery".
    """
    if not text:
        return [], [], {}

    lowered = text.lower()
    evidence: dict[str, list[str]] = {}
    discouraged: list[str] = []

    # Pass 1: negations. Record the span so pass 2 can skip it.
    negated_spans: list[tuple[int, int]] = []
    for tag, phrases in NEGATIVE_LEXICON.items():
        for phrase in phrases:
            for m in re.finditer(re.escape(phrase), lowered):
                negated_spans.append(m.span())
                if tag not in discouraged:
                    discouraged.append(tag)
                evidence.setdefault(f"-{tag}", []).append(phrase)

    def inside_negation(span: tuple[int, int]) -> bool:
        return any(span[0] >= s and span[1] <= e for s, e in negated_spans)

    # Pass 2: positives, skipping anything inside a negated phrase.
    positive: list[str] = []
    for tag, keywords in VIBE_LEXICON.items():
        for keyword in keywords:
            pattern = re.compile(r"\b" + re.escape(keyword) + r"\b")
            for m in pattern.finditer(lowered):
                if inside_negation(m.span()):
                    continue
                if tag not in positive:
                    positive.append(tag)
                evidence.setdefault(tag, []).append(keyword)
                break

    positive = [t for t in positive if t not in discouraged]
    return positive, discouraged, evidence


def resolve_event_tags(explicit_tags: list[str], vibe_text: str | None
                       ) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """Merge explicitly supplied tags with tags parsed from free text.

    Explicit tags win: if the person typed a tag directly, a negation found in
    prose does not override it. Unknown tags are dropped rather than silently
    accepted, because a tag outside the vocabulary can never match a garment.
    """
    parsed, discouraged, evidence = parse_vibe_text(vibe_text)

    valid_explicit = [t for t in explicit_tags if t in VIBE_TAGS]
    merged = list(dict.fromkeys(valid_explicit + [t for t in parsed if t in VIBE_TAGS]))
    discouraged = [t for t in discouraged if t not in valid_explicit]

    return merged, discouraged, evidence


def unknown_tags(explicit_tags: list[str]) -> list[str]:
    """Tags outside the controlled vocabulary, surfaced as a run note."""
    return [t for t in explicit_tags if t not in VIBE_TAGS]
