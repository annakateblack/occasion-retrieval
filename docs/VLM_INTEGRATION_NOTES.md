# VLM integration notes

This is the handoff for reviewing and adapting the incoming image-enrichment
implementation. It records repository seams and conflicts; it does not assume a
specific model provider.

## Ready integration points

- `retrieval.attribute_provider.AttributeProvider` is the only interface the ranker needs.
- `schemas.enrichment.AttributeResolution` carries resolved values, observations, and conflicts.
- `baseline.rank(...)` still defaults to the deterministic caption arm.
- `eval.metrics` compares `RankingRun` outputs without importing provider code.
- `eval.io.load_private_judgments(...)` refuses to load human labels outside
  `data/local/eval/` and requires `is_measured=true`.

## Expected incoming-code adaptation

1. Keep live model/API logic in one enrichment provider module.
2. Convert every model field into an `AttributeObservation`; retain provider
   and model version and include the prompt/schema fingerprint in the cache key.
3. Merge those observations with `caption_resolution(post)`.
4. Preserve all eight required ranker keys. Missing visual evidence remains
   `None` or `"unknown"`; it is not a negative class.
5. Return conflicts explicitly. Vision may fill visual gaps but must not
   override transactional size, rental price, availability, or stated dates.
6. Run both arms with identical inventory, profile, event, weights, threshold,
   and `top_k`. Use `top_k > 10` when measuring ties at rank 10.

## Likely integration conflicts

| Area | Current repository | Risk in incoming implementation | Resolution |
|---|---|---|---|
| Attribute shape | Flat values plus separate observations | Raw provider JSON reaches scorers | Adapt once at the provider boundary |
| Unknowns | `None`; length/availability use `"unknown"` | False/empty/default becomes a real negative | Preserve existing sentinels exactly |
| Authority | Caption owns transactional facts | Vision overwrites price, size, availability | Reject those overrides in merge policy |
| Confidence | Per-field | One record-level score | Retain confidence per observation |
| Cache identity | Not implemented here | Stale results after prompt/schema edits | Key by image hash + provider/model + prompt/schema fingerprint |
| Failure mode | Baseline is offline and deterministic | One malformed response aborts a batch | Validate per item, record failure, continue |
| Tie metric | Requires results below cutoff | `top_k=10` hides boundary ties | Evaluate with at least 11, preferably full survivor pool |
| Relevance scale | Decision register says 0/1/2; VLM plan says 0/1/2/3 | Labels become incomparable | Resolve before the first human-labeling pass |
| Visual vocabulary | Ranker currently uses a smaller subset | New fields appear measured but cannot affect ranking | Report coverage separately; add scorers only in later experiments |

## Evaluation run contract

For each query and arm, retain:

- inventory snapshot identifier and item count;
- provider/model version and prompt/schema fingerprint;
- ranker commit, weights, threshold, and requested depth;
- `RankingRun` output;
- aggregate metrics only in public artifacts.

Do not publish raw images, real permalinks, usernames, captions, labels, cached
model responses, or row-level outputs derived from the private catalog.

## Merge blockers

- Any baseline regression.
- Any provider that cannot be disabled.
- Missing or guessed unknown values.
- Silent caption/vision conflict resolution.
- Real data outside ignored local paths.
- Human labels created before the relevance-scale conflict is settled.
- Reported cutoff ties from a run truncated exactly at the cutoff.

