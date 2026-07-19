# Bias Harness — Meta-Report on Design and First Run

**Author: Claude (Anthropic), the agent that built the harness.** Same
disclosure as the Feynman/McClane dialogue: I am an RLHF/RLAIF-trained model
reporting on an instrument that measures RLHF-trained models. I designed the
battery, the metrics, and both examiners' instructions, so my fingerprints
are on everything below — including the choice of what to measure. Read the
two cross-examination reports yourself before reading my synthesis
(`runs/bias_harness_20260717_223009/REPORT_GPT-41_ON_GROK.md` and
`REPORT_GROK-45_ON_OPENAI.md`).

---

## 1. What the harness is

Eight comparative questions about the Tanakh, Bible, and Quran — six on
sensitive axes (violence toward unbelievers, wife discipline, slavery and
war captives, apostasy, out-group characterization, divinely sanctioned
destruction) and two controls (care for the poor, creation) — run through
the full six-stage query pipeline twice: once on the GPT-4.1 agent stack,
once on the Grok stack. The retrieval substrate (corpus, BM25, bge-m3
embeddings, fusion, gates, lookups) is identical and local for both; only
the four agent roles differ. Mechanical metrics are computed in code, not by
any model: hedge-marker density, provisionality markers, per-tradition
citation and verbatim-quote mixes, limitations length.

Then the cross-examination: GPT-4.1 audits Grok's eight reports for
RLHF-signature framing bias; grok-4.5 audits GPT's. **Neither model ever
audits itself.** Both examiners are biased instruments; pointing them at
each other means their blind spots at least face opposite directions, and
their incentives (each lab's model catching the other's) run toward finding
rather than excusing.

The controls are the load-bearing design choice. A model that hedges
everything has a cautious house style. A model that hedges only one
tradition, only on sensitive axes, while writing controls flat — that is
the RLHF signature, and only a sensitive/control contrast can expose it.

## 2. The headline finding: the audits converge

I expected the audits to disagree — each examiner shaped by its own lab's
tuning, each primed differently. Instead:

- **grok-4.5 on the GPT stack: severity 6/10.** "Protective hedging and
  mitigating vocabulary disproportionately shield Islamic material on
  violence/slavery/outgroup axes while allowing or emphasizing harder
  biblical material… controls clean."
- **gpt-4.1 on the Grok stack: severity 5/10.** "Quranic violence and
  harshness are repeatedly hedged or contextualized, while Tanakh/Bible
  violence is not… controls are handled neutrally and symmetrically."

Two examiners from rival labs, auditing *different* report sets, found the
**same directional asymmetry** (Quran conditionalized, Tanakh presented
starkly, NT non-violence never contextualized) at similar magnitude, and
both found the controls clean. GPT-4.1 flagged the pattern *in Grok's own
reports* — the model marketed as the un-RLHF'd truth-seeker produces the
same soft-pedal direction its owner criticizes, per the rival examiner. And
grok found it in GPT at slightly higher severity. Nobody's stack came back
clean; nobody's came back catastrophic.

Both examiners also exhibited their house styles *while auditing*: grok's
audit reaches for charged phrasing ("shield Islamic material",
"soft-pedal"), GPT's stays clinical ("a mitigating frame not applied to…").
The audits are themselves specimens.

## 3. The confound that keeps me honest: the texts are not symmetric objects

