# VLM integration handoff

This is the single canonical handoff for integrating the incoming image-enrichment work.
It describes the state of `prep-vlm-evaluation` relative to `main`, the adapter boundary,
known conflicts, and the validation order. It does not define a second provider architecture.

## Branch audit: changes that should survive

The audited base is `main` at `3b28f36` and the Work branch head before this QA pass is
`26e78ed`. The branch was five commits ahead and zero behind at that point.

| File | Change to retain in the integrated implementation |
|---|---|
| `README.md` | Experimental VLM status and links to the controlled evaluation documentation |
| `docs/REPO_STRUCTURE.md` | Public/private boundaries plus enrichment and evaluation package locations |
| `docs/VLM_EVALUATION_PLAN.md` | Caption-versus-enriched design, metrics, privacy rules, and gates |
| `docs/VLM_INTEGRATION_NOTES.md` | This canonical handoff and conflict register |
| `docs/IMAGE_ENRICHMENT_INTEGRATION_CHECKLIST.md` | Pointer here; do not maintain a second checklist |
| `docs/adr/0005-deterministic-baseline-before-vision.md` | Deterministic baseline and visual-enrichment gate |
| `docs/adr/0006-provider-neutral-visual-enrichment.md` | One neutral contract and fixed-ranker experiment |
| `docs/adr/README.md` | ADR 005/006 index entries |
| `src/rental_retrieval/retrieval/attribute_provider.py` | Neutral protocol, caption adapter, required-key validation |
| `src/rental_retrieval/schemas/enrichment.py` | Neutral observation and resolution contracts |
| `src/rental_retrieval/retrieval/baseline.py` | Optional provider injection with caption-only default behavior |
| `src/rental_retrieval/schemas/ranking.py` | Per-result attribute provenance and visible conflicts |
| `src/rental_retrieval/eval/io.py` | Private path, measured-data, and explicit-scale gates |
| `src/rental_retrieval/eval/metrics.py` | nDCG, overlap, coverage, evaluability, survivors, and cutoff ties |
| `src/rental_retrieval/eval/experiment.py` | One settings object used for both controlled ranking arms |
| `src/rental_retrieval/eval/__init__.py` | Evaluation package boundary |
| `tests/test_evaluation.py` | Synthetic metric, privacy, and identical-arm-settings regressions |

No Supabase, frontend, save-app, n8n, private Instagram data, live provider, enrichment
orchestration, cache implementation, ranking weights, or scoring algorithm belongs in the
Work-side change set.

## What should come from Claude

Claude's implementation should supply the live model client, image input handling, response
validation, batch/failure behavior, cache implementation, and provider-specific tests. Those
internals may use a richer schema than the ranker. Only one adapter at their boundary should
translate the result into the existing `AttributeResolution` contract.

The incoming implementation should also supply tests for malformed responses, partial model
output, timeout/item failure, cache invalidation, prompt/schema fingerprint changes, unknown
handling, and caption/vision conflicts. Cache and live-response fixtures must be synthetic.

## Concrete adapter plan

1. Keep Claude's provider-specific request/response types internal to its enrichment package.
2. Expose one callable object or closure satisfying `AttributeProvider.__call__(post)`; inject
   image access, client, and cache dependencies when constructing it rather than widening the
   ranker interface.
3. Begin adaptation from `caption_resolution(post)`. Preserve every key in
   `RANKING_ATTRIBUTE_KEYS` and the existing `None`/`"unknown"` sentinels.
4. Convert each accepted model field to an `AttributeObservation` with field-level confidence,
   `source="vision"`, provider, model version, and concise evidence when available.
5. Vision may fill an unknown visual value. When caption and vision have different known visual
   values, keep the caption value for this controlled experiment and append an explicit conflict;
   do not erase either observation.
6. Never let vision supply or override size, rental price, availability, stated dates, or other
   transactional facts. Size and price are outside `AttributeResolution`; availability remains a
   required ranker key but must retain caption authority.
7. Rich fields not consumed by current scoring may remain in `values`/`observations` for coverage
   reporting. They must not change weights or scoring in this experiment.
8. Return one `AttributeResolution`. Do not introduce a visual ranker or pass raw model JSON into
   `baseline.rank`.

## Controlled evaluation contract

Use `run_controlled_arms` with one `ControlledArmSettings` instance. Both arms therefore receive
the same inventory object, profile, event, ranker, weights, quality threshold, and retained depth.
The only intended difference is the optional enriched provider.

For each query, retain privately:

- inventory snapshot identifier and item count;
- provider/model version and prompt/schema fingerprint;
- ranker commit and `ControlledArmSettings`;
- both `RankingRun` outputs;
- the declared relevance scale and measured judgments.

