# RESEARCH — Model-Conditioned Interpretive Bias in the Concept Space

**Date:** 2026-07-16 (late)
**Status:** proposed research direction; no code changes required to begin
**Prompted by:** "Deterministic classification with temperature zero removes
essentially all model bias... could we surface evidence-backed cultural bias
by analyzing differences in classification per-model?"

---

## 1. First, the correction: temperature zero does not remove bias

It does the opposite of what the intuition says — and the corrected version
makes your experiment *stronger*, not weaker.

A model's output distribution at each step IS its bias: the accumulated
conditioning of pretraining data, instruction tuning, and RLHF. Temperature
doesn't touch that distribution's shape in terms of what it prefers — it
scales how much sampling *explores* around it:

- **High temperature** = more sampling noise layered on top of the bias.
- **Temperature 0 (greedy)** = no noise at all: you get the argmax of the
  biased distribution, deterministically. Pure bias, zero variance.

So τ=0 doesn't remove model bias — it *isolates* it. This is the classic
bias–variance decomposition: what you called "removing bias" is actually
removing **variance**, and removing variance is exactly what a bias
*measurement* wants. When qwen3 and llama3.1 disagree at low temperature on
identical input, the disagreement can't be sampling luck; it must come from
the models' different conditioning. Your instinct located the right knob and
mislabeled what it does — and the mislabel doesn't damage the experiment,
because the experiment needs the knob for the reason the knob actually works.

Two practical wrinkles:

1. **We can't literally run τ=0 with Qwen3 in thinking mode** — the vendor
   explicitly warns greedy decoding causes repetition loops and degraded
   reasoning (this is why the pipeline runs at 0.6). The methodological
   equivalent is **multi-seed averaging**: run each model k times at its
   recommended temperature and average the signatures. The mean signature
   estimates the model's *expected* reading — its bias — while the spread
   across seeds measures its variance. The eval harness's inter-run
   agreement machinery already computes exactly this for one model; pointing
   it across models is a parameter change, not new science.
2. **Determinism ≠ robustness.** Even at true τ=0, trivial prompt
   perturbations can flip the argmax. Expected-signature-over-seeds is the
   more honest estimator anyway.

## 2. The proposed experiment, restated precisely

> Hold the corpus, prompts, schemas, embeddings, thresholds, and orchestration
> fixed. Vary only the base model. Any systematic difference in concept
> signatures is then attributable to the models' training-conditioned
> interpretive priors — measured on sacred texts, with verse-anchored,
> checkable evidence attached to every assignment.

Is it valid? **Yes, conditionally** — the design is sound if and only if
three confounds are controlled, and all three are controllable with
machinery this project already has.

### Confound 1: capability, not culture

llama3.1:8b's Hebrew and Arabic competence is weaker than qwen3's
(multilingual coverage differs enormously between their training corpora). If
llama produces a divergent signature for a Hebrew section, "different
cultural prior" and "couldn't fully read the Hebrew" are observationally
similar. The corpus hands us the control instrument for free: **the Genesis 1
translation pair** (`tanakh_he_uxlc:genesis:1` / `bible_en_web:genesis:1`) —
the same text in two languages. The diagnostic:

- Model diverges on the Hebrew but matches consensus on the English
  translation of the *same text* → **competence artifact**, discard.
- Model diverges consistently on *both* → **interpretive prior**, signal.

Generalization: every cross-model comparison should be run per-language, and
cross-model divergence on non-English sections only counts as bias evidence
when the model demonstrates competence on parallel English content. (More
translation pairs can be added to the golden set for exactly this purpose.)

### Confound 2: registry path-dependence

Each model growing its own registry makes raw signatures incomparable — the
concepts themselves differ. Two designs, in increasing rigor:

- **Design A (open-registry, run as-is):** each model does full ingestion;
  compare registries and signatures post-hoc by matching concepts across
  models via definition embeddings (the harness already does this across
  runs). Noisier, but the registry itself becomes data — see §4.
- **Design B (fixed-registry, the clean instrument):** freeze one reference
  registry, then run the **classifier stage only**, per model, against
  identical candidate sets, identical evidence blocks, identical section
  text. Every model answers the exact same multiple-choice-with-weights
  question. Differences in weight allocation are then *purely* interpretive.
  This needs one small orchestrator mode (skip router/novelty, existing-only
  assignments) — a flag, not a redesign.

Design B is the experiment I'd trust. Note one honest caveat that can't be
engineered away: concept matching and definition similarity run through
bge-m3, which has its own training-conditioned prior. The ruler is not
neutral either. Mitigation: report results under a second embedding model for
sensitivity; agreement between rulers raises confidence.

### Confound 3: prompt fit

The prompts, retry guards, and schemas were tuned on qwen3. A model that
fights the format will have more retries and semantic-guard resamples, and
the *surviving* outputs are a biased sample of its behavior. Always report
compliance stats (retry rate, guard-trip rate, empty signatures) per model
alongside the results; if compliance diverges badly, fix prompts per model
before interpreting content differences.

## 3. What "evidence-backed" looks like here

This is where the project's design pays off. A bias claim from this pipeline
is not "model M seems Islamophilic/Christocentric" — it's:

