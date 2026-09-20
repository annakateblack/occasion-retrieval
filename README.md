# Personalized multimodal retrieval over fragmented social-commerce inventory

A retrieval and ranking system that recommends rental dresses from student-run
Instagram inventory, personalized to a taste profile learned from a user's saved
products rather than from self-description.

**Status:** Deterministic caption baseline complete; provider-neutral visual
enrichment and evaluation integration are prepared on an experimental branch.

## The problem, stated as a systems problem

Personalized, cross-domain, multimodal ranking over a small, noisy,
semi-structured catalog, with implicit positive-only supervision and hard
business constraints.

Three properties make it non-trivial:

**Cross-domain representation transfer.** The taste corpus is clean retail
product photography. The catalog is iPhone mirror selfies in dorm rooms. Naive
image-embedding similarity partly measures "studio photo versus bedroom photo"
rather than style. Mitigating that is the central design problem, not the
retrieval.

**Positive-unlabeled learning.** Saved products say what the user likes. Nothing
says what they rejected. Similarity scores from a positive-only model have no
calibrated meaning in absolute terms.

**Query-conditional personalization.** "My taste" is conditional on occasion. A
single global taste vector recommends going-out clothes for a formal.

## Pipeline

```
SOURCES (snapshot files) → adapters → normalization
                                          ↓
                          VLM attribute + canonical-text extraction
                                          ↓
                          dual-view embeddings (image, canonical text)
                                          ↓
                          Postgres + pgvector
                                          ↓
  event query → parse → hard filter (SQL) → vector retrieval → score fusion
                                          → [rerank] → diversify → results
```

Hard filters run first because a size 12 dress that perfectly matches taste has
relevance exactly zero. Constraints are not preferences.

## Design principles

**Baseline before sophistication.** The first taste model is top-5 nearest
neighbor. Clustering, attribute scoring, learned fusion weights, and reranking
are each gated on beating the previous step on a labeled evaluation set. A step
that does not beat its predecessor is reported as a negative result, not merged.
See [ADR 003](docs/adr/0003-taste-model-sequencing.md).

**Evaluation early.** The labeling tool and metrics harness are built before the
sophisticated personalization, because every layer after the baseline claims an
improvement that needs a number.

**No technology without an architectural reason.** No agents: the pipeline is a
known DAG with no branching uncertainty. No dedicated vector database: at this
scale it would split metadata from vectors and break filtered recall. No ANN
index in V1: exact search over a few thousand filtered rows is a few
milliseconds with perfect recall.

**Orchestration is not reasoning.** n8n schedules, retries, and calls endpoints.
It holds no business logic. If an n8n Code node does anything more than reshape
a JSON payload, that logic belongs in Python and something has gone wrong.
See [ADR 004](docs/adr/0004-orchestration-boundary.md).

## Data handling

This repository processes personal data and third-party content and commits
neither. The project never holds Instagram credentials, cookies, or session
tokens; ingestion uses the user's own official data export and resolution of
individual public post permalinks. Inventory sources are isolated behind a
snapshot-based adapter interface, so no external backend is a runtime
dependency. See [docs/DATA_HANDLING.md](docs/DATA_HANDLING.md).

Results derived from the user's own saved collection are positively biased by
construction and are labeled as development-set results. An unbiased holdout is
sampled by account, not by post.

## Documentation

| | |
|---|---|
| [ADRs](docs/adr/) | Why each major decision was made, and what was rejected |
| [DECISIONS.md](docs/DECISIONS.md) | Locked / reversible / pending register |
| [REPO_STRUCTURE.md](docs/REPO_STRUCTURE.md) | Layout and public/private boundaries |
| [DATA_HANDLING.md](docs/DATA_HANDLING.md) | What may never be committed |
| [PHASE_0_CHECKLIST.md](docs/PHASE_0_CHECKLIST.md) | Current phase exit criteria |
| [VLM_EVALUATION_PLAN.md](docs/VLM_EVALUATION_PLAN.md) | Controlled caption-vs-vision experiment |
| [VLM_INTEGRATION_NOTES.md](docs/VLM_INTEGRATION_NOTES.md) | Incoming implementation handoff and conflicts |

## Setup

```bash
uv sync
cp .env.example .env    # fill in locally; never committed
```

Requires Python 3.12+.
