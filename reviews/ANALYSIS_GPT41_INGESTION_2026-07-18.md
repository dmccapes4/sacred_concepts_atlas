# GPT-4.1 Ingestion Run — Completion Analysis

**Run:** `run_20260717_105303` · router `gpt-4.1-mini` / classifier `gpt-4.1` ·
embeddings local `bge-m3` · τ₀ 0.55, τ_max 0.92, k 150 · order `interleaved` ·
started 2026-07-17 17:53Z, finished 2026-07-18 09:50Z (interrupted twice by
infrastructure — an Ollama embed stall and a host RAM failure — resumed both
times, zero data loss; `PRAGMA integrity_check` clean on both DBs).

## 1. Headline numbers

| metric | value |
|---|---|
| sections signed | **2,348 / 2,348** (929 Tanakh · 1,189 Bible · 230 Quran) |
| registry (active concepts) | **806** |
| weight-sum violations | **0** of 2,348 (invariant held everywhere) |
| signature size | median 4 (histogram 1:9 · 2:95 · 3:396 · 4:848 · 5:778 · 6:216 · 7:6) |
| concepts minted / proposals gated | 806 / 1,264 → **61% reject rate** |
| final τ | 0.862 (asymptote 0.92) |
| singleton concepts (used once) | 78 (**9.7%**) |
| median section time | 18s (p90 27s) |

## 2. The minting curve, and one anomaly worth understanding

New concepts per quarter of the run: **387 → 234 → 55 → 130**.

The first three quarters are the textbook rising-τ curve: mint freely while
the vocabulary is cold, reuse increasingly as it warms. The fourth-quarter
resurgence (55 → 130) looks like a τ failure but is actually a **corpus
structure effect**: interleaved order exhausts the Quran lane at ~section
690 and the Tanakh lane at ~2,088, so the final ~260 sections are pure
Bible — and in `seq` order that tail is the NT epistles and Revelation,
which carry genuinely new theological vocabulary (christology, ecclesiology,
parables). The registry agrees: 165 concepts are Christianity-only, by far
the largest unique bucket ("Christ's humility as model for Christian
unity", "apostolic communal life and economic sharing", "parable of
compassionate neighbor as ethical exemplar"). Late minting under a 0.86 τ
bar is the gate working, not leaking — those concepts cleared a high bar
because nothing in Torah or Quran covers them.

Implication for the bias research: the interleaved order still ends
single-tradition. A model's late-run minting style (under max τ pressure)
is only exercised on Bible material. The `temporal` ordering arm, or a
length-balanced interleave, would remove this asymmetry if it ever matters.

## 3. Tradition structure of the concept space

| | distinct concepts | sections | concepts per section of text |
|---|---|---|---|
| Christianity | 768 | 1,189 | 0.65 |
| Judaism | 595 | 929 | 0.64 |
| Islam | 175 | 230 | 0.76 |

Normalized by corpus size, the three traditions have comparable conceptual
density — the raw asymmetry (768 vs 175) is corpus length, not model
attention. **125 concepts appear in all three traditions**; that set is the
cross-tradition backbone Phase 4 edges will light up ("divine lordship and
mercy", "prescribed social justice and charity obligations", "faithful
conduct as criteria for eternal inheritance").

The unique buckets read like each tradition's actual distinctives, which is
a face-validity check on the whole pipeline: Judaism-only is dominated by
ritual/legal specifics (nesting birds, unauthorized ritual fire, post-exilic
separation), Islam-only by Quranic particulars (Barzakh, Laylat al-Qadr,
Friday prayer), Christianity-only by parables and christology. Only 9
concepts are Judaism-only despite 929 sections — Torah vocabulary is almost
entirely shared with the Christian OT pages, as it should be (same text in
two languages; the cross-lingual acid test passing at scale).

Top concepts by usage are pan-corpus moral-theological threads, led by
"divine warning and moral responsibility" (366 sections, 15.6% of the
corpus) — heavy but not degenerate; sum-to-1.0 weighting kept it from
becoming a gravitational hub (median weight share stays distributed:
signature median is 4 concepts).

## 4. Health events from the decision log

| event | count | reading |
|---|---|---|
| `verse_not_verbatim` | 1,790 | known-too-strict exact-substring check; prior sampling showed ~79% pass after diacritic/punctuation normalization. Enforce-after-normalize remains the queued fix. |
| `semantic_retry` | 41 (1.7% of sections) | weight-sum/size guard resamples — healthy |
| `unusable_assignment` | 41 | dropped assignments missing name/definition — logged, no data loss |
| `offlist_concept_id` / `recovered_concept_id` | 21 / 10 | ID-resolution chain working |
| `agent_retry` | 10 | transient API/format errors absorbed |
| `run_aborted` | 1 | the Ollama embed stall (since fixed with transport backoff) |

**One soft invariant breach to note:** 15 sections (0.6%) sit outside the
2–6 signature-size guard (9 singles, 6 sevens). These are the
accept-last-attempt-after-retries policy doing what it says. They're logged
and harmless, but the merge/QA pass should peek at the nine 1-concept
sections — a single-concept signature means min-sum similarity to them is
all-or-nothing.

## 5. Registry health vs the progress-report concerns

The 2026-07-18 system review flagged registry path-dependence and a missing
merge pass. Current numbers soften but don't dismiss that: singletons fell
from 16% (at the 69% snapshot) to 9.7% at completion — late sections reused
rather than minted, as designed. The merge-pass input queue is therefore
smaller than feared: 78 singletons plus whatever definition-cosine > 0.90
pairs exist. That pass is still the gate before trusting `co_occurrence` /
`co_variance` edges, but it's an evening of adjudication, not a project.

## 6. What this unblocks

The completed signature pass is the input Phase 4 was blocked on:

1. **`build_edges.py`** — `conceptual` (min-sum overlap), `co_occurrence`
   (PMI-normalized), `co_variance` per the taxonomy in the strategy doc §9.
2. **Discovery queue** — high conceptual coupling with no structural link,
   cross-tradition preferred. The 125 shared concepts are the seed bed.
3. **Query-time signatures** — all 2,348 sections now carry signatures, so
   the portal's signature annotations and the evidence stage's
   `type: "signature"` support are fully live.
4. **Bias baseline** — this registry is the GPT-4.1 reading, frozen. The
   Grok run (135/2,348 at crash, resumable) and the Qwen3 run build the
   comparison layers against it.

Recommended order stands: merge pass → inter-run agreement gate → edges.

## 7. Operational note (context for the timestamps)

The run survived two unrelated infrastructure failures: an Ollama embed
timeout that killed the process pre-backoff-fix (~00:22), and a host freeze
at ~00:31 that turned out to be RAM failure (48GB → 16GB detected on
reboot; two of three DIMMs dead — see crash diagnosis in the session
notes). The final 131 sections ran after reboot on 16GB, at unchanged
per-section times — the pipeline's working set (page-embedding matrix
~30MB + SQLite) is small, so the atlas itself is indifferent to the RAM
downgrade. Both DBs pass integrity checks; decisions.jsonl is contiguous
across the interruptions.