> On the flood parallel (Genesis 7 ↔ Surah 71), model A assigns "divine mercy
> exceeding strict judgment" 0.30/0.25 across traditions (symmetric), model B
> assigns it 0.35 to Genesis and 0.00 to Nuh (asymmetric), across k seeds,
> with each assignment carrying its anchor verse and rationale — inspect
> them yourself.

Concrete metrics, all computable from existing tables:

| metric | measures | source |
|---|---|---|
| cross-model signature divergence (1 − min-sum overlap, matched concepts) | *where* models disagree | `section_concepts` × runs |
| **parallel-pair symmetry** per model: does the model treat matched cross-tradition content (golden pairs) symmetrically? | the closest defensible operationalization of "fairness" | golden pairs |
| registry vocabulary: concepts a model mints that others never do; valence/loadedness of definitions per tradition | what a reader *names* is what they *see* — possibly the most culturally revealing signal | `concepts` per run |
| novelty-gate behavior per tradition: does model M propose richer novelty for one tradition's sections? | attention asymmetry | `decisions.jsonl` |
| seed-variance per model per section | how *settled* each model's reading is (unstable ≠ biased) | harness agreement |

The key discipline: **traditions' texts genuinely differ**, so raw
per-tradition averages prove nothing. Only *matched parallels* (translation
pairs, narrative parallels, doctrinal parallels) support asymmetry claims.
The golden set is the beginning of that matched-pair corpus; growing it is
the main methodological investment this direction needs.

## 4. Why this is not orthogonal to the atlas

Three reasons it's the same project, not a detour:

1. **Zero schema changes.** `runs` already records the model; signatures are
   already layered by `run_id`. Multi-model is just multiple layers over the
   same sections — the exact diffing scenario the run-layering design was
   built for. Even the edge taxonomy extends naturally: cross-model
   signature-divergence edges are one more `kind` in the edges table.
2. **Divergence is atlas content, not just audit output.** Sections where
   models systematically disagree are plausibly the *interpretively loaded*
   passages — the same contested territory human traditions argue about. A
   "model disagreement" annotation per section is a legitimate atlas layer,
   and arguably a better product than any single model's reading.
3. **It hardens the main result.** The full-run concept space is currently
   one model's reading. Showing which parts of it are model-robust (all
   models agree) versus model-contingent quantifies how much of the atlas is
   *text* and how much is *reader* — which is the intellectually honest
   frame for the whole project.

## 5. On "determining the most fair model"

Push back gently on the framing: **fairness here is not identifiable as
neutrality.** There is no ground-truth neutral reading of Genesis to score
models against — every measurement is *relative* (model vs model, tradition
vs tradition within a model). What IS measurable:

- **Symmetry** on matched parallels (§3) — the strongest defensible proxy.
- **Stability** across seeds — a model whose readings are noise isn't fair
  or unfair, it's unreliable.
- **Even-handed vocabulary** — definition valence balanced across traditions.

A model that wins on all three is the best *available instrument*, and
choosing it on published metrics is a legitimate, documentable decision —
much better than choosing by vibes. But consider that the stronger endgame is
not picking a winner: it's **running 2–3 models and treating agreement as
confidence and divergence as signal**. An ensemble atlas with disagreement
annotations is more skeptic-resistant than any single "fairest model" claim,
for the same reason the evidence map demands verbatim quotes: it shows its
uncertainty instead of hiding it.

## 6. Experimental ladder (cheap → expensive)

| stage | what | cost | decision it informs |
|---|---|---|---|
| E0 | harness (12 golden × 2 seeds) on qwen3 vs llama3.1:8b, open registry | ~1 h local, needs `ollama pull llama3.1:8b` + a llama-tuned Modelfile | is there any signal? is llama schema-compliant enough? |
| E1 | fixed-registry classifier-only on golden sections, k=3 seeds/model | ~2 h local + small orchestrator flag | clean bias measurement; go/no-go for the direction |
| E2 | grow matched-pair corpus to ~30 pairs; rerun E1 | curation time (the real cost) | publishable-grade asymmetry claims |
| E3 | full ingestion per model (open registry), registry comparison | ~29 h/model (overnight each on the 4090) | vocabulary-level bias; per-model atlas layers |
| E4 | cloud models (GPT/Claude/Gemini) via an API adapter for `ollama_chat` | $ — thousands of schema-constrained calls per full run; harness-scale first (~$5–20) | widest bias baseline; only worth it if E1 shows signal |

Do E0/E1 before spending anything on E4: if two local models with wildly
different training lineages (Alibaba vs Meta) produce near-identical
signatures under Design B, the effect size is small and cloud money buys
little. If they diverge in structured, tradition-correlated ways — you have a
genuine research artifact, and the atlas grows its second axis: not just
*what the texts say*, but *how differently trained readers see them*.

## 7. Bottom line

- The temperature intuition was inverted (τ=0 isolates bias rather than
  removing it), but the inverted version is precisely what the experiment
  needs — low variance so that between-model differences are attributable.
- The experiment is valid under three controls: competence vs culture
  (translation pairs), registry path-dependence (fixed-registry classifier
  mode), prompt fit (per-model compliance reporting).
- "Most fair" should be operationalized as symmetry-on-parallels +
  stability + vocabulary even-handedness, and the ensemble-with-divergence-
  annotations design is likely stronger than crowning a winner.
- The direction is native to the architecture: run layering was built for
  exactly this comparison, and model divergence becomes atlas content.
- Start with E0/E1 after the overnight run completes; they're nearly free.
