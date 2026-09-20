# Image-enrichment integration review checklist

Use this checklist before merging any VLM/image-enrichment implementation into `main`.

## 1. Repository fit

- New code belongs under `src/rental_retrieval/enrichment/` or an equally explicit package.
- Existing deterministic retrieval remains usable without image enrichment.
- No Supabase, frontend, save-app, or n8n changes are mixed into this phase.
- Public code has no real Instagram credentials, cookies, raw exports, or private images.

## 2. Visual contract

The visual schema should normalize, at minimum:

- colour family
- brightness / saturation
- metallic
- sparkle / embellishment
- pattern / texture
- fabric sheen
- sleeve length
- neckline
- strap type
- hem length
- silhouette
- visible construction details such as cutout / drape / ruffle
- formality
- seasonal cues
- versatile vs occasion-specific
- classic vs statement

Every field must preserve unknowns. Missing visual evidence must never become a guessed default.

## 3. Provider boundary

The model provider should be replaceable behind one interface.

Required properties:

- deterministic stub for tests
- live provider isolated to one module/method
- model/provider version recorded with outputs
- prompt/schema fingerprint in the cache key
- per-field confidence rather than one record-level confidence
- malformed model output degrades gracefully instead of crashing the whole batch

## 4. Merge policy

Caption-derived transactional facts remain authoritative:

- size
- rental price
- availability
- renter-stated dates / explicit constraints

Image-derived facts may fill visual attributes.

Conflicts must be visible in provenance rather than silently overwritten.

## 5. Ranking integration

The enriched path should call the same ranking machinery as the baseline.

The experiment should avoid changing both:
1. the source of attributes, and
2. the scoring weights / ranking algorithm

in the same comparison.

Otherwise any measured lift is uninterpretable.

## 6. Private real evaluation vs public synthetic fixtures

Public repo:
- synthetic seed fixture only
- synthetic images or no images
- fake shortcodes/permalinks
- deterministic tests

Private local:
- `data/local/eval/`
- real anchor permalinks
- real human relevance labels
- cached image/model outputs if sensitive

The real evaluation file must be gitignored.

## 7. Evaluation gates

Before calling the vision phase successful, record:

- full test-suite count
- attribute coverage before vs after vision
- style-score evaluability before vs after vision
- top-10 overlap for My Tie vs Christmas Cocktail
- nDCG@10 on the real local labelled set
- hard-filter survivor counts before vs after
- ranking tie count at cutoff

A model layer is not an improvement merely because it is multimodal.

## 8. Current baseline facts to preserve

Observed before visual enrichment:

- 157 dress candidates in the current local inventory
- 150 passed hard filters for My Tie
- 10 returned
- caption-only ranking is dominated by length / fit / price
- My Tie vs Christmas Cocktail showed very high overlap
- style attributes are sparsely observable from captions

These are the comparison point for the next experiment.

## 9. Specific code-review questions for the incoming Claude implementation

- Does `baseline.rank(...)` remain callable without enrichment?
- Are enriched attributes optional and backward compatible?
- Does the merge layer preserve `length="unknown"` semantics expected by the event schema?
- Does vision ever override caption price, size, or availability?
- Are event-vibe tokens normalized into the same vocabulary used by `EventContext`?
- Are explanations generated from the same score components that produced the number?
- Are cache files and real labels definitely under ignored local paths?
- Is `is_measured` false for synthetic fixtures and stub providers?
- Do tests include conflict, malformed output, cache invalidation, and unknown handling?

## 10. Merge decision

Merge only if:

- tests pass;
- privacy checks pass;
- deterministic baseline behavior still passes regression tests;
- the real local evaluation is runnable without committing private data;
- ADR 006 accurately describes what was built;
- the vision phase can be disabled without redesigning retrieval.
