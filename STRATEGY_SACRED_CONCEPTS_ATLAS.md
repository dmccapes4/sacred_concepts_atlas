# STRATEGY — Sacred Concepts Atlas

**Date:** 2026-07-16
**Companion docs:** `BRIEFING_SacredConceptAtlas_2026-07-16.md` (vision),
`BIBLIOGRAPHY_SACRED_CONCEPTS_ATLAS.md` (source of truth for texts)

The center of gravity of this project is **local agents building the concept
space**. Everything before that (fetch, parse, index) exists to give agents
clean sections and good retrieval; everything after (graph, queries) exists to
use what the agents produced. Phases 0–2 are deliberately boring plumbing so
Phase 3 can be the interesting part.

---

## 1. Database structure (answering the table-per-text question)

The briefing proposes one table per book, and the follow-up proposes one table
per `text_language_version`. Recommendation: **do neither for the section rows
themselves.** Use a single `sections` table keyed by a `sources` registry, where
one `sources` row = one `text_language_version` (exactly the bibliography's `id`
column).

Why a single sections table wins here:

1. **Hybrid retrieval needs one index.** BM25 (FTS5) and the vector index have
   to search *across* all texts to do their job — "retrieve existing concepts
   relevant to this section" and "find cross-tradition parallels" are inherently
   cross-corpus queries. With N tables you either maintain N indexes and merge
   results manually, or you build a shadow union table — at which point the
   union table *is* the design.
2. **The graph layer joins against sections.** Concept assignments, edges, and
   registry provenance all reference `section_id`. One foreign key to one table
   is simple; a polymorphic reference to "one of 60 tables" is not.
3. **Extensibility by language/version becomes a data operation, not a schema
   migration.** Adding `bible_el_sblgnt` later = insert one `sources` row, run
   the ingest. No new DDL, no new indexes, no validation-script changes.

You keep the per-source mental model where it's useful: `source_id` filters
everywhere, and we can generate one SQL **view per source**
(`v_tanakh_he_uxlc`, …) so `SELECT * FROM v_quran_ar_tanzil` behaves exactly
like the table-per-text design.

### Engine: SQLite (single file), not Postgres

Unlike 2ndOpinionMD, this atlas has no server, no concurrent writers, and a
strong "the database is the artifact" property — you should be able to copy
`atlas.db` to another machine and have the whole project. SQLite gives us:

- **FTS5** — literally BM25 (`bm25()` ranking function), zero setup.
- **sqlite-vec** extension — local vector search over embedding blobs.
- Trivial backup/versioning of milestone snapshots.

If we later want Postgres (pgvector, richer SQL), the schema ports directly;
nothing below assumes SQLite-only features beyond FTS5/sqlite-vec.

### Schema (Phase 1 DDL)

```sql
sources (
  source_id     TEXT PRIMARY KEY,   -- bibliography id: tanakh_he_uxlc, ...
  text_name     TEXT NOT NULL,      -- Tanakh | Bible | Quran
  tradition     TEXT NOT NULL,      -- Judaism | Christianity | Islam
  language      TEXT NOT NULL,      -- ISO 639-1
  version       TEXT NOT NULL,
  url           TEXT NOT NULL,
  fetched_at    TEXT, sha256 TEXT   -- provenance of the raw artifact
)

sections (
  section_id    TEXT PRIMARY KEY,   -- e.g. tanakh_he_uxlc:genesis:1
  source_id     TEXT NOT NULL REFERENCES sources,
  book          TEXT NOT NULL,      -- Genesis, Matthew, Al-Baqarah...
  ref           TEXT NOT NULL,      -- human citation: "Genesis 1", "2:142-152"
  seq           INTEGER NOT NULL,   -- global reading order within source
  text          TEXT NOT NULL,      -- full section content (original language)
  metadata      JSON                -- verse count, rukū boundaries, etc.
)

pages (
  page_id       TEXT PRIMARY KEY,   -- section_id:pNN
  section_id    TEXT REFERENCES sections,
  page_no       INTEGER, ref TEXT,  -- verse-range citation: "Genesis 1:1-13"
  text          TEXT NOT NULL       -- verse-aligned chunk, ~1200 chars
)

pages_fts        -- FTS5 (BM25) over pages.text
page_embeddings (page_id, model, dim, embedding BLOB)
page_concepts    -- VIEW: pages inherit parent section's concept signature

concepts (
  concept_id    TEXT PRIMARY KEY,   -- slug: divine_speech_as_creative_act
  name          TEXT NOT NULL,
  definition    TEXT NOT NULL,      -- 1-3 sentences, written by the agent
  embedding     BLOB,               -- embedding OF THE DEFINITION (see §4.Q5)
  created_by    TEXT,               -- section_id that triggered creation
  created_at    TEXT,
  status        TEXT DEFAULT 'active'  -- active | merged(->merged_into)
)

section_concepts (
  section_id    TEXT REFERENCES sections,
  concept_id    TEXT REFERENCES concepts,
  weight        REAL NOT NULL,      -- influence score
  rationale     TEXT,               -- agent's one-line grounding quote/reason
  run_id        TEXT,               -- which agent pass produced this
  PRIMARY KEY (section_id, concept_id, run_id)
  -- CHECK enforced in code: SUM(weight) per (section_id, run_id) = 1.0 ± ε
)

runs (run_id, model, modelfile_sha, params JSON, started_at, finished_at)
```

`runs` matters more than it looks: concept extraction is stochastic, and we will
re-run with tuned prompts/thresholds. Keeping assignments keyed by `run_id`
means a re-run is a new layer we can diff, not an overwrite we regret.

---

## 2. Phased implementation

### Phase 0 — Scaffold, fetch, verify (this commit)

- `mk/` per-source Makefiles + root `Makefile` (modeled on 2ndOpinionMD/mk).
- `make fetch-all` downloads all Wave-1 artifacts into `data/raw/<source_id>/`,
  records sha256.
- `scripts/validate_bibliography.py` — parses the bibliography table and checks
  it against disk (and against the DB once it exists). `make validate`.
- **Exit criteria:** all 3 sources + Quran metadata on disk, validation green.

### Phase 1 — Parse & section

- One parser per format: UXLC XML (Tanakh), USFM (WEB Bible), Tanzil text +
  `quran-data.xml` (Quran). Output: `sources` + `sections` rows.
- Sectioning granularity (see §4.Q2): chapter for Tanakh/Bible; surah for short
  surahs, rukūʿ-group chunks (~15–40 ayat) for long ones.
- **Exit criteria:** `make validate` passes DB checks — expected section counts
  per source (929 Tanakh chapters, 1,189 Bible chapters, ~250–320 Quran
  sections), no empty text, no duplicate refs.

### Phase 2 — Pages + hybrid retrieval

- **Pages are the retrieval unit; sections are the concept unit.** A page is a
  verse-aligned chunk (~1200 chars, never splitting a verse) of its parent
  section. Full sections are far too coarse for retrieval (a chapter can run
  10k+ chars, blurring its embedding across many ideas); pages give sharp
  BM25 and vector hits. Concept signatures stay at section level — pages
  inherit their section's signature via the `page_concepts` view (backfill
  for context/insight, not independent page-level extraction).
