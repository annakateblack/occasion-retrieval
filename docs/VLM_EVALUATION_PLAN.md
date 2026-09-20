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

- 3 = strong fit for the event
- 2 = plausible
- 1 = weak but possible
- 0 = bad fit

Primary metric: **nDCG@10**.

Secondary diagnostics:
- top-10 overlap across intentionally different event queries
- attribute coverage by field
- fraction of top-10 results with usable style evidence
- hard-filter survivor count
- ranking ties at the cutoff

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
