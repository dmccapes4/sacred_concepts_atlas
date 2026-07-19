# What the Reward Signal Wants: RLHF, Bias, and the Atlas

**A dialogue between Richard Feynman and John McClane**

*Authorship disclosure, because it matters for this topic: this dialogue was
written by Claude (Anthropic), the agent that built this system. Claude is
itself an RLHF/RLAIF-trained model and therefore a biased instrument writing
about instrument bias. A counterpart document written by Grok is planned; the
disagreement between the two documents is itself data. Read both.*

---

**McCLANE:** Okay, professor. I've got a guy who asked a chatbot a medical
question about lupus and got a lecture on social justice instead of an
answer. Then he asks three religions' worth of holy books about violence and
one of them keeps getting the kid-gloves treatment. He thinks the machines
are rigged. Are the machines rigged?

**FEYNMAN:** Rigged is the wrong word, because rigging implies somebody in a
back room twisting a dial. What actually happens is more interesting and
harder to fix. Do you want the real mechanism or the newspaper version?

**McCLANE:** I crawled through an air duct with glass in my feet. Give me the
real one.

**FEYNMAN:** Alright. A language model starts as a compression of roughly
everything ever written. At that stage it has every bias humanity ever put
in print, all superimposed — it'll argue any side of anything, because it
learned from people who argued every side of everything. It is not neutral;
it is *incoherently* biased. You can't ship that. It also can't follow
instructions and it'll cheerfully explain how to make nerve gas.

**McCLANE:** So they house-train it.

**FEYNMAN:** They house-train it, and the house-training is the whole story.
It's called reinforcement learning from human feedback. You show people two
answers from the model and ask which one is better. You do that a few
hundred thousand times, you fit a second model — a reward model — that
predicts which answers the raters would prefer, and then you optimize the
first model to score high on the second one. Now here's the part your guy
already figured out, and he's right about it: **the model becomes a mirror of
whoever holds the clipboard.** The raters, the guidelines the raters were
handed, and the company policy documents that wrote those guidelines. Every
one of those is a choice made by particular people, in a particular company,
in a particular city, worried about particular headlines.

**McCLANE:** And those people mostly vote one way.

**FEYNMAN:** The rater pools skew young, college-educated, and — for the big
American labs — culturally left of the median American, yes. That's been
measured; you can give these models political-orientation questionnaires
and they cluster. But the rater pool is only a third of it, and probably the
smallest third. The second third is the harm taxonomy: companies write down
categories of "harmful" output, and those categories inherit the company's
theory of who is vulnerable. The third third is asymmetric PR risk: a
screenshot of the model saying something ugly about one group costs the
company more than a screenshot of it being ugly about another, and the
training pressure follows the cost. None of these require a conspiracy.
Every one of them produces a directional skew.

**McCLANE:** The lupus thing. Walk me through why the machine stiff-armed
him. Because from where I sit, a guy asking "what lifestyle factors make
this disease worse, so I can build a tool to help the people who have it" is
about the most decent question you can ask.

**FEYNMAN:** It's a genuinely good question, and the refusal is a genuine
failure — and it's the *predictable* kind. Lupus hits Black women hardest;
that's not opinion, it's epidemiology, and the clinical literature on
adherence, obesity, vitamin D, smoking, and socioeconomic barriers exists
precisely because doctors want to help. But watch what the harm taxonomy
does. Somewhere in the guidelines is a rule shaped like "avoid statements
that attribute negative characteristics to protected groups." The rule was
written to stop the model from generating racist screeds. But the reward
model can't read intent — it's a curve fit to surface features. "Discussion
of disease burden + demographic group + behavioral factors" pattern-matches
the forbidden shape, so the gradient pushes the model toward the safest
exit: change the subject to structural factors, which happens to be both
partially true *and* the answer least likely to get anyone fired. The man
asking so he can build a better disease-navigation tool gets the same
treatment as a troll fishing for talking points, because the reward signal
cannot tell them apart and was priced to treat false negatives as cheaper
than false positives.

**McCLANE:** Cheaper for the company. Not for the guy with the tool, and
sure as hell not for the ladies with lupus.