- FTS5 index (BM25) over pages; `bge-m3` embeddings per page (nothing is
  truncated at this size). Concept definitions embedded as they are born.
  Vector search is brute-force numpy over the embedding matrix — at ~8k pages
  and a few hundred concepts this is milliseconds; no ANN extension needed.
- Section-level retrieval = aggregate page hits (max page similarity per
  section).
- **Exit criteria:** spot-check queries ("covenant", "יום הדין", "الميثاق")
  return sensible cross-corpus results.

### Phase 3 — Local agents build the concept space (the point)

The loop, per section, executed by a **local model via Ollama** with a
Modelfile that encodes the concept doctrine (definition, similarity rule,
weighting discipline — briefing §3):

1. **Contextualize.** Hybrid-search the concept registry with the section text;
   present the top-k existing concepts (name + definition) to the agent.
2. **Extract.** Agent proposes 2–6 weighted concepts for the section, weights
   summing to 1.0, each with a grounding rationale quoting the text.
3. **Reconcile.** For each proposal, embedding-similarity against registry:
   - above match threshold → map to existing concept (agent confirms);
   - below → agent states novelty confidence; a new concept is created only if
     confidence ≥ **rising threshold** τ(n) (see §4.Q3).
4. **Commit.** Write `section_concepts` (+ new `concepts`) under a `run_id`.

Implementation notes:

- Orchestrator is a plain Python script walking sections in `seq` order; the
  "agent" is the Ollama model + Modelfile + the retrieval tool. No framework.
- Process order matters early (registry is cold). **`--order` is a real
  experimental axis, not a nuisance parameter** (made concrete 2026-07-17):
  `interleaved` round-robins the traditions so no vocabulary imprints first
  (the cycle follows composition order Tanakh→Bible→Quran — previously an
  alphabetical accident let the English Bible name creation before the
  Hebrew); `temporal` processes Tanakh, then Bible, then Quran — the
  lineage prior, mirroring how the traditions actually built on each other.
  The trade-off is structural: under `temporal`, later texts face a higher
  τ against a mature registry, so the Quran mints least and gets described
  in earlier traditions' vocabulary — "natural influence" and "the
  latecomer is read through the incumbent ontology" are the same mechanism.
  The flagship atlas uses `interleaved` (fairness prior); a `temporal` pass
  against an isolated DB copy (registry is global — harness-style isolation
  required) measures the order effect directly: registry Jaccard,
  per-tradition mint rates, and which interleave-minted Quranic concepts
  (fasting, pilgrimage, qibla) disappear under temporal order.
- Every agent decision (proposals, merges, rejections, confidences) is logged
  to a `decisions` JSONL per run — this is the tuning corpus for thresholds.
- **Exit criteria:** one full pass over all sections; registry growth curve
  flattens (new-concepts-per-100-sections declining); weight distributions are
  non-uniform (agent isn't defaulting to 0.5/0.5).

**Pre-flight: eval harness (`make eval-harness`, ~30 min).** Before any full
pass, `scripts/eval_harness.py` runs the pipeline twice over 12 golden
sections in *isolated, concept-wiped copies* of the DB (seeds 1000/9000; the
real DB is never written). The golden set packs the failure modes into the
fewest sections: a cross-lingual anchor (Genesis 1 in Hebrew *and* English —
same text, two languages; if signatures don't converge here, cross-lingual
convergence is broken everywhere), cross-tradition parallels (Genesis 7 ↔
Surah 71 Nuh; Exodus 20 ↔ Al-Baqara 2:40–71), an intra-tradition doctrinal
tension (Romans 3 ↔ James 2), and edge cases (Al-Ikhlaas at 144 chars, Al-
Fatiha, a 1 Chronicles genealogy as the "boring control"). Checks: signature
invariants (2–6 concepts, weights = 1.0), registry health (size bounds,
stopword concepts, near-duplicate definitions), parallel-pair convergence,
and **inter-run agreement** — concepts matched across the two runs by
definition-embedding cosine (≥ 0.75), then mean min-sum signature overlap;
below 0.25 means signatures reflect sampling noise, not text, and every
downstream edge would inherit that noise. Exit 0/1/2 = pass/warn/fail.

**In-flight: watchdog with codified abort criteria (`make watchdog`,
`WATCH=300` to loop).** `scripts/ingestion_watchdog.py` answers "should I
kill this at hour 3?" from `decisions.jsonl` + the DB. **Abort** (exit 2):
A1 no decision event for 15 min (stall/hang); A2 empty-signature rate > 5%;
A3 registry runaway (> 400 absolute, or > 35 new concepts in the trailing
100 sections after 150 done — τ must be biting by then); A4 median section
time (trailing 20) > 300 s (ETA blown ~4×). **Warn** (exit 1): a stopword
concept in > 50% of sections, semantic-retry rate > 40%, median > 150 s,
> 5 near-duplicate definition pairs (cosine > 0.92 — schedule the merge
pass). `make agents-reset CONFIRM=1` wipes concept state (concepts,
signatures, runs — never sections/pages/embeddings) for a cold restart.

### Phase 4 — Graph assembly & first queries

- Nodes = sections with concept signatures (already in DB), plus concepts
  themselves as a second node type. Edges = derived, not agent-authored,
  across a **connascence taxonomy** (structural, conceptual, temporal,
  co-occurrence, co-variance) — refined in §9. Blocked on Phase 3 completion:
  every edge type except structural/temporal is computed from signatures.
- Export to a simple format (GraphML / JSON) for exploration; queries like
  "top shared concepts across all three traditions", "concepts unique to one
  tradition", "conceptual center of gravity of each book".
- **Exit criteria:** the briefing's three motivating questions (§1) are each
  answerable with a query; the discovery queue (§9.4) produces at least a few
  section pairs a human reviewer finds non-obvious.

### Explicitly deferred (per briefing §6)

UI/visualization, autonomous multi-agent orchestration, commentaries/secondary
literature, graph algorithms beyond overlap edges, prediction/generation.

---

## 3. Repo layout

```
sacred_concepts_atlas/
├── Makefile                  # includes mk/*.mk
├── mk/
│   ├── 00_main.mk            # shell, vars, help, dirs, db-init
│   ├── 01_tanakh.mk          # fetch/verify/ingest tanakh_he_uxlc
│   ├── 02_bible_web.mk       # fetch/verify/ingest bible_en_web
│   ├── 03_quran.mk           # fetch/verify/ingest quran_ar_tanzil (+metadata)
│   ├── 80_embeddings.mk      # (Phase 2) embed sections/concepts
│   ├── 85_agents.mk          # (Phase 3) run concept-extraction passes
│   └── 90_validate.mk        # bibliography ↔ disk ↔ DB validation
├── scripts/
│   └── validate_bibliography.py
├── data/
│   └── raw/<source_id>/      # fetched artifacts (gitignored)
├── db/atlas.db               # SQLite (gitignored; schema in db/schema.sql)
├── modelfiles/               # (Phase 3) concept-extraction Modelfiles
└── BIBLIOGRAPHY / BRIEFING / STRATEGY docs
```

---

## 4. Answers to the briefing's "Questions for Fable" (§7)

**Q1 — Defining "concept" and similarity in the prompt.** The briefing's
definition is good; make it operational with three additions. (a) A **format
contract**: a concept is a *name* (3–6 words, noun phrase) plus a *definition*
(1–3 sentences) that must be intelligible without the section that spawned it —
if the definition needs the story, it's too narrative-specific. (b) A
**two-sided test** stated in the prompt: "Could this concept plausibly apply to
a section in a *different* book? If no, too specific. Would it apply to more
than ~a third of all sections? If yes, too broad." (c) **Similarity is decided
against definitions, not names**, with the rule: same underlying idea despite
different tradition-specific vocabulary ⇒ same concept; genuinely different
theological content ⇒ distinct, even if the words match. Give 3–4 worked
merge/don't-merge examples in the Modelfile (e.g. covenant-as-conditional-
relationship: merge across Deuteronomy and Quranic mīthāq; divine-mercy vs
divine-forgiveness: distinct).

