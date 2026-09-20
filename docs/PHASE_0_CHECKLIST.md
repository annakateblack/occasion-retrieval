# Phase 0 checklist

Phase 0 ends with a clean repository, documented architecture, a schema that
runs, a working environment, and validated assumptions about both data sources.
No embeddings, no retrieval, no frontend.

Each item has an explicit pass condition. "Looks done" is not a pass condition.

## A. Repository

- [ ] **A1** Repo created on GitHub, private or public per decision, cloned locally.
      *Pass:* `git log` shows commits; `git remote -v` shows origin.
- [ ] **A2** Directory structure matches `docs/REPO_STRUCTURE.md`.
      *Pass:* every directory exists and every empty one has a `.gitkeep`.
- [ ] **A3** `.gitignore` in place and verified.
      *Pass:* create `data/local/probe.json`, run `git status`, confirm it is not listed. Delete the probe.
- [ ] **A4** README states what the project is in systems terms, not "AI dress finder."
      *Pass:* a reader who knows nothing can say what problem it solves and what the pipeline stages are.
- [ ] **A5** License chosen and committed.
      *Pass:* `LICENSE` exists. MIT unless there is a reason otherwise.

## B. Architecture documentation

- [ ] **B1** ADR 001 (source adapters) committed.
- [ ] **B2** ADR 002 (storage and vector search) committed.
- [ ] **B3** ADR 003 (taste model sequencing) committed.
- [ ] **B4** ADR 004 (orchestration boundary) committed.
      *Pass for B1–B4:* each ADR has context, decision, alternatives with reasons for rejection, consequences including negatives, and a reversibility section. An ADR with no negative consequences listed is incomplete.
- [ ] **B5** `docs/DECISIONS.md` committed with every row carrying a status and a change trigger.
- [ ] **B6** Architecture diagram committed (ASCII is fine; a rendered one can come later).
      *Pass:* the diagram shows offline and online planes separately and marks where n8n does and does not sit.

## C. Schema

- [ ] **C1** `db/schema/001_initial_schema.sql` committed.
- [ ] **C2** Schema executes against a real Postgres with `vector` available, with no errors.
      *Pass:* `psql -f db/schema/001_initial_schema.sql` returns clean. This is the item most likely to fail silently if skipped.
- [ ] **C3** Every table has a one-line comment explaining why it exists.
- [ ] **C4** Raw-plus-normalized pairs present where normalization is lossy.
      *Pass:* `size_label`/`size_normalized`, `name_raw`/`name_normalized`, `raw_caption`, `raw_query`, `raw_payload` all present.
- [ ] **C5** `model_version` participates in the `embeddings` uniqueness constraint.
      *Pass:* inserting two rows differing only in `model_version` succeeds.

## D. Environment

- [ ] **D1** Python 3.12 installed and on PATH.
      *Pass:* `python --version` prints 3.12.x.
- [ ] **D2** `uv` installed.
      *Pass:* `uv --version` prints a version.
- [ ] **D3** Virtual environment created and dependencies resolve.
      *Pass:* `uv sync` completes; `uv run python -c "import pydantic"` succeeds.
- [ ] **D4** Git configured with name and email.
      *Pass:* `git config user.name` and `git config user.email` both return values.
- [ ] **D5** `.env` created locally from `.env.example`, and confirmed untracked.
      *Pass:* `git check-ignore .env` prints `.env`.
- [ ] **D6** Editor set up with the project interpreter selected.

## E. Source assumption validation

This section is the real content of Phase 0. Everything above is scaffolding.

- [ ] **E1** Instagram data export requested.
      *Pass:* confirmation received from Meta. **Do this first; it has multi-day latency.**
- [ ] **E2** Export received and `saved_posts.json` located.
      *Pass:* file opens; record count noted in `docs/private/source_notes.md`.
- [ ] **E3** Export field coverage measured and written down.
      *Pass:* documented answers to: how many saved posts, how many distinct account handles, are permalinks present on all rows, are timestamps present.
- [ ] **E4** oEmbed feasibility test run on 5 permalinks.
      *Pass:* for each of the 5, documented yes/no on: handle present, caption present, image or thumbnail URL present, consistent across post types (single image, carousel, reel). Responses cached to `data/local/oembed_cache/`.
- [ ] **E5** oEmbed verdict recorded.
      *Pass:* `docs/private/source_notes.md` states whether oEmbed alone is sufficient to build listings, and if not, what the fallback is.
- [ ] **E6** Taste snapshot produced by a local user-run step.
      *Pass:* dated JSON in `data/local/`, item count recorded, no live call from application code.
- [ ] **E7** Taste snapshot triaged for garment category.
      *Pass:* documented counts of dress vs non-dress vs non-apparel. The dress subset is the actual taste corpus and its size is recorded.
- [ ] **E8** Catalog feasibility confirmed.
      *Pass:* estimated reachable listing count from the export is at or above the 250 floor, or a documented plan to close the gap.

## F. Exit criteria

Phase 0 is complete when all of the following are true:

- [ ] **F1** All of A, B, C, D pass.
- [ ] **F2** E1–E8 pass, or any failure has a written decision recorded in `docs/DECISIONS.md`.
- [ ] **F3** Commit history shows 6–8 distinct commits telling a sequence, not one bulk commit.
- [ ] **F4** `git ls-files` contains no personal data, no real account handles, no credentials.
      *Pass:* run the pre-commit checks in `docs/DATA_HANDLING.md` and get clean output.
- [ ] **F5** A stranger can clone the repo, read the ADRs, and explain the architecture without asking a question.

## Explicitly NOT in Phase 0

Writing adapters. Computing embeddings. Retrieval. Ranking. Evaluation harness.
n8n workflows. Frontend. Supabase project. Model provider accounts.

If any of these have been started, Phase 0 has lost its purpose, which is to be
certain about the ground before building on it.