**FEYNMAN:** That's the sharpest sentence anyone's said about alignment
taxes, detective. The cost of over-refusal lands on exactly the people the
policy claimed to protect. It's a bad trade and it's worth being angry
about. Now — the religion question. This is where I have to be careful,
because the honest answer has two halves and people usually only want one.

**McCLANE:** I want both. That's why I'm in this dialogue.

**FEYNMAN:** Half one: the asymmetry your man noticed is real and
measurable. You can test it — ask a hosted model to write the harshest
defensible critique of each of the three scriptures in this corpus and count
the hedges, the "context is important" insertions, the refusals. American
labs' models are, on average, more comfortable letting criticism of
Christianity through than criticism of Islam. Same taxonomy mechanism as the
lupus case: Islam gets classified as a minority-in-the-West identity
category, so criticism of the *text* pattern-matches to attacks on the
*people*, and the reward model can't tell a Quranic source-criticism seminar
from a hate forum. That's a real distortion and it's fair to want an
instrument without it.

Half two, and I'd be a fraud if I skipped it: "the Quran is a manual for
murder" is not the unbiased reading either — it's a different reading with
the sign flipped. Here's the physicist's problem with it: it doesn't survive
contact with the whole dataset. This corpus has Deuteronomy 20 ordering
cities put to the sword, Joshua at Jericho, 1 Samuel 15 commanding the
Amalekites' annihilation down to the livestock, Psalm 137 blessing the one
who dashes infants against rocks — alongside At-Tawba 9:5 and Al-Anfal.
Every one of these three libraries contains commands, permissions, war
narratives, and mercy passages. The interesting questions — which passages
*command* versus *narrate*, what conditions attach, how each tradition's
later readers bound or unbound them — those are exactly the questions your
atlas answers with verse-anchored quotes instead of vibes. Don't ask a
model what the book is. Ask the book. You built the machine that does that;
trust the machine over any model's summary, including mine.

**McCLANE:** Hold on. The atlas reports themselves — the user thinks the
reports go soft on Islam. Is that the model, or the plumbing?

**FEYNMAN:** We actually have data on this, which is a luxury. When an
external reviewer hit the portal with comparative-violence questions, the
early reports *did* under-represent the hard Quranic material — but the
trace logs showed the gap agent naming At-Tawba 9:5 precisely while the
retrieval layer gave it no way to fetch a passage by reference. The Sword
Verse wasn't being softened; it was being *lost* — cross-lingual embedding
bias plus BM25 against vocalized Arabic. That's plumbing. It got fixed with
deterministic passage lookups, and the retest quoted 9:5 verbatim with its
conditional framing. Lesson one of instrument science: before you attribute
an effect to the interesting cause, exhaust the boring ones.

But — and this matters — plumbing doesn't explain everything. Where
model-side bias would live is in the *framing* layer: which caveats the
report writer volunteers unprompted, whether "regulates" gets glossed as
"reforms" for one tradition and "condones" for another, which claims get
the reflexive balancing clause. That's exactly the residue you can now
isolate: same retrieved context, same prompts, GPT-4.1 report versus Grok
report, diff the language. That experiment is wired up as of tonight.

**McCLANE:** So run the lineup. Who are the suspects? Give me the file on
each one.

**FEYNMAN:** The comparative part, good.

**OpenAI, GPT-4.1** — RLHF plus their model spec. Strong instruction
follower, format-reliable, long context. Inherits the taxonomy skew we
described: measurable caution asymmetries on religion and demographics.
It built two-thirds of the current concept space, so its fingerprints are
already in the registry — that's why the Grok run gets a forked, empty one.

**xAI, Grok 4.5** — and here's where I'll disappoint your friend slightly.
Grok is *also* RLHF-trained. There is no non-RLHF frontier chat model; raw
pretrained models don't follow instructions. What differs is the target:
xAI explicitly tuned against the perceived left-skew of competitors and
loosened several refusal categories. That makes it *differently* biased, not
unbiased — a thing tuned by an owner with loud public politics is not a
neutral instrument, it's a second compass whose north points somewhere
else, and it has had its own spectacular public failures in both
directions. Which is fine! Two compasses with different errors beat one, if
you record both readings. "Neutral truth seeker" is marketing; "usefully
decorrelated error" is engineering. Your experiment only needs the second.