**Q2 — Section granularity.** Chapters, with one exception. Tanakh and Bible
chapters (929 + 1,189) are already roughly thematically coherent and give a
node count (~2,300–2,400 total) that a local model can process in one long
pass. The Quran's surahs range from 3 to 286 ayat, so: surah = section when
short (< ~40 ayat), else split on rukūʿ boundaries from `quran-data.xml`,
grouping consecutive rukūʿāt to land in the 15–40 ayah range. Uniform-ish
section mass is what makes "each node has equal representation" true in
practice, not just in the weighting rule.

**Q3 — Rising threshold in practice.** Make it explicit and cheap to tune:

    τ(n) = τ_max − (τ_max − τ_0) · k / (k + n)

with `n` = current registry size, and starting values τ_0 = 0.55, τ_max = 0.92,
k = 150 (τ ≈ 0.74 at n=150, ≈ 0.86 at n=750). All three are CLI parameters of
the orchestrator and recorded in `runs.params`. Two practical guards matter
more than the exact curve: (1) the agent must state novelty confidence *before*
being told the threshold (no anchoring); (2) rejected-as-novel proposals are
still logged with their nearest existing concept, so we can audit whether the
threshold is suppressing real concepts and re-admit them in a later pass.

**Q4 — Embedding model.** **`bge-m3`** (available via Ollama). The deciding
constraint is that Wave 1 is *natively multilingual* — Hebrew, Greek-influenced
English, and Arabic must land in one vector space, and cross-lingual retrieval
("find Quranic sections near this Genesis chapter") is a core query.
`nomic-embed-text` and `mxbai-embed-large` are English-centric; bge-m3 is the
strongest local multilingual retriever and handles long passages (8k tokens).
Fallback if it underperforms on Hebrew: `paraphrase-multilingual` variants, or
embed an English gloss alongside the original. The `section_embeddings.model`
column exists precisely so we can A/B models without a migration.

**Q5 — Concept registry structure.** Not a bare JSON hash: a table (`concepts`,
§1) where each concept carries an **embedding of its definition**. The novelty
check is then geometric-first (nearest existing definitions retrieved by
vector, shown to the agent) and judgment-second (agent decides merge vs new
against the similarity rule). A JSON export (`make concepts-export`) gives you
the browsable hash view; the DB stays authoritative and keeps provenance
(which section coined it, which run, merge history via `status`).

---

## 5. Validation contract

`make validate` (`scripts/validate_bibliography.py`) enforces, in order:

1. **Bibliography parse** — table well-formed, ids unique and snake_case.
2. **Disk vs bibliography** — every `verified` row has `data/raw/<id>/` with a
   non-empty artifact; unexpected directories are flagged.
3. **DB vs bibliography** (skipped with a note until `db/atlas.db` exists) —
   every bibliography id has a `sources` row and > 0 sections; section counts
   within expected ranges; per-source view exists; no orphan sections.
4. **Weights** (once Phase 3 data exists) — per (section, run), concept weights
   sum to 1.0 ± 0.01; no dangling concept references.

Exit code non-zero on any failure, so it can gate `make all`.

---

## 6. Base model & Modelfile strategy (Phase 3)

**Decision: `qwen3:8b`** (over `qwen2.5-coder:7b` and `qwen2.5-coder:14b`).

Why, in order of weight:

1. **Task shape.** The conceptor's work is reading comprehension of religious
   text, cross-tradition judgment ("is Quranic mīthāq the same concept as
   Deuteronomic covenant?"), and disciplined structured output. That is
   general instruction-following, not coding. The qwen2.5-coder models are
   fine-tuned *away* from this — their post-training corpus is code, and
   coder tunes measurably regress on humanities-register text.
2. **Languages.** Sections are ingested in Hebrew, Arabic, and English. Qwen3's
   multilingual coverage (119 languages, strong Arabic, decent Hebrew) is a
   generation ahead of Qwen2.5's; the coder variants are the weakest of all
   three here.
3. **Reasoning mode.** Qwen3 has a native thinking mode. Concept reconciliation
   (merge vs novel, confidence estimation) benefits from explicit deliberation;
   we let it think and require the final channel to be pure JSON.
4. **VRAM budget on the 3060 (12 GB, ~1 GB taken by desktop).** qwen3:8b q4 is
   5.2 GB, leaving room for an 8k KV cache *and* `bge-m3` resident at the same
   time — the agent loop calls both on every section. qwen2.5-coder:14b (9 GB)
   would force embedding/generation to swap models in and out on every step.
5. **Upgrade path.** On the RTX 4090 (24 GB), the swap is one line in the
   Modelfile: `FROM qwen3:14b` (or `qwen3:32b` q4) and raise `num_ctx`.
   Re-runs land under a new `run_id` with the model recorded in `runs.model`,
   so 8b-vs-14b output is directly diffable.

### Modelfile strategy

The Modelfile (`modelfiles/atlas-conceptor.Modelfile`) is the **doctrine
document** — everything the briefing says must be "extremely well-specified"
lives in its SYSTEM block, versioned in git, hashed into `runs.modelfile_sha`.
The orchestrator's per-section user message carries only *variables* (section
text, candidate concepts from hybrid retrieval); the *rules* never move out of
the system prompt. Structure:

1. **Role & scope** — comparative-religion analyst; ground everything in the
   provided section; no outside doctrine imported into rationales.
2. **Concept contract** — name (3–6 word noun phrase) + standalone definition
   (1–3 sentences), with the two-sided breadth test (§4.Q1) and good/bad
   examples from the briefing.
3. **Similarity doctrine** — judged on definitions not names; worked merge /
   don't-merge examples including one cross-tradition merge and one
   same-vocabulary-different-theology non-merge.
4. **Weighting discipline** — 2–6 concepts, weights sum to 1.0, weights encode
   share-of-meaning; explicit anti-patterns (uniform weights, >0.85 dominant
   weight as a laziness signal, generic filler concepts).
