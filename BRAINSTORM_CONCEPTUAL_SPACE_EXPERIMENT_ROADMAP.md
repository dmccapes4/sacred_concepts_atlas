# Brainstorm — Conceptual Space Experiment Roadmap

*Companion to `STRATEGY_SACRED_CONCEPTS_ATLAS.md`. The strategy doc is the
build plan; this is the experiment plan — what questions the concept space
and concept graph can actually answer, ordered by feasibility, and framed
through the learning-loop lens from `reflections/REFLECTION_LARVAL_SPACE_AS_LEARNING_SYSTEM.md`.*

---

## 0. The framing borrowed from the ant colony

The larval-space reflection isolates a structure worth stealing: **a
prediction, an output that is not the verdict, and a settlement that arrives
later through the channel that touches reality.** The failure mode it warns
against is *the output grading itself*. That warning is the spine of this
roadmap, because the concept space has exactly the shape where you could
cheat without noticing.

Map the atlas onto the loop, honestly, checking the three required
properties (develop / output≠verdict / verdict-through-delivery):

| Ant element | Atlas element | Holds? |
|---|---|---|
| **Population space** `L(t)` | the concept registry — concepts with states (usage count, definition embedding, τ-pressure at birth) | yes: concepts *develop* (rise in usage, get merged, get re-weighted) |
| **Hypothesis** `H(t)` | the registry as a bet on "what vocabulary this corpus needs" — an emergent distribution, not any single assignment | yes |
| **Inputs delivered unevenly** | sections arrive in interleaved order; τ(n) meters how much novelty each section is *allowed* to mint | yes — τ is the nutrient valve |
| **Output** `G(t)` | a section's signature (5 concepts, weighted) | this is the trap → see below |
| **Verdict** | does the concept space make *downstream tasks* better — retrieval, discovery, cross-tradition parallels? | arrives later, separately |
| **Damping (genetics)** | τ_max, the merge-pass cosine floor, the min-sum cutoff — slow structural knobs | yes |
| **Volatility (temperature)** | model choice (GPT vs Grok vs Qwen), sampling temperature, ordering | yes |

**The trap, stated plainly:** the concept space is produced by a classifier,
and it would be trivial to "evaluate" it by asking the same class of model
whether the signatures look good. That is the output grading itself — the
one sin the ants avoid. Every experiment below is therefore designed so the
**verdict comes through delivery**: through whether the concept space
improves a task that has its own ground truth or its own independent judge,
never through a concept liking its own reflection.

Three delivery channels give honest verdicts:

1. **Retrieval delivery** — does concept-signal retrieval surface material
   the text arms miss *and* that a relevance gate (or a human) keeps?
   (Already instrumented: `concept_signal_harness.py`, gap-gate survival.)
2. **Parallel-passage delivery** — does the graph rank *known* scholarly
   parallels highly (recall against a catalogue we did not train on) while
   also proposing novel ones a human accepts?
3. **Cross-model delivery** — do independent models (GPT, Grok, Qwen)
   *converge* on the same concepts and edges? Agreement between models that
   never saw each other's output is the closest thing to an outside verdict
   the project has.

Anything that can only be scored by the model that produced it is play, not
evidence — and the reflection's rule is to *say which is which out loud*.

---

## 1. Iteration ladder (feasibility-ordered)

Numbered so we can talk about "iteration N." Each rung names its **delivery
channel** (how it gets an honest verdict) and its **failure mode** (how it
would look good while being wrong).

### Iteration 0 — Concept-space search + graph traversal in the portal *(building now)*

Two new read-only views over the frozen GPT-4.1 space. No ingestion, no
mutation — pure exploration of what already exists.

- **Concept search.** User pastes text (a sentence to ~1000 words). It is
  routed + classified into a query signature (the ingestion router/classifier
  run in "describe, don't commit" mode). Results = **sections ranked by
  signature similarity** (min-sum, the same metric the graph uses),
  presented as collapsible cards: title, tradition, score, matched concepts,
  best page preview.
- **Graph traversal.** A concept (or section) node in the center; edges to
  neighbors with metadata (edge kind, weight); click a neighbor to re-center.
  Concept↔concept via co-occurrence/co-variance; section↔section via
  conceptual/structural.
- **Delivery channel:** human eyes — does pasted text about mercy actually
  land on the mercy sections? Cheap, immediate, and the operator is an
  independent judge of relevance.
- **Failure mode:** the classifier flatters the paste by minting a
  convenient signature. Guard: in search mode the classifier may only
  *select from existing concepts*, never propose new ones — it describes the
  paste in the corpus's own vocabulary or admits it can't.

