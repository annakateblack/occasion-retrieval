"""Transparent, multi-signal classification of saved posts.

No single regex decides anything. Each signal is a named rule with a weight;
the fired signal names are recorded on every record so a classification can
always be explained and audited after the fact.

Two passes:

  Pass 1 (post level)    score each caption from its own evidence
  Pass 2 (account level) an account whose posts repeatedly show rental language
                         is a rental source, which lets a thin caption from that
                         account be promoted from 'uncertain' to
                         'rental_source_candidate' instead of being lost

Pass 2 exists because inventory posts are often terse. The account is the
strongest context available, and it is the signal a human uses too.

PUBLIC-SAFE. Weights and thresholds are tunable constants; no account handles
or captions appear here.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from rental_retrieval.schemas.instagram import Classification, InstagramSavedPost

# --- Signals -----------------------------------------------------------------
# Weight reflects how much the signal DISCRIMINATES rental inventory from
# ordinary shopping content, not how common it is. '$' is common in both, so it
# is worth little. 'dry clean' is almost exclusive to rentals, so it is worth a
# lot despite being rarer.

SIGNAL_WEIGHTS: dict[str, int] = {
    "rent_explicit_phrase": 4,   # "for rent", "available to rent", "renting out"
    "rent_price_context": 4,     # a money amount sitting next to rental language
    "rent_word": 3,              # rent / rental / renting anywhere
    "dryclean": 2,               # near-exclusive to rental listings
    "size_labelled": 2,          # "size 4", "size S"
    "retail_vs_rental": 2,       # both a retail and a rental price present
    "availability_state": 1,     # booked / taken / available
    "dm_to_claim": 1,            # DM / inquire
    "garment_word": 1,           # dress, gown, set
    "money_amount": 1,           # bare $
    "measurements": 1,           # bust / waist / length
}

SIGNAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "rent_explicit_phrase": re.compile(
        r"\b(for rent|available to rent|rent it|renting out|rent out|up for rent|to rent)\b", re.I
    ),
    "rent_word": re.compile(r"\b(rent|rents|renting|rental|rentals)\b", re.I),
    "rent_price_context": re.compile(
        r"(?:\$\s?\d{1,4}[^.\n]{0,25}\b(?:to rent|rental|rent)\b)"
        r"|(?:\brent(?:al)?\s*(?:price|fee)?\s*[:\-]?\s*\$\s?\d{1,4})",
        re.I,
    ),
    "dryclean": re.compile(r"\b(dry[\s-]?clean(?:ing|ed)?)\b", re.I),
    "size_labelled": re.compile(
        r"\bsizes?\s*[:\-]?\s*(?:\d{1,2}|xxs|xs|s|m|l|xl|xxl|small|medium|large)\b", re.I
    ),
    "retail_vs_rental": re.compile(
        r"\b(retail|retails? for|originally|msrp)\b", re.I
    ),
    "availability_state": re.compile(
        r"\b(available|unavailable|booked|taken|reserved|pending|on hold)\b", re.I
    ),
    "dm_to_claim": re.compile(r"\b(dm|d\.m\.|message me|inquire)\b", re.I),
    "garment_word": re.compile(
        r"\b(dress(?:es)?|gown|maxi|midi|skirt|top|set|jumpsuit|romper)\b", re.I
    ),
    "money_amount": re.compile(r"\$\s?\d{1,4}"),
    "measurements": re.compile(r"\b(bust|waist|hips?|length|inseam)\b", re.I),
}

# Signals that constitute *direct* evidence of renting, as opposed to evidence
# that this is merely a fashion post. At least one is required for the strongest
# class, so a well-described dress someone simply bought never becomes inventory.
DIRECT_RENT_SIGNALS = frozenset(
    {"rent_explicit_phrase", "rent_word", "rent_price_context", "dryclean"}
)


@dataclass(frozen=True)
class ClassifierConfig:
    """Every threshold in one place, so tuning is visible and diffable."""

    listing_min_score: int = 6
    """Score at or above which a post with direct rent evidence is a listing."""

    uncertain_min_score: int = 3
    """Score at or above which a post is at least 'uncertain' rather than other_shop."""

    account_min_posts: int = 2
    """An account needs this many posts in the corpus before its behavior counts."""

    account_min_rental_ratio: float = 0.5
    """Share of an account's posts showing direct rent evidence to call it a source."""

    candidate_min_score: int = 2
    """Minimum score for a post from a rental account to be a source candidate."""

    signal_weights: dict[str, int] = field(default_factory=lambda: dict(SIGNAL_WEIGHTS))