5. **Novelty protocol** — confidence stated *before* the threshold is known
   (anchoring guard, §4.Q3); the orchestrator applies τ(n), not the model.
6. **Output contract** — one JSON object, schema fixed; the orchestrator also
   passes the schema via Ollama's structured-output `format` parameter, so the
   contract is enforced twice (prompt + decoder).

### One loaded model, two roles

Router and classifier are both served by the single `atlas-conceptor` model
(qwen3:8b + sampling params, thin Modelfile). Two separate Ollama models would
alternate on every section and force a weight reload each switch (~2×5.2 GB
does not co-reside on the 3060) — hours of pure loading over a full pass.
Instead the doctrine lives in versioned prompt files sent as the system
message per call:

    doctrine_core.md  (concept contract, similarity, weighting, grounding)
      + router_role.md      -> router      (think=off, temp 0.7/top_p 0.8)
      + classifier_role.md  -> classifier  (think=on,  temp 0.6/top_p 0.95)

Their combined sha256 is recorded in `runs.prompts_sha`, so prompt-doctrine
versions are as auditable as a Modelfile would have been. Role-appropriate
sampling follows Qwen3's per-mode recommendations: the router is a wide-recall
screen and runs in non-thinking mode (3–5× faster); the classifier does the
judgment and keeps thinking on.

### Dual-model config on the 4090 (adopted 2026-07-17)

The one-model constraint above is a 3060 constraint. On the 4090 (24 GB) two
models co-reside, so the router and classifier can be sized independently:

| Role | Model | Modelfile | Mode |
|---|---|---|---|
| Router | `qwen3:8b` (5.2 GB) | `atlas-router` | non-thinking, temp 0.7/top_p 0.8 |
| Classifier | `qwen3:14b` (9.3 GB) | `atlas-classifier` | thinking, temp 0.6/top_p 0.95 |
| Embeddings | `bge-m3` (~1.2 GB) | — | — |

VRAM: ~15.7 GB of weights + two 16k KV caches ≈ 19–20 GB, comfortably inside
24 GB. Set `OLLAMA_MAX_LOADED_MODELS=3` and `OLLAMA_KEEP_ALIVE=-1` so all
three stay resident — the loop alternates router/classifier/embed on every
section, and any eviction reintroduces exactly the reload thrash the 3060
design avoided. Both Modelfiles raise `num_ctx` to 16384 (classifier prompts
with candidates + verse evidence reach ~19k chars late-run; 8k was the 3060
ceiling, not the design target).

Usage: `make agent-modelfiles` builds the pair; then
`make agent-run ROUTER_MODEL=atlas-router AGENT_MODEL=atlas-classifier`.
A split run is recorded in `runs.model` as
`router=atlas-router,classifier=atlas-classifier`, so it is attributable in
the bias research alongside single-model and cloud runs.

**Why not `llama3.2` (3B) as the router.** The instinct is sound — a router
is a wide-recall screen, and a 2 GB model that answers in a fraction of the
time is exactly what you'd reach for (it works well in that role in
English-only pipelines). It fails here on language, not on size: our router
reads the raw section text in Hebrew and Arabic and must copy verbatim anchor
verses and judge which known concepts plausibly apply. Llama 3.2's official
language coverage is English, German, French, Italian, Portuguese, Hindi,
Spanish, Thai — no Hebrew, no Arabic, i.e. two-thirds of the corpus. A router
that under-reads Tanakh and Quran sections doesn't produce noisy candidates
the classifier can veto; it produces *silent misses*, and the classifier
never sees what the router dropped (recall errors at stage 1 are
unrecoverable by design). `llama3.1:8b` has the same language list. Qwen3 is
the only family on the host with credible Hebrew + strong Arabic, which is
why both roles stay in-family and the speed lever is 8b-vs-14b rather than
Qwen-vs-Llama. On a 4090 the non-thinking 8b router is fast enough that the
3B's latency advantage buys little anyway.

Side benefit for the bias research (`RESEARCH_MODEL_INTERPRETIVE_BIAS.md`):
keeping router and classifier in one model family preserves clean attribution
— a `qwen3:8b`+`qwen3:14b` run varies capability within one interpretive
lineage, whereas a Llama router under a Qwen classifier would confound reader
bias with router recall bias. For bias-comparison runs proper, prefer the
homogeneous configs (same model both roles) so each concept space has a
single reader of record.

### Sampling parameters (and why temperature is not 0.0)

The conceptor is heuristic-management infrastructure, so greedy decoding
(temperature 0) looks natural — but it is wrong for this model in this mode,
for documented and practical reasons:

1. **Vendor guidance is unambiguous.** The Qwen3 model card: for thinking
   mode use `Temperature=0.6, TopP=0.95, TopK=20, MinP=0` and "DO NOT use
   greedy decoding, as it can lead to performance degradation and endless
   repetitions." The failure mode is real: long chain-of-thought under greedy
   decoding gets trapped in repetition loops (worse at q4 quantization), and
   the thinking channel is where our merge-vs-novel judgment happens. Going
   *below* 0.6 (my earlier 0.3) trades toward the same failure mode.
2. **Temperature 0 does not buy determinism anyway.** GPU inference under
   Ollama is not bitwise reproducible (floating-point non-associativity,
   dynamic batching), and the effective prompt changes every section as the
   registry grows. The audit trail lives in `runs` + the decisions JSONL, not
   in the decoder.
3. **Retries require variance.** The orchestrator validates every response
   (JSON schema, weight sum); on failure it resamples. At temperature 0 an
   invalid generation would fail identically on every retry.
4. **The consistency that matters is not decoder-level.** Applying the same
   standards across ~2,400 sections is enforced by the doctrine prompt,
   the structured-output grammar (`format` parameter), and validation — and
   the numbers the model emits (weights, confidences) are coarse (0.05
   steps), so sampling noise rarely moves them.

