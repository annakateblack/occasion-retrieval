-- ===========================================================================
-- V1 schema: personalized multimodal dress rental retrieval
-- Scope: DRESSES ONLY. Single user. See docs/adr/ for the decisions behind it.
--
-- Two conventions used throughout:
--   1. Raw source values are preserved alongside normalized ones. Normalization
--      is lossy and its failures must be auditable.
--   2. Enrichment and embeddings are versioned, never overwritten in place.
-- ===========================================================================

create extension if not exists vector;
create extension if not exists pgcrypto;   -- gen_random_uuid()


-- ===========================================================================
-- IDENTITY
-- Thin in V1 (single user) but present so the model is honest about scope.
-- ===========================================================================
create table users (
  id          uuid primary key default gen_random_uuid(),
  handle      text unique not null,
  created_at  timestamptz not null default now()
);


-- ===========================================================================
-- TASTE SIDE: saved items
-- Populated from a local snapshot file via the taste source adapter.
-- Nothing in this system talks to the save app at runtime. See ADR 001.
-- ===========================================================================

create table taste_collections (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references users(id) on delete cascade,
  source_collection_id text not null,          -- id as it appears in the snapshot
  name_raw          text not null,             -- verbatim, e.g. "Formal long "
  name_normalized   text not null,             -- trimmed, lowercased, emoji stripped
  -- Occasion inferred from the collection name. These are the user's OWN labels
  -- and are the closest thing to supervision this project has. Nullable because
  -- many collections (Bedroom, Accessories) carry no occasion meaning.
  occasion_label    text,                      -- 'formal'|'cocktail'|'gameday'|'going_out'|...
  is_apparel        boolean,                   -- set during Phase 1 triage
  snapshot_taken_at timestamptz not null,
  unique (user_id, source_collection_id)
);

create table taste_items (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references users(id) on delete cascade,
  collection_id     uuid references taste_collections(id) on delete set null,

  source_item_id    text not null,             -- platform id, for idempotent upsert
  image_sha256      text not null,             -- dedup key; prefer the source-provided hash
  image_url         text not null,             -- our stored copy or the source URL
  product_url       text,                      -- canonical retailer product page
  title_raw         text,
  brand_raw         text,                      -- merchant/brand as given
  price_cents       int,                       -- ~45% coverage in practice; nullable
  price_currency    text,

  -- V1 is dresses only. Non-dress saves are ingested but excluded from the
  -- taste corpus via this flag, set by the Phase 1 garment classifier.
  -- See ADR 003: an unfiltered corpus puts real weight on home goods.
  garment_category  text,                      -- 'dress'|'top'|'shoes'|'home'|'other'|null
  is_taste_corpus   boolean not null default false,

  saved_at          timestamptz,               -- drives recency weighting (step 3, ADR 003)
  snapshot_taken_at timestamptz not null,
  raw_payload       jsonb not null,            -- full source record, for re-normalization
  ingested_at       timestamptz not null default now(),

  unique (user_id, source_item_id)
);
create index on taste_items (user_id, is_taste_corpus) where is_taste_corpus;
create index on taste_items (user_id, garment_category);


-- ===========================================================================
-- CATALOG SIDE: rental accounts and listings
-- ===========================================================================

create table rental_accounts (
  id                 uuid primary key default gen_random_uuid(),
  handle             text unique not null,              -- @example_rental
  display_name       text,
  school             text default 'UGA',
  contact_method     text,                              -- 'instagram_dm'|'form'
  is_active          boolean not null default true,
  operator_is_current_student boolean,
  last_verified_at   timestamptz,                       -- staleness is a filter, not a vibe

  -- The sustainable ingestion model is opt-in. Making consent a first-class
  -- column forces the product to respect it rather than retrofitting it later.
  consent_status     text not null default 'unknown'
                     check (consent_status in ('unknown','opted_in','opted_out')),
  notes              text
);

create table rental_listings (
  id               uuid primary key default gen_random_uuid(),
  account_id       uuid not null references rental_accounts(id) on delete cascade,

  -- Provenance. The evaluation harness reports on biased and unbiased subsets
  -- separately, and this column is how it tells them apart.
  source_type      text not null
                   check (source_type in ('ig_saved_export','ig_oembed','manual','form','synthetic')),
  source_ref       text,                                -- post permalink or submission id
  is_dev_sample    boolean not null default false,      -- true => from the user's own saves
                                                        --         (positively biased)

  image_url        text not null,
  image_sha256     text not null,
  raw_caption      text,                                -- verbatim, never cleaned in place

  brand_raw        text,
  size_label       text,                                -- '4', 'S', 'XS/S' — messy on purpose
  size_normalized  int,                                 -- canonical US numeric; nullable
  price_cents      int,
  is_available     boolean not null default true,

  snapshot_taken_at timestamptz,
  first_seen_at    timestamptz not null default now(),
  last_seen_at     timestamptz not null default now(),
  raw_payload      jsonb,

  unique (account_id, image_sha256)
);
create index on rental_listings (size_normalized, price_cents) where is_available;
create index on rental_listings (source_type, is_dev_sample);


