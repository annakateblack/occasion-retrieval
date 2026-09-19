# ADR 001: Isolate every inventory source behind a snapshot-based adapter

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** Anna Kate Black

## Context

This system draws on two categories of input, and neither is a stable API we control.

The **taste corpus** comes from a fashion save app. The data is the user's own and is marked public on the platform, but there is no documented public API and no agreement in place covering programmatic access. Building the recommendation system on top of an undocumented backend would couple the project's core value to a vendor surface that can change without notice, and would publish integration details that have not been cleared for publication.

The **rental catalog** comes from UGA student-run Instagram accounts. There is no sanctioned bulk-read path for accounts the user does not control. Meta's export gives the user their own saved-post pointers; individual public posts can be resolved one at a time. Any design that assumes broad programmatic access to Instagram will not survive contact with reality.

Both sources are therefore uncertain, and both are likely to change during the project's life. The parts of the system that are actually interesting (embeddings, retrieval, ranking, evaluation) have no reason to know about either.

## Decision

Every source is reduced to a **file-based snapshot** and parsed by an **adapter** that emits normalized records. No component downstream of the adapter layer knows where data came from.

```
  source (external)          snapshot (local file)        adapter              normalized
  ─────────────────          ─────────────────────        ───────              ──────────
  Save-app profile    ──▶    data/local/taste_*.json──▶ TasteAdapter──▶  RawTasteItem
  Instagram export    ──▶    data/local/saved_posts.json ─▶ IGExportAdapter ──▶ RawDressListing
  oEmbed resolution   ──▶    data/local/oembed_cache/   ──▶ IGOembedAdapter ──▶ RawDressListing
  manual / form       ──▶    data/local/manual.csv     ──▶ ManualAdapter   ──▶  RawDressListing
```

Three rules make this real rather than decorative:

1. **An adapter is a pure function from a local file to a list of normalized records.** It performs no network I/O. This makes every adapter trivially testable against a committed fixture and means the test suite never touches an external service.
2. **Anything that touches a live account lives in `tools/private/` and is gitignored.** Snapshot production is a separate, manual, user-run step, not part of the application. The application never authenticates to anything on the user's behalf.
3. **Snapshots are append-only and timestamped.** `taste_snapshot_2026-09-19.json` is never overwritten. Re-running the pipeline against an older snapshot must reproduce older results, which is a precondition for the evaluation work in Phase 5.

The normalized contract is defined in `src/rental_retrieval/schemas/raw.py` and is the only thing the rest of the system imports.

## Alternatives considered

**Direct live API integration with the save app.** Rejected. It couples the recommendation system to an undocumented endpoint, creates an availability dependency for a system that has no need for real-time data, and would publish vendor implementation details in a public repository before that decision has been made deliberately. Taste changes on the order of weeks; polling buys nothing.

**Instagram scraping at account scale.** Rejected. It violates platform terms, breaks unpredictably against anti-automation measures, creates a maintenance burden with no intellectual payoff, and would appear in a public portfolio repository as `instagram_scraper.py`. The cost of being seen to have built it exceeds the value of the data.

**Bespoke end-to-end pipeline per source.** Rejected. It is faster for the first source and worse for every source after. With no shared normalized type there is no swap path, and the evaluation harness would have to special-case provenance.

**A plugin registry with dynamic adapter discovery.** Rejected as premature. Four adapters do not justify a registry. A dictionary mapping a `source_type` string to a callable is sufficient and readable.

## Consequences

**Positive.** The recommendation system is testable without network access. Adding a sanctioned export from the save app later is one new adapter and zero changes downstream. Provenance is explicit on every row via `source_type`, which the evaluation harness needs in order to report on biased and unbiased subsets separately. Private integration details stay out of the public repository by construction rather than by discipline.

**Negative.** Snapshots go stale and refreshing them is a manual step the user must remember. There is one extra layer of indirection between source and database. Snapshot files consume local disk that grows over time.

**Accepted risk.** A snapshot taken today and a catalog built next month can drift. Mitigated by recording `snapshot_taken_at` on every ingested row so staleness is visible rather than silent.

## What remains reversible

Nearly everything. The adapter interface is an implementation detail behind a stable normalized type. Switching the save app from snapshot-based to a sanctioned live API means writing a new adapter that produces the same `RawTasteItem` records; no schema change, no retrieval change, no evaluation change. Adding sources is additive.

**Not reversible without cost:** the decision to normalize at ingest rather than at query time. If the normalized schema turns out to be lossy for some source, re-ingestion from snapshots is required. This is mitigated by persisting the full raw record alongside the normalized fields (see ADR 002 and the `raw_payload` JSONB columns in the schema), so re-normalization never requires re-fetching from the source.