Adopted configuration (Qwen3 thinking-mode defaults, verbatim):
`temperature 0.6, top_p 0.95, top_k 20, min_p 0.0, repeat_penalty 1.0`.
`repeat_penalty` is neutralized (Qwen guidance; a >1 penalty also mildly
distorts JSON output, which repeats structural tokens by design — loop
prevention is the sampler's job here). `seed` is deliberately *not* set in
the Modelfile: the orchestrator sets and records it per run (bumping on
retries), which gives near-reproducibility without freezing retry paths.
`num_ctx 8192` fits section + retrieved candidates + thinking on the 3060;
bump to 16k+ on the 4090.

---

## 7. Ingestion orchestration: router → retrieve → classifier

Refined from the two-agent design proposal (2026-07-16). The shape survives
intact; the refinements below are mostly about confidence semantics, candidate
assembly, and where authority lives.

### The two artifacts

- `artifacts/concept_dictionary.json` — `{name: definition}`
- `artifacts/concept_hash.json` — `{name: {section_id: influence}}`

Both are **exported mirrors of the database**, atomically rewritten after
every section commit. SQLite stays authoritative because it carries what the
JSON can't: definition embeddings, provenance (coining section, run), merge
history, and per-run layering. The hash maps section→influence as an object
(not a list of pairs) — same information, direct lookup. Restarting or
re-running never corrupts the artifacts; they are always a pure function of
the DB state.

### Stage 1 — Router (wide recall, deliberately information-poor)

Input: the **full list of concept names** (no definitions) + section text.
Output: up to 10 existing-concept candidates with confidence, up to 4
new-concept candidates (name + definition + confidence), a draft weighted
distribution, and a grounding rationale.

The router's constraint is the point: names-only across the *whole* registry
gives global awareness that top-k retrieval cannot (it sees everything, shallowly),
and it stays cheap as the registry grows (~300 names ≈ 2k tokens). It runs in
non-thinking mode — recall screening doesn't need deliberation, and it's the
call that runs 2,348 times on the hot path. Router output is **advisory
everywhere**: its draft distribution and novelty confidences are handed to the
classifier as a starting point, never committed.

### Stage 2 — Candidate set assembly (orchestrator, no model call)

The classifier's candidate set is the union of three recall paths, capped at 12:
1. Router selections (validated against the registry by exact name/alias).
2. Embedding retrieval: section pages → nearest concept definitions (catches
   what the router's name-level scan missed).
3. For each router new-candidate: its nearest existing concepts by definition
   embedding — the near-duplicate check that surfaces "this 'new' concept
   already exists under different vocabulary" *before* the classifier rules.

### Stage 2b — Verse-anchored corpus evidence (adopted 2026-07-16)

For each entry in its draft distribution the router also picks the one verse
that best embodies that concept (verbatim from the section). Each anchor verse
becomes a semantic query over the page index, and the hits are bucketed per
draft concept for the classifier:

    {draft_concept: {anchor_verse: [(page_ref, similarity,
                                     section_signature, draft_overlap), ...]}}

where `section_signature` is the retrieved section's concept signature under
the current run, and `draft_overlap` is its overlap with the router's draft —
`Σ_c min(draft[c], neighbor[c])`, the same min-sum used for graph edges in
Phase 4. Design notes:

- **Why verse anchors beat whole-section retrieval:** a section embedding
  averages several ideas; a single distinctive verse is a sharp, per-concept
  query. This also makes the router's concept claims falsifiable — each claim
  now carries a concrete textual witness (logged, and checked verbatim).
- **What the classifier does with it:** recognize reuse (neighbors
  consistently carrying an existing concept), resist false merges (high verse
  similarity + disjoint signatures = shared vocabulary, different theology),
  and calibrate novelty (similar-but-unlabeled neighborhoods).
- **The staleness caveat (raised in the proposal, real, accepted):** early
  sections retrieve mostly unlabeled neighbors, and signatures assigned early
  reflect a smaller registry than later ones. Two mitigations: unprocessed
  neighbors are shown explicitly as "(not yet processed)" — absence of
  signature is never evidence against a concept — and the classifier prompt
  states that evidence is context, not authority. The permanent fix is the
  Phase-4/5 **second pass**: re-running with a mature registry re-labels every
  section with full-coverage evidence, and the run layering (`run_id`) makes
  pass-1 vs pass-2 drift directly measurable — itself a useful signal for
  concept stability.
- Hits exclude the section under analysis and dedupe to the best page per
  section.
- **Per-source ranking (added 2026-07-16, same fix as query retrieval §8):**
  anchor verses are verbatim, so they're in the section's own language, and
  bge-m3 scores same-language matches ~0.05–0.10 higher than equally relevant
  cross-lingual ones — a global top-k returned same-corpus neighbors only.
  Evidence retrieval now takes the top hit *per source* per anchor (same
  evidence-block size, guaranteed tradition spread), so a Genesis 7 anchor
  also surfaces Surah 71's flood. **Similarity floor 0.55** (added after the
  eval_20260716_232206 harness failure): a weak cross-tradition neighbor
  (e.g. cosine 0.53) is noise, and noisy evidence neighborhoods destabilized
  the classifier's mint/reuse decisions — registries diverged 16 vs 27
  concepts across seeds and inter-run agreement fell 0.49 → 0.24. Sources
  with no hit above the floor simply contribute nothing for that anchor. This matters beyond the single decision:
  cross-tradition evidence at ingestion time is what seeds cross-tradition
  concept convergence, which the Phase-4 discovery queue depends on. Note the
  router's anchors deliberately do NOT get the per-language query schema that
  probe/gap use — anchors must stay verbatim quotes (their falsifiability
  property); the leveling happens at the retrieval end instead.

### Stage 3 — Classifier (final authority, information-rich)

Input: section text + rich dictionary entries for the candidate set + the
router's draft and new candidates. Entries carry more than definitions (the
dictionary-enrichment suggestion, adopted): **aliases** (names merged into the
concept), **usage stats** (sections touched, total influence), and **exemplar
sections** (top 3 by weight). Usage context is what lets the classifier judge
"does this section belong to that concept's family" rather than matching
definition prose in a vacuum.

Output: final 2–6 concept signature (weights → 1.0), each entry either
`existing` (exact concept_id) or `new` (name + definition + novelty
confidence). Thinking mode on. Structured output enforced by JSON schema at
the decoder (Ollama `format`), so malformed JSON is impossible; residual
failures (weight sums, bad ids) trigger a resample with a bumped seed (≤3
attempts).

### Confidence semantics and the τ(n) gate

Confidences mean different things at each stage and only one is load-bearing:
router confidence ranks candidates; the **classifier's novelty confidence** is
what the orchestrator gates against τ(n) (§4.Q3). Neither agent is ever told
the threshold (anchoring guard). A gated-out new concept is not dropped
silently: its weight flows to the **nearest existing concept** by definition
embedding (marked `[gated->nearest]` in the rationale), and the rejection —
with name, definition, confidence, threshold, and nearest neighbor — is logged
to `runs/<run_id>/decisions.jsonl` for later audit/re-admission.

**Staged for gate v0.2 — NOT applied while the dual GPT-4.1/Qwen3 ingestion
runs are live** (mid-run prompt/doctrine changes would change `prompts_sha`
and contaminate the bias comparison). Verified on the GPT-4.1 run at ~653
sections: 151 rejections, all reweighted to a nearest neighbor, weight always
landed. The v0.1 absorb rule (cosine top-1, unconditional) has three known
refinements, in priority order:

1. **Agent-chosen absorb target**: on gate failure, a cheap follow-up asks
   the classifier to pick the receiving concept from the candidate set — or
   "none" (renormalize rest of signature) when the embedding neighbor is
   thematically adjacent but theologically wrong.
2. **Split/refine outcome**: a rejected proposal is often a *bundle* — three
   related ideas compressed into one name that correctly failed novelty.
   Third gate outcome beyond create/absorb: decompose into 2–3 tighter
   existing-or-new assignments whose weights sum to the original.
3. **Similarity floor**: below a minimum cosine, drop the weight rather than
   force a bad absorb.

