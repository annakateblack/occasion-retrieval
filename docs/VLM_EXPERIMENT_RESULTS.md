# First live visual-enrichment experiment

## Executive summary

The live Anthropic vision path worked on every reachable anchor image and
substantially increased the amount of structured visual information available
for those items. Anchor-level ranks generally improved. Full-catalog top-10
nDCG did not change because only 7 of 157 candidates received enrichment.

The correct conclusion is narrow: **the provider and integration path produced
useful directional movement on the treated anchors, while treatment coverage
was too sparse to demonstrate aggregate retrieval improvement.**

## Experiment question

Does filling caption-unknown visual attributes improve event-specific ranking
when the downstream ranker is held fixed?

## Controlled design

| Element | Arm A: caption baseline | Arm B: enriched |
|---|---|---|
| Inventory and event | Same | Same |
| Hard filters | Same | Same |
| Ranker and weights | Same | Same |
| Quality threshold and depth | Same | Same |
| Caption extraction | Deterministic | Deterministic |
| Vision observations | None | Fill unknown visual fields only |

Known caption values were not overwritten. Disagreements remained visible as
conflicts, and transactional facts remained outside vision authority.

## Observed results

| Metric | Result |
|---|---:|
| Inventory candidates | 157 |
| Reachable anchor images attempted | 7 |
| Successful live enrichments | 7/7 |
| Mean visual-attribute coverage | 91.4% |
| Enriched share of inventory | 4.5% |
| Full-catalog top-10 nDCG change | Flat |
| Comparable anchors | 6 |
| Anchors improving in rank | 5 |
| Anchors moving down | 1, by one place |
| Mean rank improvement | +20.5 places |
| Mean score delta | +0.0301 |

“Mean rank improvement” is reported over the six anchors that were comparable
between arms; positive values mean movement toward rank 1. The seventh reachable
image contributed to provider success and coverage but was not part of that
paired rank statistic.

## How to interpret the result

### 1. Provider feasibility: positive

All seven reachable images completed successfully, and 91.4% mean coverage
shows that the structured contract captured most requested visual fields on
this small anchor set. This validates the live request, response validation,
normalization, provenance, and cache path as an operational pipeline.

### 2. Treated-item movement: directionally positive

Five of six comparable anchors improved, with an average improvement of 20.5
ranking positions and a +0.0301 average score change. That is consistent with
the hypothesis that previously unknown visual attributes can affect the fixed
ranker in useful ways.

It is not a causal or population-level estimate. The anchor set is small and
purpose-built, and one item moved down.

### 3. Top-10 retrieval: no demonstrated improvement yet

Top-10 nDCG was flat. This is not reported as a win. With only seven enriched
items among 157 candidates, most of each ranking remained identical between
arms. Sparse treatment limits how much a top-10 aggregate can move, even when
some treated items change substantially.

The result therefore separates two questions:

- **Did the live enrichment path work on the images it received?** Yes, in this
  seven-image feasibility run.
- **Did vision improve catalog-wide top-10 relevance?** Not demonstrated by
  this run.

## Limitations

- Only 7 of 157 candidates were enriched.
- Paired rank movement was available for six anchors.
- Anchors are not a random sample of the catalog.
- The small treated set provides limited power for top-10 metrics.
- High attribute coverage does not by itself prove attribute correctness.
- Several rich visual fields are retained for analysis but are not consumed by
  current scoring.
- Results come from a private development inventory and should not be presented
  as an unbiased population estimate.

## Next experiment

Repeat the same controlled A/B after enriching the full reachable catalog. If
full coverage is impractical, predeclare a stratified sample that provides
substantial enrichment coverage in each event's candidate pool; do not select
items after observing their baseline ranks.

Keep the following fixed:

- inventory snapshot;
- event definitions and relevance labels;
- caption extraction;
- hard filters;
- ranking weights and algorithm;
- quality threshold, retained depth, and tie handling;
- confidence floor and merge policy.

Evaluate:

1. nDCG@10 as the primary catalog-level outcome;
2. attribute coverage and style-score evaluability;
3. top-k overlap across intentionally different events;
4. hard-filter survivor equality;
5. per-anchor rank and score deltas;
6. conflict, low-confidence, unusable-image, and cache-hit rates;
7. cutoff ties using more than ten retained results.

Before running, record the provider/model version, contract fingerprint,
inventory snapshot, treated share, and relevance scale. Report both positive and
flat results, and distinguish provider feasibility from ranking effectiveness.

## Privacy and reporting

The public result contains aggregate counts and metrics only. Images, captions,
usernames, permalinks, shortcodes, row-level outputs, caches, credentials, and
human labels remain in gitignored local storage.