### Iteration 1 — Parallel-passage recall benchmark

Assemble a small gold set of *catalogued* cross-references (a few dozen:
synoptic parallels, Torah↔Quran retellings, Kings↔Chronicles doublets,
known Psalm↔Psalm pairs). Score the `conceptual` edge ranking's recall of
them, and separately measure how many top-ranked *no-structural-edge* pairs
a human marks as genuine.

- **Delivery channel:** an external catalogue we did not build the concept
  space against. This is the cleanest verdict available — recall against
  ground truth the model never saw.
- **Failure mode:** gold set curated to match what the space already does.
  Guard: build the gold set from a standard cross-reference (e.g. a public
  parallel-passage table) *before* looking at the edge ranking.

### Iteration 2 — Cross-model concept agreement

The real prize behind the GPT/Grok/Qwen runs. Match concepts across two
runs by definition-embedding cosine, then measure signature agreement per
section (mean min-sum overlap of matched signatures). High agreement =
signatures reflect the *text*; low = they reflect the *model*.

- **Delivery channel:** models that never saw each other's output. Already
  scaffolded in `eval_harness.py` (inter-run agreement ≥ 0.25 gate).
- **Failure mode:** matching concepts too loosely so everything "agrees."
  Guard: report the agreement curve across cosine thresholds, not one
  number; show disagreement examples (concepts one model split and another
  merged) as the interesting output, not noise.
- **Payoff artifact:** an *edge-set diff* — parallels GPT found that Grok
  missed and vice versa. That diff is the model-bias result made concrete,
  and it is the reason the strategy doc keeps edges keyed by `run_id`.

### Iteration 3 — Concept trajectories (the τ curve as a learning signal)

Treat ingestion order as time and watch a concept's usage accrue. Which
concepts were minted early and stayed load-bearing? Which were minted late
under high τ (genuinely new vocabulary the corpus forced)? This is the ant
loop's own diagnostic: is the hypothesis (registry) settling toward
homeostasis or thrashing?

- **Delivery channel:** internal dynamics, so weakest verdict — treat as
  *diagnostic*, not evidence (the reflection's second use of the lens).
- **Payoff:** a per-run "health" fingerprint that makes GPT vs Grok
  comparable at the level of *how they learn*, not just what they output.

### Iteration 4 — Concept-conditioned generation / query steering *(deferred)*

Let a user pick concepts from the graph and compose a query constrained to
them ("show covenant + divine-mercy sections, exclude ritual-law"). Steering
retrieval by the concept axis rather than by words.

- Deferred because it is the first rung where the space *acts* rather than
  *describes*, and we want iterations 1–2 to have delivered a verdict on
  whether the space is trustworthy before we build UI that assumes it is.

---

## 2. What Iteration 0 commits to (so we can iterate without regret)

- **Read-only over a frozen run.** The portal never writes to the concept
  space. Search classification is ephemeral (describe-mode), logged to the
  session, never committed. This keeps the "output can't grade itself"
  discipline: exploration cannot mutate the thing being explored.
- **Reuse, don't re-implement.** Section ranking uses the same min-sum as
  `build_edges.py`; graph traversal reads the `edges` table directly;
  classification reuses the ingestion router/classifier in a describe-only
  mode. One metric, one edge set, one classifier — no shadow logic that
  could drift from the real pipeline.
- **Everything logged.** Concept searches and graph walks append to the
  session log (`log.jsonl`, existing convention) so a session is a
  reproducible trace — the same receipt-keeping the reflection closes on.

---

## 3. Open questions to revisit (not blocking Iteration 0)

- Should graph traversal expose *all five* edge kinds or only the two
  interesting ones (conceptual, co-occurrence)? Start with a kind filter,
  default to conceptual+co_occurrence, let the operator toggle.
- Section-node vs concept-node traversal share a canvas — do they confuse,
  or is "click a section to see its concepts, click a concept to see its
  sections" the natural bipartite walk? Iteration 0 answers this by feel.
- When the Grok/Qwen runs finish, the portal should let the operator pick
  *which run* to explore — the run selector is the entry point to
  Iteration 2's comparison UX. Wire the plumbing now (run_id param), expose
  the selector later.

---

*The concept space is a hypothesis the corpus is betting on. Iteration 0
lets us look at the bet. Iterations 1–2 are where the verdict comes back
through delivery — recall against a catalogue, and agreement between models
that never met. Keep the output and the verdict in different rooms, and the
loop stays honest.*
