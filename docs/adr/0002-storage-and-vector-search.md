# ADR 002: One Postgres for metadata and vectors, exact search in V1

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** Anna Kate Black

## Context

Retrieval in this system is not pure vector search. Every query applies non-negotiable constraints before similarity matters at all: dress size, price ceiling, availability, and whether the rental account is still active. A size 12 dress that perfectly matches the user's taste has relevance exactly zero.

That makes the access pattern a **filtered** vector search, where the filter is relational and the ranking is geometric. How those two live together is the decision.

Scale matters here and it cuts against intuition. V1 targets roughly 400 dress listings. The realistic ceiling, if every UGA rental account were ingested, is on the order of 10,000 to 20,000 items. The taste corpus is larger but still small: under 2,000 saved products, of which only the dress subset is used. These are numbers where a sequential scan over every vector is a few milliseconds.

## Decision

**A single Postgres database, hosted on Supabase, with the `pgvector` extension.** Vectors live in the same database as the metadata they belong to, and a retrieval query is one SQL statement:

```sql
with eligible as (
  select l.id, l.image_url, l.price_cents, e.vec
  from rental_listings l
  join rental_accounts  a on a.id = l.account_id
  join embeddings       e on e.owner_id = l.id
                         and e.owner_type = 'rental'
                         and e.view = 'image'
                         and e.model_version = $model
  where l.size_normalized = $size
    and l.price_cents    <= $max_price
    and l.is_available
    and a.is_active
    and a.consent_status <> 'opted_out'
)
select id, 1 - (vec <=> $query_vec) as sim
from eligible
order by vec <=> $query_vec
limit 80;
```

**No ANN index in V1.** Exact search over a few thousand filtered rows is fast enough, and exact search has perfect recall by definition. An HNSW index will be built in a branch and benchmarked against exact scan so the tradeoff is measured rather than assumed, but the default path is exact.

**Embeddings live in one polymorphic table** keyed by `(owner_type, owner_id, view, model_version)`. The `model_version` component is load-bearing: it is what turns "compare two embedding models" from a data migration into a `WHERE` clause, which the Phase 5 evaluation depends on.

**Raw source payloads are persisted** in JSONB columns alongside normalized fields, so a change to the normalization logic never requires re-fetching from an external source.

## Alternatives considered

**A dedicated vector database (Pinecone, Weaviate, Qdrant).** Rejected for this scale. These systems earn their complexity above roughly ten million vectors, where index build time, sharding, and memory pressure are real problems. At 20,000 vectors they introduce a genuine architectural defect: metadata and vectors live in separate systems, so a filtered query either over-fetches from the vector store and filters in application code (which silently breaks recall, since the filter can eliminate most of what was returned) or maintains a denormalized copy of the metadata in two places. Choosing one here would be optimizing for resume keywords over correctness.

**FAISS or a local numpy index.** Rejected. Genuinely the fastest option at this scale and the most honest about it, but it provides no persistence story, no filtered query, no join to metadata, and nothing that transfers to a production setting. The learning goals include persistent data and pgvector specifically.

**SQLite with `sqlite-vec`.** Rejected, narrowly. It would work, it is simpler to run locally, and it needs no hosted service. Postgres wins because the project's stated learning goals name Supabase and pgvector, because a hosted database makes the Phase 8 frontend deployment straightforward, and because Postgres is what the user would encounter professionally.

**Building an HNSW index by default.** Rejected in V1. At this row count an approximate index trades recall for latency the system does not need. It also introduces a subtle failure mode worth understanding but not shipping: with a pre-filter, HNSW walks the graph and discards non-matching rows, so it can return fewer than `k` results. `pgvector`'s `hnsw.iterative_scan` setting exists to address exactly this. Knowing about this interaction is valuable; suffering it in V1 is not.

## Consequences

**Positive.** Filter and rank in one statement, with no application-side join and no recall ambiguity. One system to run, back up, and reason about. The `model_version` key makes the evaluation ablations cheap. Being able to say "I benchmarked exact search at N vectors, measured p95, and concluded an ANN index was unjustified, here is the script" is a stronger technical claim than having added an index.

**Negative.** Migrations must be managed by hand as plain SQL files. Supabase's free tier imposes storage and connection limits that a growing catalog could eventually reach. Exact search is O(n) and will not scale past roughly 100,000 vectors, at which point the index decision must be revisited.

**Accepted risk.** Storing multiple embedding model versions multiplies vector storage. At this scale that is megabytes and the flexibility is worth far more than the disk.

## What remains reversible

**Fully reversible:** adding an HNSW or IVFFlat index later is a single `CREATE INDEX` and changes no application code. Swapping embedding models is additive because of `model_version`. Migrating from Supabase to any other Postgres host is a dump and restore.

**Reversible with moderate effort:** moving to a dedicated vector store would require re-homing the vectors and rewriting the retrieval query to a two-phase fetch-then-join. Roughly a day of work, and the trigger for it is a catalog above about 100,000 items, which this project is unlikely to reach.

**Effectively locked:** the choice of relational storage for the catalog itself. Everything from the evaluation harness to the frontend assumes SQL.
