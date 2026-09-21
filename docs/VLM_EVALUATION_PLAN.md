# Visual enrichment evaluation plan

This document defines the experiment that follows the deterministic personalized baseline.

## Question

Does adding structured visual attributes improve event-specific rental-dress ranking over the caption-only baseline?

## Baseline already established

The current baseline:
- parses the user's official Instagram export locally;
- classifies and normalizes rental listings;
- applies explicit hard filters;
- ranks with interpretable fit, event, price, seasonality, style, and availability components.

The baseline's main observed failure is insufficient garment observability. Caption text is good for transactional facts but sparse for visual style.

## Controlled experiment

Keep the ranker fixed.

Change only the source of garment attributes:

**Arm A — caption-only**
- current deterministic extraction

**Arm B — caption + vision**
- caption extraction remains intact
- a VLM fills currently unknown visual attributes
- conflicts are preserved and flagged rather than silently overwritten

This design lets us attribute any ranking improvement to better visual coverage rather than to a simultaneous scoring rewrite.

## Visual attributes to extract

The first visual contract should include:
- colour family
- brightness / saturation
- metallic
- sparkle / sequin / embellishment
- pattern
- fabric sheen / visible texture
- sleeve length
- neckline
- strap type
- hem length
- silhouette
- cutout / drape / ruffle detail
- formality
- seasonal cues
- versatile vs occasion-specific
- classic vs statement

Each field should support:
- normalized value
- confidence or uncertainty state
- source = vision
- optional short evidence note

## Evaluation queries

The local private evaluation set should contain multiple distinct event intents, including:
- September bright cocktail / My Tie
- Christmas Cocktail
- Parents Weekend formal
- generic date night
- winter date night

Real user labels stay under `data/local/` and must never be committed. Public tests use synthetic fixtures only.

## Relevance labels

Use graded relevance:

- 2 = strong fit for the event
- 1 = plausible fit
- 0 = weak or not relevant

This is the locked 0/1/2 scale in `DECISIONS.md` and the scale declared by the
existing real anchor file. Existing labels are not rewritten or rescaled:
`strong -> 2`, `plausible -> 1`, and both `weak` and `not_relevant -> 0`.
Those two zero-grade words remain distinct for error analysis even though nDCG
treats them equally. The generic metric can evaluate a future label set that
explicitly declares a maximum of 3, but such a set must not be mixed with or
silently substituted for the current labels.

Primary metric: **nDCG@10**.

Report it in two views:

- **full-qrel nDCG@10** uses every label, including positive anchors absent
  from the ranked inventory. This exposes candidate-generation or recall loss.
- **reachable-qrel nDCG@10** uses only labels whose items are in the inventory
  presented to the ranker. This isolates ranking quality among candidates that
  could actually appear.

The report must show both. Reachable-qrel nDCG must use persisted inventory
coverage from the experiment, never infer reachability from returned ranks.

Secondary diagnostics:
- top-10 overlap across intentionally different event queries
- attribute coverage by field
- fraction of top-10 results with usable style evidence
- hard-filter survivor count
- ranking ties at the cutoff

Tie diagnostics require retaining results below the reporting cutoff. An
evaluation run truncated to exactly 10 cannot establish whether rank 10 is tied
with rank 11. If the last retained row is still tied with rank 10, the reported
tie count is a lower bound; retain more rows until the score changes.

Visual enrichment is fill-only, but `length` is also consumed by the hard
filter. Filling an unknown caption-derived length from vision can therefore
change whether an item survives filtering. Every report must show the enriched
minus caption-only hard-filter survivor delta; a non-zero delta requires item
level review and is not automatically a gain or regression.

Published metrics are gated. By default the reporting command refuses to emit
metrics unless the anchor set is real and the recorded provider is live.
Synthetic or stub harness output requires an explicit unmeasured override and
must remain labelled **NOT MEASURED**.

## Success criteria

The visual arm should not be considered better merely because it uses a model.

It should demonstrate:
- materially higher style-attribute coverage;
- lower top-10 overlap for event pairs that should differ;
- improved nDCG@10 on the labelled query set;
- no regression in hard-filter correctness;
- no hidden defaulting of unknown attributes.

## Failure analysis

If the visual arm does not improve ranking, record the negative result and inspect:
- image resolution quality;
- attribute extraction accuracy;
- whether the scoring vocabulary is too coarse;
- whether the event schema is under-specified;
- whether direct image embeddings are a better next experiment.

## Privacy

No raw Instagram images, private exports, personal profile, real permalinks, or real human relevance labels belong in Git.

Public repo artifacts should contain only:
- schemas
- provider interfaces
- synthetic fixtures
- evaluation code
- aggregate metrics that do not expose private content

For screenshots or interviews, use redacted reporting. It replaces shortcodes
with stable `anchor_NNN` identifiers and excludes captions, usernames,
permalinks, label notes, and raw relevance labels.
