# ADR 004: n8n is the control plane and holds no business logic

- **Status:** Accepted
- **Date:** 2026-09-19
- **Deciders:** Anna Kate Black

## Context

The system has recurring operational work at its edges: ingesting a new snapshot, enriching newly submitted listings, re-verifying which rental accounts are still active, and rebuilding the taste profile when the corpus changes. This work is scheduled or event-driven, needs retries, and needs someone to be told when it fails.

n8n is a visual workflow automation tool that does these things well. It is also explicitly on this project's learning goals list, which is a legitimate reason to include a tool but not a licence to misuse it.

The risk is specific and worth naming. n8n workflows are JSON blobs. They produce unreadable git diffs, have no meaningful unit-testing story, and are slower to debug than a stack trace. Every piece of logic placed inside an n8n Code node is a piece of logic that is not in the repository where a reader can evaluate it. For a project whose purpose is demonstrating engineering judgment, logic hidden in a workflow JSON is worse than no automation at all.

An honest assessment: for this system's scale, a cron job plus a Python script plus a jobs table would be simpler, more testable, and arguably more impressive to an engineer reading the repo. n8n is included because it is a learning goal and because it genuinely fits the heterogeneous-glue problem at the ingestion edge, not because the architecture demands it.

## Decision

**n8n is the control plane. Python is the reasoning engine. The boundary is an HTTP call.**

n8n is permitted to: receive webhooks, run schedules, validate payload shape, call FastAPI endpoints, retry with backoff, fan out over lists, route on HTTP status, and send failure notifications.

n8n is **not** permitted to: compute embeddings, perform retrieval, rank or rerank, call model providers, transform domain data, or implement any conditional logic that depends on the meaning of the data rather than its shape.

The operational test, to be quoted in the repository README:

> If an n8n Code node does anything more than reshape a JSON payload, that logic belongs in Python and something has gone wrong.

Every workflow is **exported to `orchestration/workflows/*.json` and committed**, with a companion markdown file explaining each node. A workflow that exists only in a hosted n8n instance is not portfolio work and is not reproducible.

Ownership by workflow:

| Workflow | Owner | Reasoning |
|---|---|---|
| Listing submission webhook | n8n | Receive, validate shape, dedup check, call API. Pure glue. |
| Scheduled account re-verification | n8n | Schedule, rate limit, fan out, retry, alert. Textbook fit. |
| Snapshot ingestion trigger | n8n | Detect new file, call API. Glue. |
| Nightly taste-profile rebuild | n8n triggers, **Python computes** | The cron belongs in n8n, the model does not. |
| Attribute extraction | **Python** | Prompt versioning, schema validation, retry on malformed output, cost tracking. |
| Embedding generation | **Python** | Batching, model pinning, idempotent upserts. |
| Retrieval, fusion, ranking | **Python** | Latency-critical and the intellectual core of the project. |
| Evaluation harness | **Python** | Must be scriptable, diffable, reproducible. |

**n8n is introduced in Phase 9, after the Python pipeline is stable.** Automating a pipeline whose shape is still changing means building the automation twice.

## Alternatives considered

**Cron plus a Python script plus a jobs table.** The simplest correct answer and a close call. Rejected because n8n is an explicit learning goal, because the ingestion edge genuinely involves gluing heterogeneous triggers, and because n8n's execution history provides operational visibility that would otherwise need to be built.

**Airflow or Prefect.** Rejected as substantially heavier than the problem. These earn their complexity with large DAGs, backfills, and data-dependency management. This system has four thin workflows.

**Celery or RQ.** Rejected because they solve a different problem. They are task queues for application-level background work, not schedulers for cross-system glue. If the extraction pipeline later needs real queueing, a queue can be added inside the Python service without touching this decision.

**Putting the full pipeline in n8n.** Rejected firmly. This is the single worst option available. It would hide the project's most interesting code inside workflow JSON, make evaluation impossible to run reproducibly, and invert the thing the repository is meant to demonstrate.

## Consequences

**Positive.** Core logic stays in the repository, readable and testable. Scheduling, retries, and alerting arrive without being written. The boundary is stated explicitly, which is itself evidence of judgment. The FastAPI endpoints n8n calls are the same ones the frontend uses, so the contract is exercised twice.

**Negative.** Two runtimes to operate and understand. Workflow JSON diffs are noisy in code review. n8n becomes an operational dependency for ingestion, though not for retrieval, so its failure degrades freshness rather than availability. Self-hosting requires Docker.

**Accepted risk.** The temptation to solve "just this one small thing" in a Code node is constant and the drift is gradual. Mitigated by the stated test above and by a Phase 10 review of every committed workflow against it.

## What remains reversible

**Fully reversible.** n8n is strictly additive. Every workflow it runs is an HTTP call to an endpoint that exists independently and can be invoked by cron, by a script, or by hand. Removing n8n entirely means writing a scheduler, and nothing about the application changes.

The reverse is not true, which is the point: had business logic been placed inside n8n, extracting it later would mean rewriting it. This ADR exists primarily to prevent that.