DEFAULT_CONFIG = ClassifierConfig()


def score_caption(caption: str | None, config: ClassifierConfig = DEFAULT_CONFIG
                  ) -> tuple[int, list[str]]:
    """Return (score, fired_signal_names) for one caption."""
    if not caption:
        return 0, []
    fired = [name for name, pattern in SIGNAL_PATTERNS.items() if pattern.search(caption)]
    score = sum(config.signal_weights.get(name, 0) for name in fired)
    return score, sorted(fired)


def classify_post(score: int, signals: list[str], *, account_is_rental_source: bool,
                  config: ClassifierConfig = DEFAULT_CONFIG) -> Classification:
    """Decide one post's class from its score, its signals, and its account.

    The ordering matters:

    1. Strong score AND direct rent evidence -> rental_listing.
       Both are required. Score alone would promote a detailed shopping post
       that happens to mention a dress, a size and a price.

    2. Direct evidence but a weaker score -> uncertain, not listing.
       Preserving uncertainty is the point; these are reviewed, not assumed.

    3. No direct evidence, but the account is a known rental source and the post
       carries some substance -> rental_source_candidate.

    4. Some signal but nothing above -> uncertain.

    5. Nothing -> other_shop.
    """
    has_direct = bool(DIRECT_RENT_SIGNALS.intersection(signals))

    if has_direct and score >= config.listing_min_score:
        return "rental_listing"
    if has_direct:
        return "uncertain"
    if account_is_rental_source and score >= config.candidate_min_score:
        return "rental_source_candidate"
    if score >= config.uncertain_min_score:
        return "uncertain"
    return "other_shop"


def identify_rental_accounts(posts: list[InstagramSavedPost],
                             config: ClassifierConfig = DEFAULT_CONFIG) -> set[str]:
    """Pass 2. Accounts whose posts repeatedly carry direct rent evidence.

    Requires both a minimum post count and a minimum ratio. The count guard
    stops one rental post from an otherwise ordinary account making that whole
    account a rental source.
    """
    total: dict[str, int] = defaultdict(int)
    direct: dict[str, int] = defaultdict(int)

    for p in posts:
        if not p.owner_username:
            continue
        total[p.owner_username] += 1
        if DIRECT_RENT_SIGNALS.intersection(p.classification_signals):
            direct[p.owner_username] += 1

    return {
        user
        for user, n in total.items()
        if n >= config.account_min_posts
        and (direct[user] / n) >= config.account_min_rental_ratio
    }


def classify_all(posts: list[InstagramSavedPost],
                 config: ClassifierConfig = DEFAULT_CONFIG) -> list[InstagramSavedPost]:
    """Run both passes over a corpus, in place, and return it.

    Pass 1 must complete for every post before pass 2 runs, because account
    behavior is computed from post-level signals.
    """
    # Pass 1: post-level signals (scores were set during normalization, but we
    # recompute here so the classifier is usable standalone and testable).
    for p in posts:
        score, signals = score_caption(p.raw_caption, config)
        p.classification_score = score
        p.classification_signals = signals

    # Pass 2: account aggregation, then final assignment.
    rental_accounts = identify_rental_accounts(posts, config)
    for p in posts:
        p.account_is_rental_source = bool(
            p.owner_username and p.owner_username in rental_accounts
        )
        p.classification = classify_post(
            p.classification_score,
            p.classification_signals,
            account_is_rental_source=p.account_is_rental_source,
            config=config,
        )
    return posts