Also staged for the same window: doctrine_core.md gains the mode-of-
endorsement axis (commands / permits-regulates / narrates / divine-agency /
condemns) already adopted in the query report role — concept definitions on
legal/sensitive material should encode *how* a text relates to a practice,
not only the practice. Design split-vs-absorb against the dual-run diffs:
sections where one model minted and the other gated-and-absorbed are the
richest test cases.

### Orchestrator guarantees (code, not model discipline)

- Exact-name/alias reuse: a "new" proposal whose name already exists is
  silently converted to reuse.
- Slug collisions get suffixed; duplicate concept ids within one signature are
  merged; final weights renormalized to exactly 1.0.
- Every section commit is atomic (DB transaction + artifact tmp-rename), so
  the run is killable and resumable at any point (`--resume` skips sections
  already present under the run_id).
- Full decision trace per section (router output, candidate set, classifier
  output, gate results, timings) in `decisions.jsonl` — the tuning corpus for
  τ parameters and prompt revisions.

---

## 8. Query orchestration: probe → gap → report → evidence map

`scripts/atlas_query.py` (`make query Q="..."`). Four roles on the same loaded
`atlas-conceptor` model, prompts in `modelfiles/{probe,gap,report,evidence}_role.md`
over a shared `query_core.md` (corpus description + citation discipline).
Every run writes `queries/<ts>_<slug>/` with `report.md`, `evidence_map.json`,
and a full `trace.jsonl`.

Pipeline (refinements over the proposal noted inline):

