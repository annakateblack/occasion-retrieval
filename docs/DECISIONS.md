# Decision register

Status values:

- **LOCKED** — settled for V1. Changing it invalidates work already done.
- **REVERSIBLE** — the current answer, cheap to change later.
- **PENDING** — not yet decided; blocked on evidence named in the last column.

## Scope

| Decision | Status | Reason | What would change it |
|---|---|---|---|
| V1 serves one user (me) | LOCKED | No second user exists; collaborative signal is unavailable at n=1 | A second real user, which is out of V1 scope |
| Dresses only | LOCKED | Keeps the taste corpus and catalog semantically coherent; sets/jumpsuits blur the attribute schema | Post-V1, once dress-only metrics are published |
| Visual grid is the primary interface, not chat | LOCKED | Users judge dresses by looking; a chat interface would hide the product | Nothing in V1 |
| Evaluation precedes sophistication | LOCKED | Every layer after the baseline claims an improvement that needs a number | Nothing |
| No agents in V1 | LOCKED | The pipeline is a known DAG with no branching uncertainty; agents add nondeterminism and defeat evaluation | A genuine tool-selection problem, which does not exist here |
| No MCP in V1 | REVERSIBLE | One honest future use (exposing retrieval as a tool). ~100 lines, Phase 9+ | Wanting to query the catalog conversationally after V1 ships |

## Architecture

| Decision | Status | Reason | What would change it |
|---|---|---|---|
| Source-adapter interface, snapshot-based (ADR 001) | LOCKED | Both sources are uncertain; the recommendation system must not care | Nothing. Swapping a source is an adapter, not a redesign |
| The save app is a private local snapshot, never a live dependency | LOCKED | No agreement in place; polling buys nothing for weeks-scale data | An explicit decision to contact the save app, then a sanctioned export |
| Postgres + pgvector, one system (ADR 002) | LOCKED | Filtered vector search needs metadata and vectors co-located | A catalog above ~100k items, which is ~5x the realistic ceiling |
| Exact vector search, no ANN index (ADR 002) | REVERSIBLE | Few ms at this row count, perfect recall; `CREATE INDEX` is one line | Measured p95 exceeding ~200ms |
| Python owns all retrieval/ranking/model logic | LOCKED | It is the interesting part and must be readable in the repo | Nothing |
| n8n is control plane only (ADR 004) | LOCKED | Logic in workflow JSON is invisible and untestable | Nothing. This ADR exists to prevent drift |
| n8n arrives in Phase 9, not earlier | REVERSIBLE | Automating an unstable pipeline means building it twice | The pipeline stabilizing early |

## Models and personalization

| Decision | Status | Reason | What would change it |
|---|---|---|---|
| Taste model V1 = top-5 NN (ADR 003) | LOCKED for V1 | Baseline must exist before sophistication can be shown to help | Nothing for V1; later steps are gated on beating it |
| Clustered taste profile | PENDING | Deferred to step 4 | Beating top-k NN + recency on nDCG@10 |
| Attribute PMI component | PENDING | Deferred to step 5 | Beating step 4 |
| Learned fusion weights | PENDING | Deferred to step 5 | Enough labels to fit ~5 params without overfitting |
| Reranking | PENDING | Deferred to step 6 | Beating step 5 on quality per unit cost |
| Dual-view embeddings (image + canonical text) | REVERSIBLE | Separate vectors keep the view weight tunable; concatenation fixes it arbitrarily | Measuring that one view alone matches the pair |
| Image encoder choice | PENDING | Should be decided by the eval, not up front | Phase 4 ablation across 2–3 encoders |
| Structured metadata is never embedded | LOCKED | Numbers have poor embedding geometry; size/price are SQL filters | Nothing |
| Score calibration before any fusion | LOCKED | Raw cosine is not comparable across queries or encoders | Nothing |

## Data and evaluation

| Decision | Status | Reason | What would change it |
|---|---|---|---|
| No Instagram credentials, ever | LOCKED | Hard security boundary set by the user | Nothing |
| Bootstrap catalog from own IG export + per-post oEmbed | REVERSIBLE | Only credential-free path; scope confirmed for single public posts | oEmbed not returning usable image/caption (test pending) |
| Own saved collection is dev-only, labeled biased | LOCKED | Positively selected by construction | Nothing. An unbiased holdout is built separately in Phase 10 |
| Unbiased holdout sampled by account, then posts wholesale | LOCKED | Sampling posts the user chose reproduces the bias | Nothing |
| V1 catalog target ~400 listings, floor 250 | REVERSIBLE | Post-filter pools need ~80–120 candidates/query for nDCG to discriminate | Measured post-filter pool sizes once real size/price distributions are known |
| Graded 0/1/2 labels, pooled across variants | LOCKED | Graded labels enable nDCG; pooling prevents favoring the system that produced the pool | Nothing |
| nDCG@10 primary, P@10 + Recall@120 secondary | REVERSIBLE | nDCG suits graded labels; Recall@120 separates funnel leakage from ranking | Finding a metric that better matches the DM decision |
| Held-out query set (6 of ~22) for final numbers | LOCKED | Fitting and reporting on the same queries is not an evaluation | Nothing |

## Open, blocking nothing yet

| Question | Blocked on | Needed by |
|---|---|---|
| What Instagram oEmbed actually returns (image? caption?) | 5 permalinks from the user | Phase 1b |
| Instagram export field coverage for saved posts | Export delivery (pending) | Phase 1b |
| Final taste snapshot method | User decision on local helper vs manual | Phase 1a |
| Exact V1 catalog size | Both of the above | Phase 5 |
| API budget | User | Phase 3 |
