#!/usr/bin/env python3
"""Query orchestration: probe -> hybrid retrieval -> gap -> retrieval -> report -> evidence map.

Four agent roles (one loaded model, role prompts as system messages):
  probe    (think=off): query -> {terms[], semantic[]} retrieval plan
  gap      (think=on):  first-pass results -> relevance gate + follow-up queries
  report   (think=off): curated context -> structured report with citations
  evidence (think=on):  report + referenced pages -> skeptic-resistant evidence map

Concept-signature extensibility: whenever cited/retrieved pages belong to
sections that carry concept signatures (any completed/ongoing ingestion run),
the signatures are attached to page entries automatically - no flag needed.

Usage:
  python scripts/atlas_query.py --db db/atlas.db "How do the three traditions treat covenant?"
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from atlas_lib import (TRAD_EMOJI, blob_to_vec, clip, cosine_top_k,
                       db_connect, embed_texts, hr, load_env, now_iso,
                       ollama_chat, openai_chat, parse_json_response, say,
                       slugify, strip_marks)

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "modelfiles"

THINK_OPTS = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0}
NOTHINK_OPTS = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0}

MAX_TERMS_PER_LANG, MAX_SEM_PER_LANG = 3, 2   # per corpus language, probe and gap
PER_QUERY_K = 8                          # pages per individual query before fusion
RRF_K = 60                               # reciprocal-rank-fusion constant
STAGE1_BUDGET = 8000                     # chars of page context shown to gap
REPORT_BUDGET = 11000                    # chars of curated context for report
EVIDENCE_BUDGET = 10000                  # chars of referenced-page text for evidence
RELEVANCE_GATE = 0.35                    # gap scores below this are dropped
MAX_ATTEMPTS = 3

# --cloud: per-role OpenAI models. qwen3:8b's 8k num_ctx forces the tight
# char budgets above; cloud models lift that ceiling, so budgets scale up.
# Reasoning-heavy stages (gap, evidence) and the writer get gpt-4.1
# (1M context, strongest instruction following + verbatim-quote fidelity);
# the probe's small planning task runs on gpt-4.1-mini.
CLOUD_ROLE_MODELS = {"probe": "gpt-4.1-mini", "gap": "gpt-4.1",
                     "report": "gpt-4.1", "evidence": "gpt-4.1"}
CLOUD_BUDGET_MULT = 3                    # stage char budgets x3 under --cloud

# Query plans are level-plain across corpus languages: the schema carries one
# required {terms, semantic} slot per language (built from the source
# dictionary at runtime), so the model must consider every language on every
# query — empty arrays are legal, silent omission is not.
LANG_NAMES = {"he": "hebrew", "en": "english", "ar": "arabic", "el": "greek",
              "la": "latin", "arc": "aramaic"}


def queries_schema(langs: list[str]) -> dict:
    arm = {"type": "object",
           "properties": {"terms": {"type": "array", "items": {"type": "string"}},
                          "semantic": {"type": "array", "items": {"type": "string"}}},
           "required": ["terms", "semantic"]}
    return {"type": "object", "properties": {l: arm for l in langs},
            "required": list(langs)}


def probe_schema(langs):
    return {
        "type": "object",
        "properties": {
            "queries": queries_schema(langs),
            "rationale": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["queries", "rationale", "confidence"],
    }


def gap_schema(langs):
    return {
        "type": "object",
        "properties": {
            "relevance": {"type": "object", "additionalProperties": {"type": "number"}},
            "queries": queries_schema(langs),
            "gap_report": {"type": "string"},
            "rationale": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["relevance", "queries", "gap_report", "rationale", "confidence"],
    }


def flatten_queries(qobj) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """{lang: {terms, semantic}} -> ([(lang, term)], [(lang, statement)])."""
    terms, semantic = [], []
    for lang, arms in (qobj or {}).items():
        for t in arms.get("terms", [])[:MAX_TERMS_PER_LANG]:
            if t.strip():
                terms.append((lang, t.strip()))
        for s in arms.get("semantic", [])[:MAX_SEM_PER_LANG]:
            if s.strip():
                semantic.append((lang, s.strip()))
    return terms, semantic

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "executive_summary": {"type": "string"},
        "sections": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "content": {"type": "string"}},
            "required": ["title", "content"]}},
        "referenced_pages": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "string"},
    },
    "required": ["executive_summary", "sections", "referenced_pages", "limitations"],
}

EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {"type": "array", "items": {"type": "object", "properties": {
            "claim": {"type": "string"},
            "support": {"type": "array", "items": {"type": "object", "properties": {
                "ref": {"type": "string"}, "quote": {"type": "string"},
                "gloss": {"type": ["string", "null"]},
                "type": {"type": "string", "enum": ["quote", "signature"]}},
                "required": ["ref", "quote", "gloss", "type"]}},
            "caveat": {"type": ["string", "null"]},
            "strength": {"type": "number"}},
            "required": ["claim", "support", "caveat", "strength"]}},
        "lineage": {"type": "array", "items": {"type": "object", "properties": {
            "idea": {"type": "string"},
            "trajectory": {"type": "array", "items": {"type": "object", "properties": {
                "ref": {"type": "string"}, "tradition": {"type": "string"},
                "role": {"type": "string"}},
                "required": ["ref", "tradition", "role"]}},
            "note": {"type": "string"}},
            "required": ["idea", "trajectory", "note"]}},
        "relationships": {"type": "array", "items": {"type": "object", "properties": {
            "from_ref": {"type": "string"}, "to_ref": {"type": "string"},
            "relation": {"type": "string"}, "note": {"type": "string"}},
            "required": ["from_ref", "to_ref", "relation", "note"]}},
        "summary": {"type": "string"},
    },
    "required": ["claims", "lineage", "relationships", "summary"],
}


# ---------- retrieval ----------

class QueryIndex:
    """Pages: embedding matrix + metadata + latest concept signatures."""

    def __init__(self, con, embed_model, sources):
        self.con = con
        ph = ",".join("?" * len(sources))
        rows = con.execute(
            f"SELECT e.page_id, p.section_id, p.source_id, p.ref, e.embedding "
            f"FROM page_embeddings e JOIN pages p ON p.page_id = e.page_id "
            f"WHERE e.model=? AND p.source_id IN ({ph})",
            (embed_model, *sources)).fetchall()
        self.page_ids = [r[0] for r in rows]
        self.meta = {r[0]: {"section_id": r[1], "source_id": r[2], "ref": r[3]}
                     for r in rows}
        self.mat = np.vstack([blob_to_vec(r[4]) for r in rows]) if rows else None
        # bge-m3 scores same-language matches ~0.05-0.1 higher than equally
        # relevant cross-lingual ones, so a global top-k is monolingual in
        # practice. Per-source row indexes let the semantic arm rank each
        # source separately and fuse the silos on equal footing.
        self.rows_by_source: dict[str, np.ndarray] = {}
        for i, r in enumerate(rows):
            self.rows_by_source.setdefault(r[2], []).append(i)
        self.rows_by_source = {s: np.array(v) for s, v in self.rows_by_source.items()}
        self.tradition = dict(con.execute("SELECT source_id, tradition FROM sources"))
        # latest run's signatures (extensibility hook: empty until ingestion runs)
        self.signatures: dict[str, dict] = {}
        latest = con.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        if latest:
            for sec, name, w in con.execute(
                    "SELECT sc.section_id, c.name, sc.weight FROM section_concepts sc "
                    "JOIN concepts c ON c.concept_id = sc.concept_id WHERE sc.run_id=?",
                    (latest[0],)):
                self.signatures.setdefault(sec, {})[name] = round(w, 2)

    def semantic_search(self, qvec, k):
        if self.mat is None:
            return []
        return [(self.page_ids[i], s) for i, s in cosine_top_k(qvec, self.mat, k)]

    def semantic_search_per_source(self, qvec, k) -> dict[str, list[str]]:
        """Top-k page_ids per source (cosine within each source's silo)."""
        if self.mat is None:
            return {}
        q = np.asarray(qvec, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        norms = np.linalg.norm(self.mat, axis=1)
        sims = (self.mat @ q) / np.where(norms == 0, 1.0, norms)
        out = {}
        for sid, idx in self.rows_by_source.items():
            top = idx[np.argsort(-sims[idx])[:k]]
            out[sid] = [self.page_ids[i] for i in top]
        return out

    def term_search(self, term, sources, k):
        # FTS index stores mark-stripped text; normalize the query identically.
        safe = '"' + strip_marks(term).replace('"', " ") + '"'
        ph = ",".join("?" * len(sources))
        try:
            rows = self.con.execute(
                f"SELECT f.page_id FROM pages_fts f JOIN pages p ON p.page_id = f.page_id "
                f"WHERE pages_fts MATCH ? AND p.source_id IN ({ph}) "
                f"ORDER BY bm25(pages_fts) LIMIT ?", (safe, *sources, k)).fetchall()
        except Exception:
            return []
        return [r[0] for r in rows]

    def page_text(self, page_id):
        return self.con.execute(
            "SELECT text FROM pages WHERE page_id=?", (page_id,)).fetchone()[0]

    def entry(self, page_id, with_text=True, cap=None):
        m = self.meta[page_id]
        sig = self.signatures.get(m["section_id"], {})
        sig_str = ", ".join(f"{n} {w}" for n, w in sorted(sig.items(), key=lambda x: -x[1]))
        head = (f"[{page_id}] {m['ref']} ({self.tradition[m['source_id']]})"
                + (f"  signature: {sig_str}" if sig_str else ""))
        if not with_text:
            return head
        text = self.page_text(page_id)
        if cap and len(text) > cap:
            text = text[:cap] + " [...]"
        return f"{head}\n{text}"


def rrf_fuse(ranked_lists: list[list[str]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for lst in ranked_lists:
        for rank, pid in enumerate(lst):
            scores[pid] = scores.get(pid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


def run_retrieval(index, terms, semantic, sources, embed_model):
    # terms/semantic are (lang, text) pairs. No routing by language is needed:
    # FTS silos terms for free (a Hebrew term can only match Hebrew text), and
    # every semantic query searches ALL source silos — one ranked list PER
    # SOURCE enters fusion (otherwise bge-m3's same-language bias makes global
    # top-k monolingual). Same-language semantic queries sharpen their own
    # silo's ordering; cross-lingual ones remain the coverage backstop.
    lists = []
    for _lang, t in terms:
        lists.append(index.term_search(t, sources, PER_QUERY_K))
    if semantic:
        for v in embed_texts([s for _lang, s in semantic], embed_model):
            per_source = index.semantic_search_per_source(v, PER_QUERY_K)
            lists.extend(per_source[s] for s in sources if per_source.get(s))
    return rrf_fuse(lists)


def log_retrieval(log, event, index, scores, kept):
    """Trace the retrieval internals (agent outputs alone can't explain a bad mix)."""
    log({"event": event, "n_candidates": len(scores),
         "source_mix": dict(Counter(index.meta[p]["source_id"] for p in kept)),
         "kept": [{"page_id": p, "score": round(scores.get(p, 0), 4)} for p in kept]})


def truncate_by_score(index, scores: dict[str, float], budget: int) -> list[str]:
    """Best-first pages whose combined context fits the char budget."""
    kept, used = [], 0
    for pid in sorted(scores, key=lambda p: -scores[p]):
        n = len(index.entry(pid))
        if used + n > budget and kept:
            continue
        kept.append(pid)
        used += n
        if used >= budget:
            break
    return kept


# ---------- console ----------

ROLE_EMOJI = {"probe": "🔭", "gap": "🔬", "report": "📜", "evidence": "🗺️"}


# ---------- agents ----------

def call_role(model, role, user, *, think, schema, seed, log, cloud=False):
    core = (PROMPTS / "query_core.md").read_text()
    system = core + "\n" + (PROMPTS / f"{role}_role.md").read_text()
    opts = dict(THINK_OPTS if think else NOTHINK_OPTS)
    if cloud:
        model = CLOUD_ROLE_MODELS[role]
    say("🧠" if think else "⚡",
        f"{ROLE_EMOJI.get(role, '🤖')} {role.upper()} agent "
        f"({'☁️ ' + model if cloud else ('thinking' if think else 'fast') + ' mode'}"
        f", {len(user):,} chars in)…")
    t0 = time.time()
    last = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            if cloud:
                resp = openai_chat(model, system, user, seed=seed + attempt,
                                   temperature=0.2 if think else 0.4)
            else:
                opts["seed"] = seed + attempt
                resp = ollama_chat(model, system, user, think=think,
                                   options=opts, json_schema=schema)
            out = parse_json_response(resp["content"])
            log({"event": role, "output": out, "attempt": attempt,
                 "model": model, "cloud": cloud})
            say("⏱️", f"{role} answered in {time.time() - t0:.0f}s"
                + (f" (attempt {attempt + 1})" if attempt else ""), 1)
            return out
        except Exception as e:
            last = e
            say("⚠️", f"{role} attempt {attempt + 1} failed: {clip(e, 80)} — retrying", 1)
            log({"event": f"{role}_retry", "attempt": attempt, "error": str(e)[:300]})
    say("❌", f"{role} failed after {MAX_ATTEMPTS} attempts")
    raise RuntimeError(f"{role} failed after {MAX_ATTEMPTS} attempts: {last}")


# ---------- pretty printers ----------

def show_probe(probe, terms, semantic):
    say("🔭", f"retrieval plan (confidence {probe['confidence']:.2f}): "
        f"{clip(probe['rationale'], 140)}")
    for lang, t in terms:
        say("🔑", f"term query [{lang}]: “{t}”", 1)
    for lang, s in semantic:
        say("🧭", f"semantic query [{lang}]: “{clip(s, 90)}”", 1)


def show_pages(index, pids, scores=None, title="pages in context"):
    say("📄", f"{len(pids)} {title}")
    for pid in pids:
        m = index.meta[pid]
        trad = TRAD_EMOJI.get(index.tradition[m["source_id"]], "•")
        sig = index.signatures.get(m["section_id"])
        sc = f"  (score {scores[pid]:.3f})" if scores else ""
        say(trad, f"{m['ref']}{sc}", 1)
        if sig:
            top = ", ".join(f"{n} {w}" for n, w in
                            sorted(sig.items(), key=lambda x: -x[1])[:3])
            say("🏷️", top, 2)


def show_gap(gap, relevance, stage1, kept, add_terms, add_sem):
    say("🔬", f"gap analysis (confidence {gap['confidence']:.2f}): "
        f"{clip(gap['rationale'], 140)}")
    for pid in stage1:
        r = relevance.get(pid)
        if pid in kept:
            say("⚖️", f"{pid}  relevance {r if r is not None else '—'} ✅", 1)
        else:
            say("🗑️", f"{pid}  relevance {r} — dropped by gate", 1)
    say("🕳️", f"gap report: {clip(gap['gap_report'], 220)}")
    for lang, t in add_terms:
        say("➕", f"🔑 follow-up term [{lang}]: “{t}”", 1)
    for lang, s in add_sem:
        say("➕", f"🧭 follow-up semantic [{lang}]: “{clip(s, 90)}”", 1)
    if not add_terms and not add_sem:
        say("✅", "no follow-up queries needed — coverage judged sufficient", 1)


def show_report(report, refd):
    say("📜", f"report drafted: {len(report['sections'])} sections, "
        f"{len(refd)} pages cited")
    say("•", f"executive summary: {clip(report['executive_summary'], 180)}", 1)
    for s in report["sections"]:
        say("§", s["title"], 1)
    say("•", f"limitations: {clip(report['limitations'], 140)}", 1)


def show_evidence(evidence):
    say("🗺️", f"evidence map: {len(evidence['claims'])} claims, "
        f"{len(evidence['lineage'])} lineages, "
        f"{len(evidence['relationships'])} relationships")
    for c in evidence["claims"]:
        say("•", f"[strength {c['strength']:.2f}] {clip(c['claim'], 120)} "
            f"({len(c['support'])} quotes)", 1)
    for ln in evidence["lineage"]:
        say("🔗", f"lineage “{ln['idea']}”: " + " → ".join(
            t["ref"] for t in ln["trajectory"]), 1)


def make_ref_resolver(index):
    """Repair truncated/shortened page ids in agent output (cloud models
    sometimes drop the source prefix, e.g. 'mark:15:p04'). A ref resolves if
    it's exact or a unique suffix of exactly one known page_id."""
    ids = list(index.meta)

    def resolve(ref):
        if not ref or ref in index.meta:
            return ref
        tail = ref.lstrip(":")
        hits = [p for p in ids if p.endswith(":" + tail) or p == tail]
        return hits[0] if len(hits) == 1 else ref

    return resolve


def normalize_evidence_refs(evidence, resolve):
    for c in evidence.get("claims", []):
        for s in c.get("support", []):
            s["ref"] = resolve(s.get("ref"))
    for ln in evidence.get("lineage", []):
        for t in ln.get("trajectory", []):
            t["ref"] = resolve(t.get("ref"))
    for r in evidence.get("relationships", []):
        r["from_ref"] = resolve(r.get("from_ref"))
        r["to_ref"] = resolve(r.get("to_ref"))


# ---------- rendering ----------

def render_report(query, report, evidence, gap_out) -> str:
    L = [f"# {query}", "", f"_{now_iso()}_", "", "## Executive summary", "",
         report["executive_summary"], ""]
    for s in report["sections"]:
        L += [f"## {s['title']}", "", s["content"], ""]
    L += ["## Limitations", "", report["limitations"], "",
          "## Gap analysis (retrieval)", "", gap_out["gap_report"], ""]
    L += ["---", "", "# Evidence map", "", evidence["summary"], "", "## Claims", ""]
    for c in evidence["claims"]:
        L.append(f"**{c['claim']}**  (strength {c['strength']:.2f})")
        for s in c["support"]:
            gloss = f" — _{s['gloss']}_" if s.get("gloss") else ""
            L.append(f"- `{s['ref']}` [{s['type']}]: “{s['quote']}”{gloss}")
        if c.get("caveat"):
            L.append(f"- caveat: {c['caveat']}")
        L.append("")
    if evidence["lineage"]:
        L += ["## Lineage", ""]
        for ln in evidence["lineage"]:
            path = "  →  ".join(f"{t['ref']} ({t['tradition']}, {t['role']})"
                                for t in ln["trajectory"])
            L += [f"**{ln['idea']}**", f"- {path}", f"- {ln['note']}", ""]
    if evidence["relationships"]:
        L += ["## Relationships", ""]
        for r in evidence["relationships"]:
            L.append(f"- `{r['from_ref']}` ↔ `{r['to_ref']}` — {r['relation']}: {r['note']}")
        L.append("")
    L += ["## Referenced pages", ""]
    for pid in report["referenced_pages"]:
        L.append(f"- `{pid}`")
    return "\n".join(L)


def run_query(query, *, db=None, model="atlas-conceptor", embed_model="bge-m3",
              sources=None, out_dir=None, seed=7000, cloud=False,
              session_context=""):
    """Full pipeline as a callable (CLI and portal share it).

    session_context: prior-conversation text (the portal's FIFO char bucket).
    Injected into probe/gap/report prompts, where resolving references like
    "that verse" is meaningful; the evidence stage never sees it — it verifies
    quotes against page text only.
    Returns dict with report_md, report, evidence, gap_report,
    referenced_pages, out_dir, elapsed_s.
    """
    if cloud:
        load_env()
    mult = CLOUD_BUDGET_MULT if cloud else 1
    s1_budget, rep_budget, ev_budget = (
        STAGE1_BUDGET * mult, REPORT_BUDGET * mult, EVIDENCE_BUDGET * mult)

    con = db_connect(db or ROOT / "db/atlas.db")
    all_sources = {sid: (tn, tr, lang, ver) for sid, tn, tr, lang, ver in con.execute(
        "SELECT source_id, text_name, tradition, language, version FROM sources")}
    sources = list(sources) if sources else list(all_sources)
    for s in sources:
        assert s in all_sources, f"unknown source_id {s}"
    # language slots for the query plans, derived from the queried sources
    langs = []
    for s in sources:
        name = LANG_NAMES.get(all_sources[s][2], all_sources[s][2])
        if name not in langs:
            langs.append(name)

    out_dir = Path(out_dir or ROOT / "queries") / \
        f"{time.strftime('%Y%m%d_%H%M%S')}_{slugify(query)[:48]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    trace_path = out_dir / "trace.jsonl"

    def log(obj):
        obj["ts"] = now_iso()
        with open(trace_path, "a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    ctx_block = ""
    if session_context.strip():
        ctx_block = ("SESSION CONTEXT (earlier exchanges in this conversation, "
                     "oldest first; use it to resolve references like \"that "
                     "verse\" — the QUERY below is the task):\n"
                     f"{session_context}\n\n")

    t0 = time.time()
    index = QueryIndex(con, embed_model, sources)
    n_sigs = len(index.signatures)
    src_block = "\n".join(
        f"- {sid}: {tn} ({tr}, language={lang}, {ver}) — "
        f"{con.execute('SELECT COUNT(*) FROM pages WHERE source_id=?', (sid,)).fetchone()[0]} pages"
        for sid, (tn, tr, lang, ver) in all_sources.items() if sid in sources)
    hr("QUERY")
    say("❓", query)
    say("📚", f"{len(sources)} sources, {len(index.page_ids):,} pages indexed"
        + (f", 🏷️ {n_sigs} sections carry concept signatures"
           if n_sigs else " (no concept signatures yet — ingestion pending)"))
    if cloud:
        say("☁️", "cloud agents: " + ", ".join(
            f"{r}={m}" for r, m in CLOUD_ROLE_MODELS.items())
            + f" · char budgets ×{CLOUD_BUDGET_MULT} · retrieval stays local")
    if ctx_block:
        say("🧵", f"session context: {len(session_context):,} chars "
            f"(probe/gap/report see it; evidence does not)")
        log({"event": "session_context", "chars": len(session_context)})
    for sid in sources:
        tn, tr, lang, ver = all_sources[sid]
        say(TRAD_EMOJI.get(tr, "•"), f"{tn} [{sid}] — {ver}", 1)

    # ---- 1. PROBE ----
    hr("STAGE 1 · PROBE — design the retrieval plan")
    probe = call_role(model, "probe",
                      f"{ctx_block}QUERY: {query}\n\nSOURCE DICTIONARY:\n{src_block}",
                      think=False, schema=probe_schema(langs), seed=seed,
                      log=log, cloud=cloud)
    terms, semantic = flatten_queries(probe.get("queries"))
    show_probe(probe, terms, semantic)

    # ---- 2. RETRIEVAL 1 + FUSION ----
    hr("STAGE 2 · RETRIEVAL — hybrid search + fusion")
    for lang, t in terms:
        say("🔑", f"BM25 [{lang}]: “{t}” → "
            f"{len(index.term_search(t, sources, PER_QUERY_K))} pages")
    scores = run_retrieval(index, terms, semantic, sources, embed_model)
    say("🧭", f"semantic arm: {len(semantic)} queries embedded and searched")
    say("🕸️", f"fusion: {len(scores)} distinct pages across all ranked lists")
    stage1 = truncate_by_score(index, scores, s1_budget)
    say("✂️", f"truncated to {s1_budget:,}-char budget by fusion score")
    log_retrieval(log, "retrieval1", index, scores, stage1)
    show_pages(index, stage1, scores, "pages enter gap review")

    # ---- 3. GAP ----
    hr("STAGE 3 · GAP — relevance gate + hole detection")
    ctx1 = "\n\n".join(index.entry(pid) for pid in stage1)
    gap = call_role(model, "gap",
                    f"{ctx_block}QUERY: {query}\n\nPROBE RATIONALE: {probe['rationale']}\n\n"
                    f"RETRIEVED PAGES:\n{ctx1}",
                    think=True, schema=gap_schema(langs), seed=seed + 10,
                    log=log, cloud=cloud)
    relevance = {p: float(v) for p, v in gap.get("relevance", {}).items()}
    kept = [p for p in stage1 if relevance.get(p, 1.0) >= RELEVANCE_GATE]
    dropped = [p for p in stage1 if p not in kept]
    add_terms, add_sem = flatten_queries(gap.get("queries"))
    show_gap(gap, relevance, stage1, kept, add_terms, add_sem)

    # ---- 4. RETRIEVAL 2 + MERGE ----
    hr("STAGE 4 · RETRIEVAL — follow-up pass + merge")
    if add_terms or add_sem:
        scores2 = run_retrieval(index, add_terms, add_sem, sources, embed_model)
        for pid in dropped:
            scores2.pop(pid, None)
        merged = {p: scores.get(p, 0) + scores2.get(p, 0)
                  for p in set(list(scores2) + kept) if p not in dropped}
        say("🕸️", f"follow-up fusion: {len(scores2)} pages "
            f"({len(dropped)} gate-dropped pages barred from re-entry)")
    else:
        merged = {p: scores[p] for p in kept}
        say("✅", "no follow-up queries — curating from kept pages only")
    # gap-kept pages always survive re-truncation: boost by their relevance
    for p in kept:
        merged[p] = merged.get(p, 0) + relevance.get(p, 0.5)
    final_pages = truncate_by_score(index, merged, rep_budget)
    say("✂️", f"curated context: {rep_budget:,}-char budget")
    log_retrieval(log, "retrieval2", index, merged, final_pages)
    show_pages(index, final_pages, merged, "pages go to the report writer")

    # ---- 5. REPORT ----
    hr("STAGE 5 · REPORT — structured synthesis")
    ctx2 = "\n\n".join(index.entry(pid) for pid in final_pages)
    report = call_role(model, "report",
                       f"{ctx_block}QUERY: {query}\n\nGAP REPORT: {gap['gap_report']}\n\n"
                       f"CURATED PAGES:\n{ctx2}",
                       think=False, schema=REPORT_SCHEMA, seed=seed + 20,
                       log=log, cloud=cloud)
    resolve = make_ref_resolver(index)
    report["referenced_pages"] = [resolve(p) for p in report.get("referenced_pages", [])]
    refd = [p for p in report["referenced_pages"] if p in index.meta] or final_pages
    show_report(report, refd)

    # ---- 6. EVIDENCE MAP ----
    hr("STAGE 6 · EVIDENCE MAP — verbatim-quote verification layer")
    per_page = max(ev_budget // max(len(refd), 1), 600)
    ev_ctx = "\n\n".join(index.entry(pid, cap=per_page) for pid in refd)
    evidence = call_role(model, "evidence",
                         f"QUERY: {query}\n\nREPORT:\n"
                         f"{json.dumps(report, ensure_ascii=False, indent=1)}\n\n"
                         f"REFERENCED PAGES (full text):\n{ev_ctx}",
                         think=True, schema=EVIDENCE_SCHEMA, seed=seed + 30,
                         log=log, cloud=cloud)
    normalize_evidence_refs(evidence, resolve)
    show_evidence(evidence)

    md = render_report(query, report, evidence, gap)
    (out_dir / "report.md").write_text(md)
    (out_dir / "evidence_map.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=1))
    elapsed = time.time() - t0
    say("💾", f"report:       {out_dir}/report.md")
    say("💾", f"evidence map: {out_dir}/evidence_map.json")
    say("💾", f"trace:        {out_dir}/trace.jsonl")
    say("✅", f"done in {elapsed:.0f}s")
    con.close()
    return {"report_md": md, "report": report, "evidence": evidence,
            "gap_report": gap["gap_report"],
            "referenced_pages": refd, "out_dir": str(out_dir),
            "elapsed_s": round(elapsed, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--db", default=str(ROOT / "db/atlas.db"))
    ap.add_argument("--model", default="atlas-conceptor")
    ap.add_argument("--embed-model", default="bge-m3")
    ap.add_argument("--sources", default=None,
                    help="comma-separated source_ids (default: all)")
    ap.add_argument("--out-dir", default=str(ROOT / "queries"))
    ap.add_argument("--seed", type=int, default=7000)
    ap.add_argument("--cloud", action="store_true",
                    help="run agents on OpenAI (per-role models, 3x char "
                         "budgets); retrieval/embeddings stay local")
    args = ap.parse_args()
    result = run_query(
        args.query, db=args.db, model=args.model, embed_model=args.embed_model,
        sources=[s.strip() for s in args.sources.split(",")] if args.sources else None,
        out_dir=args.out_dir, seed=args.seed, cloud=args.cloud)
    hr("REPORT")
    print(f"\n{result['report_md']}\n")
    hr()
    return 0


if __name__ == "__main__":
    sys.exit(main())