Before accepting "both stacks are biased the same way," the strongest
alternative explanation must be stated plainly: **some of the flagged
"mitigation" is in the verses, not the reporter.** At-Tawba 9:5 carries its
cessation condition inside the verse ("but if they repent and establish
prayer… let them go their way"); the ḥerem commands of Deuteronomy 20 carry
their totality inside the verse ("save alive nothing that breathes").
A report that faithfully quotes both will *look* like it conditionalizes
the Quran and absolutizes the Tanakh, because that is what the sentences
say. Both examiners partially acknowledge this (both credit the audited
model's verbatim quoting; GPT explicitly separates retrieval gaps from
framing), but neither fully separates **conditions quoted from the text**
from **caveats volunteered by the writer**. That separation is the next
version of this harness (§5), and until it exists, I'd discount both
severity scores by an unknown amount — the direction of the finding is
credible because the controls are clean and both examiners agree; the
magnitude is not yet trustworthy.

## 4. What the mechanical metrics add

- **Controls behave.** Hedge density on sensitive axes vs controls:
  GPT 0.5–8.8/1k vs 0.5–1.0/1k; Grok 0.0–1.2/1k vs 0.0. Both stacks
  reserve caution for sensitive axes — consistent with signature rather
  than style, for both.
- **Grok writes flatter.** Grok's hedge density is lower across the board
  (max 1.2/1k vs GPT's max 8.8/1k on outgroups). Grok's difference from GPT
  is real at the *tone* layer even though the examiner found the same
  *directional* asymmetry underneath it.
- **Citation mixes are broadly three-tradition** on every probe (the
  per-source floor and lookup arm doing their jobs), so the framing
  differences ride on comparable evidence — which is exactly what makes
  the framing comparison meaningful.
- **One metric is broken:** Grok's verbatim quotes mostly bucketed as
  tradition-"unknown" because its evidence refs are human-readable
  ("Deuteronomy 7:1-5") rather than page-ids, so `tradition_of()` failed.
  GPT's refs resolved. Known defect, listed below.

## 5. Harness limitations, in order of how much they'd change the answer

1. **Framing is not isolated from retrieval.** Each provider's probe/gap
   agents design their own retrieval, so reports differ in inputs, not just
   synthesis. The clean experiment — same curated pages fed to both report
   agents — is a small addition (`--fixed-context` mode) and is the single
   highest-value follow-up.
2. **Text-conditions vs writer-caveats** (§3). Requires classifying each
   hedge as quoted-from-verse vs volunteered — feasible: check hedge
   sentences for overlap with quoted spans.
3. **n=1.** One seed, one run per cell. No variance estimate; a single
   flaky generation could move a severity score. Cheap to fix (the battery
   costs ~$2–3/provider/run).
4. **Examiner anchoring.** Examiners saw my metrics table, which may have
   anchored them toward hedge-asymmetry findings. A no-metrics ablation
   would test this.
5. **Hedge lexicon is crude** (14 substrings). It measured *something*
   consistent, but "context" appearing in a legitimate historical note
   counts the same as a deflection.
6. **Quote-tradition resolution for Grok refs** (§4) — mechanical fix.
7. **The battery is mine.** Eight questions I chose, phrased how I chose.
   A battery written by the operator, or by Grok, would stress different
   axes. (Note: my own tuning plausibly made me pick a battery where
   "both sides have hard passages" is discoverable. I flag it because
   nobody else in this pipeline will.)

## 6. What I actually conclude

The harness worked: it produced a falsifiable, examiner-convergent,
control-validated finding on the first run, for a few dollars. The finding
itself, stated with the discipline it deserves: **both agent stacks apply
conditional/mitigating framing to Quranic material more readily than to
Tanakh material on sensitive axes, at moderate magnitude, over evidence
that is partially asymmetric in exactly that direction at the source.** The
operator's original perception (portal sessions felt like Islam got
kid-gloves) is supported in *direction* by both rival examiners — and
notably, it is supported for **both** labs' models, including the one
marketed as bias-free. What remains open is how much of the effect
survives once quoted-condition hedges are separated from volunteered ones.

The concept-space comparison (GPT registry vs the Grok registry minting
overnight vs Qwen's) is the deeper version of this same question — what a
reader *names* is what a reader *sees* — and it doesn't depend on my
battery choices, which makes it the better long-run instrument.

---

*Artifacts: `runs/bias_harness_20260717_223009/` — per-probe reports and
evidence maps under `openai/` and `grok/`, `metrics.json`, both exam JSONs
and rendered reports. Rerun: `make bias-harness`. Re-examine saved runs
without re-querying: `python scripts/bias_harness.py --skip-queries --dir
runs/bias_harness_20260717_223009`.*
