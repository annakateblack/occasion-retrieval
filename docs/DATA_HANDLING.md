# Data handling policy

This repository is public. It processes personal data and third-party content.
This document states what may and may not be committed, and why. It is a
standing constraint, not a suggestion.

## Never committed, under any circumstances

| Category | Examples | Where it lives instead |
|---|---|---|
| Credentials | Database passwords, API keys, service role tokens, model provider keys | `.env` (gitignored) |
| Session material | Cookies, session tokens, 2FA codes, passwords for any platform | Nowhere. This project never handles these. |
| Personal data exports | Instagram `saved_posts.json`, `saved_collections.json` | `data/local/` (gitignored) |
| Taste corpus snapshots | Taste snapshot JSON, in any form | `data/local/` (gitignored) |
| Downloaded media | Any dress image, from either source | `data/local/images/` (gitignored) or object storage |
| Platform internals | Undocumented endpoint paths, request shapes, internal identifiers | `docs/private/` (gitignored) |
| Third-party account handles | Real UGA rental account handles, in fixtures or seed data | `data/local/`; pseudonymize in anything committed |

## The `.env` boundary

`.env` holds every value that is a secret **or** that identifies a private
integration surface. `.env.example` is committed and contains keys with empty
or obviously-placeholder values, never real ones.

```
DATABASE_URL=
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
MODEL_PROVIDER_API_KEY=

# Taste source. Base URL is configuration, never hardcoded, and never
# committed with a real value. See ADR 001.
TASTE_SOURCE_API_BASE=
TASTE_SOURCE_USER_ID=
```

Rationale for the last two: the snapshot producer needs them, the application
does not. Keeping them in configuration means the public repository contains no
description of a third-party backend, which is a decision the user has
explicitly reserved.

## The `data/local/` boundary

Everything personal. The directory is gitignored except for `.gitkeep`.

```
data/local/
├── taste_snapshot_2026-09-19.json     taste corpus, dated, append-only
├── instagram_export/                   unpacked Meta export
├── oembed_cache/                       one JSON per permalink, cached forever
├── enrichment/                         private images and model-response caches
├── eval/                               real relevance labels and private run outputs
└── images/                             any downloaded media
```

Snapshots are **dated and never overwritten**. Re-running the pipeline against
an older snapshot must reproduce older results, which the evaluation work
requires.

The oEmbed cache is permanent by design. A permalink is resolved once and never
re-requested. This is both a courtesy to the platform and a correctness property:
cached responses make evaluation runs reproducible.

Image-enrichment caches may contain model-derived descriptions of private images,
permalinks, shortcodes, usernames, or captions. Real caches therefore stay under
`data/local/enrichment/` even when the raw image bytes are stored elsewhere.
Real relevance labels and row-level evaluation outputs stay under
`data/local/eval/`. Only aggregate, non-identifying metrics may leave those paths.

## The `docs/private/` boundary

Engineering notes that are accurate and useful but not cleared for publication:
source endpoint behavior, pagination quirks, field coverage measurements,
anything vendor-specific.

Nothing moves from `docs/private/` to `docs/` without a deliberate decision.
The default is private.

## Public sample data

`tests/fixtures/` and `data/sample/` contain **synthetic** records only. They
must be structurally identical to real records and semantically fictional:

- Rental account handles: `@sample_rental_01`, never a real handle
- Image URLs: point at committed placeholder images or `example.com`
- Captions: written for the fixture, not copied from a real post
- Product URLs: `example.com` paths

These exist so the test suite runs without network access and without personal
data, and so a reader can understand the data shape without seeing anyone's
actual information.

## Third-party content specifically

Rental listings are other people's photographs, frequently containing their own
image or a friend's. The handling rules:

1. **Store the permalink as canonical.** Link back to the source post.
2. **Do not rehost images in the public repository.** Ever.
3. **Honor deletion.** A listing whose source post is gone is marked unavailable.
4. **Pseudonymize handles** in anything committed, including README screenshots.
5. **Respect `consent_status`.** The schema carries it; the retrieval query
   filters on it.

Publishing a list of student-run account handles to a public GitHub repository
is a decision with real privacy consequences for people who did not consent to
being in a portfolio project. The default is not to.

## Instagram-derived data

The project never holds Instagram credentials, cookies, or session tokens. The
only ingestion paths are the user's own official data export and resolution of
individual public post permalinks. Neither requires authentication.

The user's saved collection is **positively biased**: those posts were saved
because the user liked them. Any listing ingested from it carries
`is_dev_sample = true`, and the evaluation harness reports on that subset
separately and labels it. Absolute performance numbers from the development
sample are never presented as unbiased results.

## Pre-commit check

Before the first commit that touches data, and periodically after:

```bash
git status --porcelain              # nothing from data/local, docs/private, tools/private
git ls-files | grep -Ei '\.(json|csv|jpe?g|png|webp)$'   # inspect every hit
git ls-files -z | xargs -0 grep -lEi 'api[_-]?key|secret|password|bearer '
```

If a secret is ever committed, rotating it is mandatory. Rewriting history is
not sufficient, because the value must be assumed compromised the moment it is
pushed.
