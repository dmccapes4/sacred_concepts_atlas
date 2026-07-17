# Sacred Concepts Atlas

A knowledge and concept space over the primary texts of Judaism, Christianity,
and Islam — in their main languages (Hebrew, English, Arabic). Local LLM agents
walk the corpus section by section, grow a shared concept registry, and stamp
every section with a weighted conceptual signature (weights sum to 1.0). On top
of that sits a hybrid-RAG query pipeline that answers cross-tradition questions
with verse-anchored evidence.

| Corpus | Language | Edition |
|---|---|---|
| Tanakh | Hebrew | UXLC (tanach.us) |
| Bible (66-book canon) | English | World English Bible (USFM) |
| Quran | Arabic | Tanzil Uthmani |

## Documents

- `STRATEGY_SACRED_CONCEPTS_ATLAS.md` — the design doc: schema, agent
  orchestration, model choices, sampling, query pipeline, edge taxonomy.
- `BRIEFING_SacredConceptAtlas_2026-07-16.md` — original project brief.
- `BIBLIOGRAPHY_SACRED_CONCEPTS_ATLAS.md` — source of truth for texts/URLs.
- `RESEARCH_MODEL_INTERPRETIVE_BIAS.md` — multi-model comparison research plan.
- `EMOJI_DICTIONARY.md` — what every log-line emoji means.

## Requirements

- Python 3.10+, `make`, `wget` (zip extract uses Python's `zipfile` — no
  system `unzip` needed)
- [Ollama](https://ollama.com) with the embedding model pulled:
  `ollama pull bge-m3` (embeddings are **always** local bge-m3 — the stored
  page vectors are bge-m3, so this is not swappable per machine)
- For local agents: `qwen3:8b` (and `qwen3:14b` for the dual-model config)
- For cloud agents (`CLOUD=1`): `OPENAI_API_KEY` in a local `.env`
  (not committed)

**Order matters.** `pages-build` / `fts-build` / `embed-pages` / `agent-run`
all need sections in the DB. If ingest failed partway, re-run from
`make tanakh-all bible-web-all quran-all` before those later targets — an
empty DB quietly produces `0 sections` / a no-op agent run.

## Quickstart on a fresh machine

The DB, raw texts, and embeddings are regenerable and not in git. Full rebuild
— **order matters**: pages/agents need sections in the DB first.

```bash
git pull                     # if already cloned
make py-venv                 # venv + requirements.txt
make db-init                 # SQLite schema -> db/atlas.db
make tanakh-all bible-web-all quran-all   # fetch, verify, unpack, ingest
# sanity — expect hundreds of sections, not 0:
#   make tanakh-stats bible-web-stats quran-stats
make pages-build             # verse-aligned retrieval pages
make fts-build               # BM25 index (diacritic-stripped He/Ar)
make embed-pages             # bge-m3 page embeddings via Ollama (long pole)
make validate                # bibliography vs disk vs DB
```

If ingest failed partway (e.g. missing tools), re-run from `tanakh-all` before
`pages-build` / `agent-run` — an empty DB quietly produces `0 sections`.

## Running the concept-extraction agents

### Dual-model config (RTX 4090, 24 GB) — recommended

Router `qwen3:8b` (non-thinking screen) + classifier `qwen3:14b` (thinking
judge) + bge-m3, all resident. See STRATEGY §6 "Dual-model config on the 4090".

```bash
export OLLAMA_MAX_LOADED_MODELS=3
export OLLAMA_KEEP_ALIVE=-1
make agent-modelfiles        # builds atlas-router + atlas-classifier
make agent-run ROUTER_MODEL=atlas-router AGENT_MODEL=atlas-classifier
```

### Single-model config (RTX 3060, 12 GB)

One `qwen3:8b` model serves both roles (two models won't co-reside):

```bash
make agent-modelfile         # builds atlas-conceptor
make agent-run
```

### Cloud config (OpenAI)

```bash
make agent-run CLOUD=1       # embeddings stay local (bge-m3)
```

Common knobs: `ORDER=interleaved|temporal` (section sequencing),
`LIMIT=n` (partial pass), `TAU_0/TAU_MAX/TAU_K` (novelty gate).
Interrupted runs: `make agent-resume` (add the same `ROUTER_MODEL=`/`CLOUD=1`
you started with). Progress lines include a running ETA (⏳).

### Pre-flight and monitoring

```bash
make eval-harness            # dual golden-section runs + invariant checks
make watchdog WATCH=300      # health-check a live run every 5 min
scripts/overnight.sh         # harness -> gate -> full run under watchdog
```

## Querying the atlas

```bash
make query Q="How do the three traditions treat the covenant?"
make query Q="Did Jesus die on the cross?" CLOUD=1
```

Pipeline: probe → hybrid retrieval (BM25 + bge-m3, per-source balanced) →
gap analysis → second retrieval → report → verse-level evidence map. Each run
writes a folder under `queries/` with `report.md` and the full trace.

## Inspecting results

```bash
make concepts-stats          # registry size, per-run coverage, weight sanity
make concepts-export         # registry -> concepts.json
```

Per-run artifacts land in `runs/<run_id>/` (decisions JSONL) and `artifacts/`
(concept dictionary + hash). Every run records its model(s), prompt hashes,
and parameters in the `runs` table — single-model, dual-model, and cloud runs
are directly comparable, which is the substrate for the interpretive-bias
research.

## Repo layout

```
mk/            MakefileBook: 00_main + per-source + embeddings/agents/eval/query
scripts/       ingestion parsers, orchestrators (agent_conceptor, atlas_query),
               eval harness, watchdog, sql.py (sqlite CLI stand-in)
modelfiles/    Ollama Modelfiles + versioned agent prompts (doctrine_core,
               router_role, classifier_role, query roles)
db/            schema.sql (+ atlas.db, generated)
data/raw/      fetched source texts (generated)
runs/          per-run decision logs (generated)
queries/       query reports + traces (generated)
artifacts/     concept dictionary/hash exports (generated)
```