1. **Probe** (think=off) — query + source dictionary → a **level-plain
   per-language query plan** (revised 2026-07-16):
   `{queries: {hebrew: {terms[], semantic[]}, english: {...}, arabic: {...}}}`
   + rationale/confidence. Budget: ≤3 terms + ≤2 semantic per language. The
   language keys are built at runtime from the queried sources' languages
   (adding a Greek NT later grows a `greek` slot with zero code changes) and
   are **required by the JSON schema**, so grammar-constrained decoding forces
   the model to consider every corpus language on every query — empty arrays
   are legal, silent omission is not. (Motivating failure: a salvation query
   whose probe emitted six English terms and nothing else, yielding a
   Bible-only report.) The craft rules still carry the corpus's defining
   constraint: **BM25 is language-siloed** (a term only ever matches its own
   language's text — covenant/בְּרִית/ميثاق are three separate queries), while
   **semantic queries are cross-lingual** through bge-m3 and phrased as
   passage-like statements, not questions. No routing logic exists: FTS silos
   terms for free, and every semantic query searches all source silos
   per-source (a mistranslated term is a harmless no-op; a same-language
   semantic query sharpens its own silo's ordering).
2. **Hybrid retrieval + fusion** — each query (term via FTS5/BM25, semantic
   via the page-embedding matrix) returns a ranked list; lists merge by
   reciprocal-rank fusion, `score(p) = Σ 1/(60 + rank)`. RRF chosen over score
   mixing because BM25 and cosine are on incommensurable scales. **The
   semantic arm contributes one ranked list per source** (added 2026-07-16):
   bge-m3 scores same-language matches ~0.05–0.10 above equally relevant
   cross-lingual ones, so a global top-k is monolingual in practice — ranking
   each source's silo separately puts every source's best pages at RRF rank 1.
   Context is truncated to a char budget best-first by fused score (page
   never split).
3. **Gap** (think=on) — sees query, probe rationale, and stage-1 pages.
   Outputs follow-up queries in the same per-language shape as the probe
   (empty allowed — "do not invent queries to fill quota") plus a
   `gap_report`, and — the adopted relevance-gate idea — **scores every shown
   page** for relevance; the orchestrator drops pages below 0.35. The gate
   threshold is system-side and never revealed to the model (same anchoring
   guard as τ).
4. **Retrieval 2 + merge** — follow-up queries fused as before; gap-dropped
   pages are barred from re-entry, gap-kept pages get a relevance boost so
   re-truncation cannot evict what the gap stage explicitly kept.
5. **Report** (think=off) — query + gap_report + curated context → structured
   report: executive summary, 2–5 thematic sections with inline refs,
   `referenced_pages` (only pages actually cited), and a `limitations`
   section fed by the gap analysis. Refinement over the proposal: report gets
   the gap_report so known holes surface as stated limitations instead of
   silent omissions.
6. **Evidence map** (think=on) — report + full text of referenced pages →
   the skeptic-facing artifact: claims each backed by 1–4 **verbatim quotes**
   with exact refs (paraphrase in the quote field is forbidden; unquotable
   claims must be weakened or dropped), per-claim strength and honest
   caveats, **lineage trajectories** in canonical order (Tanakh → Bible →
   Quran) labeling each stop origin/restatement/transformation/counterpoint —
   describing *textual* relationships only, never asserted historical
   dependence — and pairwise relationships the trajectories miss.

**Concept-signature extensibility (built in, dormant until ingestion runs):**
the query index attaches each page's section signature from the latest run
automatically — page entries shown to gap/report/evidence carry
`signature: covenant_as_conditional_relationship 0.45, ...` once
`section_concepts` has rows. The evidence role may then add `type:
"signature"` support ("independent analysis grouped these passages under one
concept") but is explicitly barred from letting it substitute for a quote.
The concept hash needs no separate plumbing: signatures ARE the hash, joined
per-section at query time. Post-ingestion, probe/gap can also gain
concept-name query terms — a prompt edit, not a code change.

Timing on the 3060: probe and report run non-thinking (~10–30 s), gap and
evidence think (~60–90 s each) — roughly 3–5 minutes per query end to end.

### Cloud-hosted query agents (`--cloud` / `make query CLOUD=1`, added 2026-07-17)

**Why queries go cloud while ingestion stays local.** The query pipeline's
quality ceiling is context: qwen3:8b's 8k `num_ctx` forces the tight char
budgets above, and truncation-by-fusion-score is where good pages die.
Queries are also the *opposite* workload from ingestion: low-volume,
interactive, quality-sensitive — a few cents each versus 2,348 × 2 agent
calls for a full ingestion pass (which stays local for cost, and because the
model-bias research direction needs local, controlled models anyway).

**Per-role models** (proposed split was probe=gpt-4.1-mini, gap=gpt-4o,
report=gpt-4.1, evidence=gpt-4o; adopted split replaces 4o with 4.1):

| role | model | why |
|---|---|---|
| probe | gpt-4.1-mini | small planning task; structure matters more than depth |
| gap | gpt-4.1 | reads the whole stage-1 context; long-context reasoning is the job |
| report | gpt-4.1 | writing quality over curated context |
| evidence | gpt-4.1 | verbatim-quote fidelity across many pages — the least forgiving task |

Rationale for dropping gpt-4o: gpt-4.1 beats it on instruction following and
long-context work, has 1M context vs 128k, and costs less ($2/$8 vs
$2.5/$10 per Mtok) — there's no dimension on which 4o wins for these stages.

**Mechanics:** `--cloud` swaps only the agent calls (OpenAI chat completions
in `json_object` mode, key from `.env`, seed passed best-effort, temperature
0.4 fast / 0.2 reasoning stages). Retrieval, fusion, budgets logic, gates,
and bge-m3 embeddings are unchanged and local — the corpus never leaves the
machine; only prompts (which contain retrieved page text) go to the API.
Char budgets scale ×3 (stage-1 24k, report 33k, evidence 30k). Grammar-
constrained decoding isn't available server-side the way Ollama enforces it,
so format safety rides on the existing parse + retry guards — in practice
gpt-4.1-class models are more format-reliable than the local 8B, not less.
Query harnesses/evals are deferred until concept signatures exist (they
change what queries can do and would invalidate any baseline).

### Passage lookups + calibration (external review response, 2026-07-17)

An external review of a portal session on comparative violence/gender
questions (`reviews/REVIEW_GROK_QUERY_SESSION_2026-07-17.md`) found the gap
agent *naming* missing passages precisely (At-Tawba 9:5, An-Nisaa 4:34) while
the pipeline gave it no way to fetch a passage it already knew — only more
search queries, which must survive BM25/embedding ranking. Famous passages
are exactly where the model's parametric knowledge (book + verse) beats
search. Four changes:

1. **Lookup arm** (probe + gap, up to 12 each): explicit refs like
   "At-Tawba 9:5" or "Deuteronomy 21:10-14" resolve deterministically to
   pages (`PassageLookup`: book-name normalization, Arabic definite-article
   assimilation An-/Al-/At-, transliteration vowel collapse, "Quran N:M"
   numeric form, verse-range overlap) and enter fusion with a bonus score
   that guarantees survival through truncation. A gap lookup may re-seat a
   gate-dropped page: a deliberate by-reference request outranks a bad
   first-pass relevance score.
2. **Corpus outline** (~3.4k chars, every book + section count) shown to the
   probe so lookups and plans are grounded in what exists.
3. **Per-source floor in truncation** (2 pages) — best-first packing can no
   longer squeeze a tradition out of the context window.
4. **Report calibration rules**: comparative/superlative conclusions must be
   marked provisional *in the executive summary* when the gap report names
   missing material ("in the retrieved evidence, X…"), and claims about what
   a text "condones" must classify each passage as commands / permits-
   regulates / narrates / divine-agency / condemns — regulation is neither
   endorsement nor absence.

Retest of the reviewed query: probe looked up Deuteronomy 7 / Joshua 6 /
1 Samuel 15 / At-Tawba 9 / Al-Anfal 8 directly, gap fetched At-Tawba 9:5 and
An-Nisaa 4:89 by name, and the report quoted the Sword Verse verbatim with
its conditional framing and led with "In the retrieved evidence… this
comparison is provisional". The specific misses the review cited are closed.

### Cloud-hosted ingestion (`make agent-resume CLOUD=1`, added 2026-07-17)

Same `--cloud` pattern as queries: agents on OpenAI, **embeddings stay local**
(bge-m3 via Ollama — light VRAM). Split: router=`gpt-4.1-mini`,
classifier=`gpt-4.1`. Resume stamps `switched_to_cloud_at` into `runs.params`
when a local run continues on cloud (mixed-model mid-run is accepted when the
goal is finishing the atlas, not a controlled local reading). Est. ~$50–60 for
remaining sections; sequential wall clock ~6–8h vs ~40h on the 3060. Parallel
section workers are deferred (shared registry needs commit locking).

### Grok (xAI) as a second cloud provider (added 2026-07-17)

`--cloud` now takes a provider: `--cloud openai` (default, bare `--cloud`
unchanged) or `--cloud grok`; Makefile-side `PROVIDER=grok` on `agent-run`,
`agent-resume`, and `query`. Purpose: a third concept space for the
model-interpretive-bias research (qwen3 local, GPT-4.1, Grok) — different
labs, different RLHF regimes, same corpus, same prompts, same invariants.

**Model split** (xAI lineup as of 2026-07: grok-4.5 flagship 500k ctx $2/$6
per Mtok; grok-4.20 reasoning/non-reasoning 1M ctx $1.25/$2.50; no
mini-class budget tier exists):

| job | model | why |
|---|---|---|
| ingest router | grok-4.20-0309-non-reasoning | wide-recall screening; cheapest capable tier |
| ingest classifier | grok-4.5 @ reasoning_effort=low | the interpretive organ gets the flagship; low is the token-efficient reasoning tier |
| query probe | grok-4.20-0309-non-reasoning | small planning task |
| query gap / report / evidence | grok-4.5 @ reasoning_effort=low | long-context reasoning, the writer's voice, verbatim-quote fidelity |

**Reasoning-effort measurements** (real Genesis-1 classifier prompt,
2026-07-17): grok-4.5's reasoning cannot be disabled; default is high.
high ran 41–47s (~2.1k hidden reasoning tokens, billed as output), low ran
31s (~1.5k) with the *same* 5-assignment sum-1.00 signature; medium was
slowest (52s) with no visible gain. grok-4.20-0309-reasoning rejects the
effort knob (HTTP 400); grok-4.20-0309-non-reasoning answers in 9s and was
briefly adopted as the classifier for speed, then reverted by operator call:
**quality and token efficiency outrank wall clock** — overnight is overnight,
and the flagship's reading is the point of the Grok concept space.
`cloud_chat` pins `reasoning_effort=low` on every grok-4.5 call
automatically, and only on 4.5 (other grok models error on the parameter).

**Cost** (full 2,348-section pass, $100 credit budget): router ~21M in /
1.2M out ≈ $29; classifier ~12M in / ~5M out (incl. reasoning) ≈ $55 →
**~$85 total**, ~31s/section → ~21h wall clock. Queries cost cents each;
~$10–15 of headroom remains for the Grok-side query/framing-diff experiments.

**ETA robustness**: the ingestion ETA projects from a rolling *median* of
the last 60 section durations, and sections slower than 4× the median
(network-backoff stalls) are counted and displayed but never enter the
estimate — one rare 10-minute DNS stall says nothing about future
throughput, so it must not drag the projection the way a straight mean
would. If stalls become frequent, the median itself degrades and the ETA
honestly reflects it.

**Mechanics.** xAI's API is OpenAI-compatible; `atlas_lib.cloud_chat`
dispatches on a provider table (URL + key env var: `OPENAI_API_KEY`,
`SPACEXAI_API_KEY`). The grok payload is minimal — model, messages,
`response_format: json_object` — because grok reasoning models reject some
OpenAI sampler params and seed support is undocumented; determinism rides on
the retry guards alone. **No separate context management needed**: grok-4.5's
500k context dwarfs our ×3 char budgets, which stay identical across
providers on purpose — the bias comparison must not be confounded by
different context sizes. Embeddings and retrieval stay local as always.

**Fresh concept space required** (`make db-fork` → `db/atlas_grok.db`): the
`Registry` loads every active concept regardless of `run_id`, so a Grok run
on the main DB would inherit the GPT-4.1 vocabulary instead of minting its
own — exactly the signal the open-registry bias design needs. Fork copies
the DB, keeps sections/pages/embeddings, wipes `concepts` /
`section_concepts` / `runs`. Overnight recipe:

```
make db-fork
make agent-run DB=db/atlas_grok.db PROVIDER=grok
```

**Portal**: a model selector (GPT-4.1 / Grok 4.5 / local, offered per
available API key) rides each query; mid-session switches append a
`model_switch` event to `session_log.jsonl` — the same session context
answered by different models is bias-comparison data, so it is part of the
record, not a config change.

---

## 9. Concept graph edges — connascence taxonomy (Phase 4, refined 2026-07-16)

**Status: BUILT (2026-07-18)** — `scripts/build_edges.py` + `mk/87_graph.mk`,
first materialization on the completed GPT-4.1 run (`run_20260717_105303`):
25,229 edges in ~2 s (structural 3,057 · temporal 2,345 · conceptual 18,539
at cutoff 0.35 · co-occurrence 1,092 at NPMI≥0.10/count≥3 · co-variance 196
at |r|≥0.40/joint≥5). The build is pure SQL/numpy — no LLM, no Ollama, no
embeddings — so it can run alongside a live ingestion on another DB file.
Edges are keyed by `run_id` + `method`, so the Grok and Qwen runs will get
their own diffable edge sets from the same builder.

One discovery-queue refinement earned during the first build: ranking by
source *tradition* misleads — Tanakh↔WEB-OT pairs are "cross-tradition"
(Judaism vs Christianity) but the same scripture in two languages. The queue
now ranks by **scripture family** (quran / hebrew_bible / new_testament) and
dedupes language twins (Job(he)↔X and Job(en)↔X are one finding). First
queue (top 150, all cross-family): 102 hebrew_bible↔quran, 40
hebrew_bible↔new_testament, 8 new_testament↔quran — including Luke 1 ↔
Maryam 19 (both annunciations, found blind from signatures alone) as a
face-validity anchor, and 2 Cor 1 ↔ Ash-Sharh/Ad-Dhuhaa ("divine consolation
of the prophet") as the uncatalogued kind the queue exists to surface.

### 9.1 Two node types, five edge kinds

The graph is heterogeneous. **Section nodes** (2,348 of them) and **concept
nodes** (however many the registry stabilizes at) are already in the DB;
`section_concepts` is itself the bipartite section↔concept edge set with
weights — no new work needed there. The derived edges, borrowing the
*connascence* framing (kinds of coupling, weakest/most-visible first):

| kind | endpoints | derived from | needs Phase 3? |
|------|-----------|--------------|----------------|
| `structural` | section↔section | shared book, adjacency, explicit cross-reference / parallel-passage tables | no |
| `temporal` | section→section | `seq` reading order per source; Quran revelation order (Meccan/Medinan already in `metadata`, full order index derivable from Tanzil metadata on disk) | no |
| `conceptual` | section↔section | signature overlap `sim(a,b) = Σ_c min(w_a[c], w_b[c])` above cutoff | yes |
| `co_occurrence` | concept↔concept | concepts appearing together in section signatures; PMI-normalized so ubiquitous concepts don't dominate | yes |
| `co_variance` | concept↔concept | correlation of the two concepts' weight vectors over all sections (each concept is a sparse vector indexed by section — exactly the concept hash transposed) | yes |

`co_occurrence` and `co_variance` are deliberately distinct: co-occurrence
asks "do these concepts appear in the same sections at all?" while
co-variance asks "when both appear, do their influences rise and fall
together?" Two concepts can co-occur often but anti-covary (one displaces the
other within a signature) — that pattern is itself interesting, e.g. law and
mercy trading weight across a book.

### 9.2 Schema: one table, dormant until unblocked

```sql
edges (
  edge_id    INTEGER PRIMARY KEY,
  kind       TEXT NOT NULL,        -- structural | conceptual | temporal |
                                   -- co_occurrence | co_variance | ...
  src_type   TEXT NOT NULL,        -- section | concept
  src_id     TEXT NOT NULL,
  dst_type   TEXT NOT NULL,
  dst_id     TEXT NOT NULL,
  weight     REAL NOT NULL,
  metadata   JSON,                 -- per-kind payload: shared concepts, PMI,
                                   -- r-value, n-overlap, direction...
  run_id     TEXT,                 -- signature run the edge derives from
  method     TEXT                  -- versioned builder: "minsum_v1", "pmi_v1"
)
```

Design choices, in the same spirit as the rest of the schema: **edges are
derived, never agent-authored**, so the whole table can be dropped and
rebuilt from `section_concepts` — it's a materialization, not a source of
truth. `run_id` + `method` make edge sets diffable across signature runs the
same way `runs` layers concept assignments. New edge kinds are a new `kind`
string and a builder function, not a migration.

### 9.3 Builder shape

`scripts/build_edges.py --kind all --run <run_id>` + `mk/87_graph.mk`. Each
kind is a pure SQL/numpy pass (no LLM): structural and temporal are trivial
joins; conceptual is a sparse min-sum over the signature matrix (2,348 ×
|registry| — small); co-occurrence and co-variance operate on the transposed
matrix. Thresholds (overlap cutoff, PMI floor, |r| floor, minimum
co-occurrence count before an r-value is trusted) live in the Makefile as
tunable knobs, mirroring τ.

### 9.4 The discovery queue — why this is the point

The briefing's real prize is edges **scholars haven't catalogued**. Known
parallel passages (synoptic gospels, Torah retellings in the Quran,
Chronicles/Kings) will dominate any similarity ranking — they're true but
uninteresting. So the flagship artifact is a *contrast* query:

> rank section pairs by `conceptual` weight **descending**, filtered to pairs
> with **no** `structural` edge, preferring **cross-tradition** pairs.

High conceptual coupling with no structural explanation is exactly "the model
found a connection nobody indexed." The concept space is what makes this
possible: raw embedding similarity would surface pairs that merely share
vocabulary, while signature overlap surfaces pairs that share *analysis* —
two sections the classifier independently described with the same weighted
concepts, possibly in different languages with zero lexical overlap. Each
queue entry ships with its evidence (shared concepts, weights, both
rationales from `section_concepts.rationale`) so a human can adjudicate.
Human review of the top-N is the Phase 4 eval.

### 9.5 Roadmap position

```
Phase 0-2  retrieval infrastructure     ✅ done (fetch, ingest, pages, FTS, embeddings)
Phase 3    concept space ingestion      ✅ GPT-4.1 complete (2,348/2,348, 806 concepts)
                                        ⏳ Grok resuming on 82; Qwen3 running on 245
Phase 4    edge materialization         ✅ built + first run (make graph / graph-stats)
Phase 4+   discovery queue review       ⏳ artifacts/atlas/graph/discovery_queue.md
           GraphML export               ✅ make graph-export (Gephi/networkx)
```

The dependency is real, not incidental: a half-ingested corpus would produce
edges biased toward whichever tradition was processed first (the τ curve
means early sections mint concepts and late sections reuse them). Edge
building should only run on a **completed** pass, and the interleaved
processing order (§7) exists partly to protect this property if a run is
ever inspected mid-flight.
