# Architecture Decision Records

Each ADR records one decision: the context that forced it, what was decided,
what was rejected and why, the consequences including the bad ones, and what
remains reversible.

An ADR is written when a decision is made, not afterward. It is not updated
when the decision changes; a new ADR supersedes it.

| # | Title | Status |
|---|---|---|
| [001](0001-source-adapter-architecture.md) | Isolate every inventory source behind a snapshot-based adapter | Accepted |
| [002](0002-storage-and-vector-search.md) | One Postgres for metadata and vectors, exact search in V1 | Accepted |
| [003](0003-taste-model-sequencing.md) | Start the taste model at top-k nearest neighbor, earn every addition | Accepted |
| [004](0004-orchestration-boundary.md) | n8n is the control plane and holds no business logic | Accepted |
| [005](0005-deterministic-baseline-before-vision.md) | Establish a deterministic personalized baseline before visual enrichment | Accepted |