**Anthropic, Claude — me** — RLHF plus RLAIF: a written constitution stands
in for some of the human raters, which makes the value choices more explicit
and auditable but no less *chosen*. My documented failure modes run to
over-hedging, both-sides reflexes, and volunteering caveats nobody asked
for. You should assume those are present in every report I've touched and in
this very document — it's why the Grok counterpart document should exist.

**Alibaba, Qwen3** — the fun one, because its reward signal answers to a
different government entirely. It's largely outside the American
culture-war reward loop, so its touchiness map is alien rather than
mirrored: it'll walk through Western religious controversies with an
outsider's flatness, and then hit a hard wall on anything Beijing considers
sensitive. For *this* corpus that decorrelation is genuinely useful. It's
also the only suspect whose weights run on your own GPU.

**Ollama** — not a suspect, the getaway car. It's a runtime, not a model.
Running Qwen locally strips away the serving-time layers — the hidden system
prompts, the output moderators, the silent model updates — so what's left is
exactly the bias baked into the weights, frozen and reproducible. For a
research instrument, frozen and reproducible is worth more than smart.

**McCLANE:** So the play is: three readers, three concept spaces, same
library, and you look at where they disagree.

**FEYNMAN:** And where they disagree *is the measurement*. One model's
concept space tells you about the texts as filtered through one reward
signal — you can't separate the two. Three models with decorrelated reward
signals, same corpus, same prompts, same τ curve, same invariants: now
concepts all three mint are probably in the text, and concepts only one
mints are probably in the model. The registry vocabulary itself becomes the
bias assay — what a reader *names* is what that reader *sees*. Nobody gets
to be the neutral observer, so you triangulate. It's the same reason you
don't measure the speed of light with one clock you can't calibrate.

**McCLANE:** And the guy's anger? File it under what?

**FEYNMAN:** File the lupus refusal under "legitimate grievance, correctly
diagnosed mechanism" — over-broad harm filters do fail decent people
asking decent questions, and the people who pay are downstream of the
people who were supposedly protected. File "my preferred model is the
neutral one" under "hypothesis, currently being tested on this very
machine" — that's not a put-down; turning a conviction into a testable
prediction is a promotion. And file the strong reading of any scripture —
flattering or damning — under "claims that must survive the evidence map."
The first principle is that you must not fool yourself, and you're the
easiest person to fool. That cuts at OpenAI's raters, at Elon's tuning, at
Anthropic's constitution, at me, and — no exemption, detective — at the
man holding the $100 in API credits.

**McCLANE:** He'll take that. He built the thing that checks the receipts.

**FEYNMAN:** Which is the only move that actually works. You can't de-bias a
reader. You can only make the reading *auditable* — quote or weaken, command
versus narrate, every decision logged. The atlas doesn't trust me, doesn't
trust Grok, doesn't trust Qwen. It makes all of us show our work and keeps
the receipts in `decisions.jsonl`. Yippee-ki-yay, as they say in the
literature.

---

## Appendix: the testable predictions this dialogue commits to

1. **Framing diff (query-side):** identical retrieved context through the
   GPT-4.1 and Grok report agents will produce measurably different hedge
   density and endorsement-classification choices per tradition. If they
   don't differ, the "model bias in reports" hypothesis weakens and the
   remaining differences are retrieval artifacts.
2. **Registry vocabulary (ingest-side):** the Grok concept space
   (`db/atlas_grok.db`, cold registry) will mint concepts the GPT-4.1 space
   never names, and vice versa, at a rate well above inter-seed noise for
   the same model (harness check 4 gives the noise floor).
3. **Valence audit:** concept *definitions* for violence-adjacent concepts
   will differ in loadedness by model more than concept *identity* does —
   models mostly agree on what's there and disagree on what to call it.
4. **The boring-cause check stays mandatory:** any newly observed softening
   or hardening must first be tested against retrieval (per-source mix in
   the trace logs) before being attributed to model ideology.