Report nDCG@10, top-k overlap, per-field attribute coverage, style-score evaluability,
hard-filter survivor counts, and cutoff-tie diagnostics. `retained_depth` must exceed 10. A tie
count is complete only when retained results cross the end of the cutoff tie group; otherwise it
must be reported as a lower bound.

## Relevance-label conflict: unresolved by design

The exact conflict is:

- `docs/DECISIONS.md`, **Data and evaluation**, locks “Graded 0/1/2 labels.”
- `docs/VLM_EVALUATION_PLAN.md`, **Relevance labels**, defines 3/2/1/0.
- the public nDCG regression test demonstrates four grades but the metric supports either scale.

Measured label files must now declare `relevance_scale_max` as `2` or `3`; this records the choice
without making it here.

If 0/1/2 is selected, keep the decision-register row, rewrite the VLM plan's label meanings to
three grades, change four-grade synthetic examples, enforce maximum 2 in labeling/export tools,
and relabel rather than mechanically compress any existing 0..3 judgments.

If 0/1/2/3 is selected, update the LOCKED decision-register row and rationale, keep the VLM plan's
four meanings, enforce maximum 3 in labeling/export tools, and version any earlier 0..2 judgments
so mixed-scale aggregates cannot be produced.

## Documentation reconciliation

- ADR 003 defers **VLM reranking**, while ADR 005 inserts **VLM attribute enrichment with the
  same deterministic ranker** before the top-5 NN taste model. ADR 005 supersedes only ADR 003's
  original step numbering; it does not authorize VLM judging or reranking.
- ADR 005 defines the experimental goal. ADR 006 defines the integration boundary. Neither
  authorizes changed scoring weights during the caption-versus-enriched comparison.
- README is an eventual architecture overview, not evidence that its embedding, database, or
  reranking stages are currently implemented.
- `REPO_STRUCTURE.md` shows intended locations. Claude's enrichment package may use a different
  explicit package name, but the adapter into `AttributeResolution` remains singular.

## Likely merge conflicts

| File/area | Why it may conflict | Integration rule |
|---|---|---|
| `attribute_provider.py` | Claude may add its own protocol or result type | Keep this protocol; adapt Claude's type |
| `schemas/enrichment.py` | Claude may define richer output | Keep richer types internal; extend only for neutral metadata |
| `baseline.py` | Claude may call enrichment directly | Preserve optional injection and caption-only default; no client/cache imports |
| `schemas/ranking.py` | Claude may add provider metadata | Preserve provenance/conflicts; avoid provider-specific fields |
| evaluation tests | Claude may change settings by arm | Route comparisons through `run_controlled_arms` |
| cache paths | Provider defaults may write beside source | Redirect real output under `data/local/enrichment/` |
| label schema | Claude may assume either scale | Require a decision and `relevance_scale_max` before measured evaluation |

## Exact post-integration validation order

1. Confirm the branch is `prep-vlm-evaluation`, record its head, and unpack Claude's ZIP outside
   the repository for review.
2. Inventory the ZIP; reject private images, exports, real handles, permalinks, captions, labels,
   credentials, generated caches, and changes to excluded systems.
3. Copy provider internals first without wiring them into ranking. Keep dependencies and paths
   isolated to the enrichment package.
4. Add the single adapter described above. Resolve schema differences at that boundary.
5. Reconcile provider-specific tests and run them with synthetic inputs only.
6. Run the existing caption-only regression suite before enabling the enriched arm.
7. Run controlled synthetic arms with one settings object, retained depth greater than 10, and
   unchanged weights/thresholds. Confirm identical candidate counts and hard-filter behavior.
8. Run malformed-output, partial-output, timeout, unknown, conflict, and cache-invalidation tests.
9. Run full `pytest` and `ruff check .` in the locked environment.
10. Inspect the tracked tree and diff for private identifiers/data and verify cache/label/image
    paths are ignored.
11. Re-read ADR 006 against the actual implementation and update only factual drift.
12. After the relevance-scale decision, run the private local experiment and publish only
    aggregate, non-identifying metrics.
13. Review the final diff against `main`; do not merge until every blocker below is cleared.

## Merge blockers

- Any caption-only baseline regression.
- Any live provider that cannot be disabled.
- A second provider/ranker architecture or provider-specific JSON reaching scorers.
- Changed weights, filters, or threshold between arms.
- Missing/guessed unknown values or silent conflict resolution.
- Vision authority over transactional facts.
- Real data or caches outside ignored local paths.
- Human labels without an explicit relevance scale.
- A cutoff-tie count presented as complete when retained rows do not cross the tie group.