-- ===========================================================================
-- ENRICHMENT: one shared attribute schema across BOTH domains
-- The canonical_text column is the bridge between clean retail product photos
-- and user-generated Instagram photos. Same vocabulary, same encoder, both sides.
-- ===========================================================================
create table item_attributes (
  owner_type       text not null check (owner_type in ('taste','rental')),
  owner_id         uuid not null,

  silhouette       text,     -- 'a-line'|'bodycon'|'slip'|'fit-and-flare'|'mermaid'|'shift'
  neckline         text,     -- 'square'|'sweetheart'|'halter'|'v'|'strapless'|'scoop'
  length           text,     -- 'mini'|'midi'|'maxi'|'floor'
  sleeve           text,     -- 'sleeveless'|'strap'|'cap'|'short'|'long'|'puff'
  primary_color    text,
  secondary_colors text[],
  pattern          text,     -- 'solid'|'floral'|'sequin'|'lace'|'stripe'|'print'
  fabric_guess     text,
  formality        int check (formality between 1 and 5),
  occasions        text[],   -- ['formal','cocktail','date_night','gameday']
  aesthetic_tags   text[],   -- ['minimal','romantic','preppy','edgy']

  canonical_text   text not null,   -- fixed-template description, garment only
  extraction_model text not null,   -- pinned model+prompt version
  extraction_confidence real,
  extracted_at     timestamptz not null default now(),

  -- Model in the PK: two extractions can coexist and be diffed.
  -- Enrichment is never overwritten in place.
  primary key (owner_type, owner_id, extraction_model)
);


-- ===========================================================================
-- VECTORS
-- (view, model_version) is what turns "compare two embedding models" into a
-- WHERE clause instead of a data migration. The Phase 5 ablations depend on it.
-- ===========================================================================
create table embeddings (
  id            bigserial primary key,
  owner_type    text not null check (owner_type in ('taste','rental','event')),
  owner_id      uuid not null,
  view          text not null check (view in ('image','canonical_text','caption')),
  model_version text not null,
  dim           int  not null,
  vec           vector(1152) not null,   -- sized to the largest encoder in use
  created_at    timestamptz not null default now(),
  unique (owner_type, owner_id, view, model_version)
);
create index on embeddings (owner_type, view, model_version);

-- NOTE: no HNSW/IVFFlat index in V1. Exact scan at this row count is a few ms
-- and has perfect recall. See ADR 002 for the benchmark plan.


-- ===========================================================================
-- QUERY SIDE
-- ===========================================================================
create table events (
  id                 uuid primary key default gen_random_uuid(),
  user_id            uuid not null references users(id) on delete cascade,
  title              text not null,               -- 'Kappa formal'
  event_date         date,
  raw_query          text not null,               -- the prose, verbatim, forever
  parsed_constraints jsonb not null default '{}',  -- hard filters
  intent_text        text,                        -- soft stylistic prose, isolated
  event_class        text,                        -- 'formal'|'cocktail'|'gameday'|...
  created_at         timestamptz not null default now()
);
-- raw_query is kept verbatim so that changing the parser means re-running every
-- historical query and diffing, rather than losing the original input.


-- ===========================================================================
-- OUTPUT AND OBSERVABILITY
-- Logging the full score decomposition per run is what makes evaluation and
-- debugging possible. Logging only the final list is the common mistake.
-- ===========================================================================
create table recommendation_runs (
  id              uuid primary key default gen_random_uuid(),
  event_id        uuid not null references events(id) on delete cascade,
  variant         text not null,        -- 'baseline_random'|'topk_nn'|'topk_nn_recency'|...
  config          jsonb not null,       -- weights, model versions, k values
  n_after_filter  int,
  n_after_vector  int,
  n_after_rerank  int,
  latency_ms      jsonb,                -- per stage
  cost_usd        numeric(10,6),
  created_at      timestamptz not null default now()
);
create index on recommendation_runs (event_id, variant);

create table recommendations (
  run_id       uuid not null references recommendation_runs(id) on delete cascade,
  listing_id   uuid not null references rental_listings(id) on delete cascade,
  final_rank   int  not null,
  scores       jsonb not null,   -- {taste:.72, event:.61, fused:.74, ...}
  matched_taste_item_ids uuid[],  -- the saves that drove the match; powers "why"
  rationale    text,
  primary key (run_id, listing_id)
);


-- ===========================================================================
-- GROUND TRUTH
-- Relevance is query-conditional: a slip dress is relevant for date night and
-- irrelevant for a tailgate. Labels are therefore per (event, listing).
-- ===========================================================================
create table relevance_labels (
  event_id     uuid not null references events(id) on delete cascade,
  listing_id   uuid not null references rental_listings(id) on delete cascade,
  grade        int  not null check (grade in (0,1,2)),
                -- 2 = would actually DM this account about this dress
                -- 1 = plausible, would consider
                -- 0 = no
  label_round  int  not null default 1,   -- round 2 = self-consistency re-check
  labeled_at   timestamptz not null default now(),
  primary key (event_id, listing_id, label_round)
);


-- ===========================================================================
-- TASTE PROFILE
-- method + params let multiple approaches coexist on one catalog with no
-- migration, which is what ADR 003's staged build requires.
-- ===========================================================================
create table taste_profiles (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references users(id) on delete cascade,
  version        int  not null,
  method         text not null,      -- 'topk_nn' in V1
  params         jsonb not null,     -- {"k": 5, "view": "image", "model_version": "..."}
  n_source_items int  not null,
  built_at       timestamptz not null default now(),
  unique (user_id, version)
);
