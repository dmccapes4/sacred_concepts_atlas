-- Sacred Concepts Atlas — SQLite schema
-- Authoritative store. concept_dictionary.json / concept_hash.json in artifacts/
-- are mirrors exported by the orchestrator after each section commit.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
  source_id   TEXT PRIMARY KEY,
  text_name   TEXT NOT NULL,
  tradition   TEXT NOT NULL,
  language    TEXT NOT NULL,
  version     TEXT NOT NULL,
  url         TEXT NOT NULL,
  fetched_at  TEXT,
  sha256      TEXT
);

CREATE TABLE IF NOT EXISTS sections (
  section_id  TEXT PRIMARY KEY,          -- source:book:n
  source_id   TEXT NOT NULL REFERENCES sources(source_id),
  book        TEXT NOT NULL,
  ref         TEXT NOT NULL,             -- human citation: "Genesis 1", "2:142-152"
  seq         INTEGER NOT NULL,          -- reading order within source
  text        TEXT NOT NULL,
  metadata    TEXT                       -- JSON
);
CREATE INDEX IF NOT EXISTS sections_source_idx ON sections(source_id, seq);

-- Pages: the hybrid-RAG retrieval unit. Verse-aligned chunks of a section.
-- Sections carry concept signatures; pages carry the indexes. A page inherits
-- its section's signature via the page_concepts view (backfill for context).
CREATE TABLE IF NOT EXISTS pages (
  page_id     TEXT PRIMARY KEY,          -- section_id:pNN
  section_id  TEXT NOT NULL REFERENCES sections(section_id),
  source_id   TEXT NOT NULL REFERENCES sources(source_id),
  page_no     INTEGER NOT NULL,          -- 1-based within section
  ref         TEXT NOT NULL,             -- verse-range citation: "Genesis 1:1-13"
  text        TEXT NOT NULL,
  metadata    TEXT                       -- JSON: first/last verse index, n_verses
);
CREATE INDEX IF NOT EXISTS pages_section_idx ON pages(section_id, page_no);

-- BM25 layer over pages.
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
  page_id UNINDEXED, section_id UNINDEXED, book, text,
  tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS page_embeddings (
  page_id     TEXT NOT NULL REFERENCES pages(page_id),
  model       TEXT NOT NULL,
  dim         INTEGER NOT NULL,
  embedding   BLOB NOT NULL,             -- float32 little-endian
  PRIMARY KEY (page_id, model)
);

CREATE TABLE IF NOT EXISTS concepts (
  concept_id  TEXT PRIMARY KEY,          -- slug of name
  name        TEXT NOT NULL,
  definition  TEXT NOT NULL,
  aliases     TEXT DEFAULT '[]',         -- JSON array: names merged into this concept
  embedding   BLOB,                      -- embedding of the definition
  embed_model TEXT,
  created_by  TEXT,                      -- section_id that coined it
  created_run TEXT,
  created_at  TEXT DEFAULT (datetime('now')),
  status      TEXT DEFAULT 'active',     -- active | merged
  merged_into TEXT
);

CREATE TABLE IF NOT EXISTS section_concepts (
  section_id  TEXT NOT NULL REFERENCES sections(section_id),
  concept_id  TEXT NOT NULL REFERENCES concepts(concept_id),
  weight      REAL NOT NULL,
  rationale   TEXT,
  run_id      TEXT NOT NULL,
  PRIMARY KEY (section_id, concept_id, run_id)
);
CREATE INDEX IF NOT EXISTS section_concepts_concept_idx ON section_concepts(concept_id, run_id);

-- Backfill: pages inherit the concept signature of their parent section.
CREATE VIEW IF NOT EXISTS page_concepts AS
  SELECT p.page_id, p.section_id, sc.concept_id, sc.weight, sc.run_id
  FROM pages p JOIN section_concepts sc ON sc.section_id = p.section_id;

CREATE TABLE IF NOT EXISTS runs (
  run_id       TEXT PRIMARY KEY,
  model        TEXT NOT NULL,
  embed_model  TEXT,
  prompts_sha  TEXT,                     -- sha256 over doctrine+role prompt files
  params       TEXT,                     -- JSON: tau0, tau_max, tau_k, order, seeds...
  started_at   TEXT DEFAULT (datetime('now')),
  finished_at  TEXT
);
