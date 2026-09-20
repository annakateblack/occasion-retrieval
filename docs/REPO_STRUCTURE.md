# Repository structure

`[public]` is committed. `[private]` is gitignored and exists only locally.

```
rental-retrieval/
│
├── README.md                     [public]  systems-level description, not "AI dress finder"
├── LICENSE                       [public]
├── .gitignore                    [public]
├── .env.example                  [public]  keys with empty values
├── .env                          [private] real values, never committed
├── pyproject.toml                [public]  deps + tooling, uv-managed
│
├── docs/
│   ├── adr/                      [public]  Architecture Decision Records
│   │   ├── README.md                       index
│   │   ├── 0001-source-adapter-architecture.md
│   │   ├── 0002-storage-and-vector-search.md
│   │   ├── 0003-taste-model-sequencing.md
│   │   └── 0004-orchestration-boundary.md
│   ├── DECISIONS.md              [public]  locked / reversible / pending register
│   ├── DATA_HANDLING.md          [public]  what may never be committed, and why
│   ├── PHASE_0_CHECKLIST.md      [public]  pass/fail exit criteria
│   ├── REPO_STRUCTURE.md         [public]  this file
│   ├── EVALUATION.md             [public]  Phase 5: methodology and results
│   ├── assets/                   [public]  diagrams
│   └── private/                  [private] endpoint behavior, vendor specifics,
│                                           field-coverage measurements
│
├── db/
│   ├── schema/                   [public]  001_initial_schema.sql
│   └── migrations/               [public]  numbered forward migrations
│
├── src/rental_retrieval/         [public]  ALL business logic lives here
│   ├── schemas/                            normalized ingestion contract (ADR 001)
│   │   └── raw.py
│   ├── sources/                            one adapter per source; pure, no network I/O
│   │   ├── taste_source.py                 (Phase 1a)
│   │   ├── ig_export.py                    (Phase 1b)
│   │   ├── ig_oembed.py                    (Phase 1b)
│   │   └── manual.py                       (Phase 1b)
│   ├── extraction/                         VLM attributes + canonical text (Phase 3)
│   ├── retrieval/                          filter, vector search, fusion, rank (Phase 4+)
│   └── eval/                               metrics and ablation harness (Phase 5)
│
├── tools/
│   └── private/                  [private] snapshot producers that touch a live
│                                           account. Manual, user-run, never
│                                           imported by the application. ADR 001.
│
├── orchestration/
│   └── workflows/                [public]  exported n8n JSON + per-node markdown.
│                                           A workflow only in a hosted instance
│                                           is not portfolio work. ADR 004.
│
├── tests/
│   ├── fixtures/                 [public]  SYNTHETIC records only. Structurally
│   │                                       real, semantically fictional.
│   └── test_*.py                 [public]
│
├── data/
│   ├── sample/                   [public]  synthetic demo data
│   └── local/                    [private] Taste snapshots, Instagram export,
│                                           oEmbed cache, downloaded images
│
├── frontend/                     [public]  Next.js (Phase 8). Empty placeholder now.
├── notebooks/                    [private] exploration; outputs contain real data
└── scripts/                      [public]  one-shot maintenance scripts
```

## Why the boundaries fall where they do

**`src/` holds every decision anyone would want to read.** The retrieval
function, the taste model, the scoring. Nothing interesting lives in
`orchestration/` or in a notebook.

**`tools/private/` is the only place that touches a live account.** It is
gitignored, manual, and never imported by application code. This is what makes
"The save app is not a production dependency" structurally true rather than a promise.

**`notebooks/` is gitignored entirely.** Notebook outputs embed whatever was in
the dataframe, which here means real handles, real URLs, and real images.
Committing a notebook is the most common way personal data leaks into a public
repo. Anything worth keeping from a notebook is rewritten into `src/` or `docs/`.

**`tests/fixtures/` is synthetic and that is a hard rule.** It lets the suite run
with no network and no personal data, and it lets a reader understand the data
shape without seeing anyone's actual information.

**`db/schema/` and `db/migrations/` are separate.** `schema/` is the current
state, readable in one pass. `migrations/` is the ordered history. Readers want
the first; the database wants the second.
