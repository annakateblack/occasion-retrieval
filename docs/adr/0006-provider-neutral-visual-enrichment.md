# ADR 006: Keep visual enrichment behind a provider-neutral attribute contract

- **Status:** Accepted
- **Date:** 2026-09-20
- **Deciders:** Anna Kate Black

## Context

The deterministic baseline extracted caption attributes inside `baseline.rank()`.
That made the default path simple, but it left no clean way to compare caption
attributes with image-enriched attributes while holding filters, scoring, and
sorting fixed. A vision implementation could otherwise fork the ranker or
discard its confidence and conflict metadata to fit the old flat dictionary.

## Decision

The ranker accepts an optional `AttributeProvider`. Providers return an
`AttributeResolution` containing:

- the flat normalized values consumed by the existing scorers;
- per-field observations with source, confidence, evidence, provider, and model version;
- explicit conflicts retained for review.

The default provider wraps the existing deterministic caption rules. Calling
`baseline.rank(...)` without a provider remains the caption-only baseline.

An image implementation should merge caption and vision observations before
calling the ranker. It must return every required ranking field, preserve
unknowns, and leave size, price, and availability under caption/transactional
authority. The ranker validates the contract and fails fast on an incomplete
provider rather than inventing defaults.

## Evaluation boundary

The evaluation package consumes `RankingRun` outputs from either arm. It owns
nDCG, attribute coverage, style-score evaluability, overlap, survivor counts,
and cutoff-tie diagnostics. It does not call a model and does not know which
provider produced a run.

Real judgments load only from `data/local/eval/`. Public tests construct
synthetic labels in memory and keep `is_measured=false`.

## Consequences

**Positive.** Claude's incoming implementation can supply a provider without
rewriting the baseline. Both arms share the same hard filters, scorers, weights,
sort order, and explanation logic. Provenance survives through ranked output.

**Negative.** Providers must adapt richer model output to the normalized
contract. Adding a new attribute that affects ranking still requires an
explicit scorer change and a new controlled experiment.

**Accepted risk.** An enriched provider can choose a poor conflict-resolution
policy. Conflicts are therefore surfaced in output and flags rather than being
silently erased.

## Rejected alternatives

**A second visual ranker.** Rejected because drift in filters or weights would
make the arm comparison uninterpretable.

**Passing raw model JSON into scoring.** Rejected because provider-specific
schemas would leak through the entire retrieval package.

**Replacing the deterministic extractor.** Rejected because the baseline must
remain independently runnable and reproducible.

