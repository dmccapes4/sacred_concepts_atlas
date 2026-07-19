#!/usr/bin/env python3
"""Concept-extraction orchestrator: local agents build the concept space.

Per section (interleaved across the three traditions):
  1. ROUTER  (think=off): full concept-name list + section text
             -> candidates, new-concept candidates, draft distribution.
  2. RETRIEVE: candidate set = router picks  ∪  embedding retrieval
             (section pages -> concept definitions)  ∪  nearest concepts to
             each router new-candidate (near-duplicate check).
  3. CLASSIFIER (think=on): section + rich candidate entries + router draft
             -> final weighted signature, new concepts gated by tau(n).
  4. COMMIT: concepts / section_concepts under run_id; export
             artifacts/concept_dictionary.json + concept_hash.json;
             append runs/<run_id>/decisions.jsonl.

Resume: sections that already have rows for the run are skipped.
"""

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from atlas_lib import (TRAD_EMOJI, blob_to_vec, clip, cloud_chat, cosine_top_k,
                       db_connect, embed_texts, hr, load_env, now_iso,
                       ollama_chat, parse_json_response, say,
                       slugify, tau, vec_to_blob)

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "modelfiles"
ARTIFACTS = ROOT / "artifacts"

# Qwen3 sampling: thinking vs non-thinking modes (model card recommendations)
THINK_OPTS = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0}
NOTHINK_OPTS = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0}

MAX_SECTION_CHARS = 9000    # defensive cap for prompt assembly (~2.5k tokens)
CANDIDATE_CAP = 12          # rich entries shown to classifier
RETRIEVE_K = 6              # embedding-retrieved concepts per section
NEIGHBOR_K = 3              # near-duplicate check per router new-candidate
EVIDENCE_K = 1              # corpus pages retrieved per anchor verse PER SOURCE
EVIDENCE_MIN_SIM = 0.55     # drop evidence hits below this cosine: a weak
                            # cross-tradition neighbor is noise that destabilizes
                            # novelty decisions (eval_20260716_232206 post-mortem)

# --cloud [provider]: cloud agents; embeddings stay local (bge-m3 via Ollama).
# The classifier is the interpretive organ, so it gets the provider's
# flagship: grok-4.5 (operator call 2026-07-17: quality and tokens over wall
# clock — ~31s/section is acceptable overnight). cloud_chat pins 4.5 to
# reasoning_effort=low, the token-efficient tier; measured same signature
# quality as high on the Genesis-1 A/B at ~30% fewer reasoning tokens.
# Full-pass estimate ~$85 of the $100 credit, ~22h wall clock.
CLOUD_ROLE_MODELS = {
    "openai": {"router": "gpt-4.1-mini", "classifier": "gpt-4.1"},
    "grok": {"router": "grok-4.20-0309-non-reasoning", "classifier": "grok-4.5"},
}
MAX_ATTEMPTS = 3            # per agent call (seed bumps between attempts)
WEIGHT_TOL = 0.01

# NOTE: with grammar-constrained decoding, non-required fields are simply never
# emitted. Every field is therefore required; "not applicable" is null.
ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "confidence": {"type": "number"}},
            "required": ["name", "confidence"]}},
        "new_candidates": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "definition": {"type": "string"},
            "confidence": {"type": "number"}},
            "required": ["name", "definition", "confidence"]}},
        "draft_distribution": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "weight": {"type": "number"},
            "verse": {"type": "string"}},
            "required": ["name", "weight", "verse"]}},
        "rationale": {"type": "string"},
    },
    "required": ["candidates", "new_candidates", "draft_distribution", "rationale"],
}

CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "assignments": {"type": "array", "items": {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["existing", "new"]},
            "concept_id": {"type": ["string", "null"]},
            "name": {"type": ["string", "null"]},
            "definition": {"type": ["string", "null"]},
            "novelty_confidence": {"type": ["number", "null"]},
            "weight": {"type": "number"},
            "rationale": {"type": "string"}},
            "required": ["kind", "concept_id", "name", "definition",
                         "novelty_confidence", "weight", "rationale"]}},
        "rationale": {"type": "string"},
    },
    "required": ["assignments", "rationale"],
}


class Registry:
    """In-memory mirror of the concepts table, kept in sync on insert."""

    def __init__(self, con):
        self.con = con
        self.by_id: dict[str, dict] = {}
        self.mat = None          # (n, dim) embedding matrix, row i = ids[i]
        self.ids: list[str] = []
        for cid, name, definition, aliases, emb in con.execute(
                "SELECT concept_id, name, definition, aliases, embedding "
                "FROM concepts WHERE status='active'"):
            self.by_id[cid] = {"name": name, "definition": definition,
                               "aliases": json.loads(aliases or "[]")}
            if emb:
                self._append_vec(cid, blob_to_vec(emb))

    def _append_vec(self, cid, vec):
        self.ids.append(cid)
        row = vec.reshape(1, -1)
        self.mat = row if self.mat is None else np.vstack([self.mat, row])

    def __len__(self):
        return len(self.by_id)

    def name_to_id(self) -> dict[str, str]:
        d = {}
        for cid, c in self.by_id.items():
            d[c["name"].lower()] = cid
            for a in c["aliases"]:
                d[a.lower()] = cid
        return d

    def nearest(self, vec, k) -> list[tuple[str, float]]:
        if self.mat is None:
            return []
        return [(self.ids[i], s) for i, s in cosine_top_k(vec, self.mat, k)]

    def add(self, cid, name, definition, vec, section_id, run_id, embed_model):
        self.con.execute(
            "INSERT INTO concepts (concept_id, name, definition, embedding, embed_model, "
            "created_by, created_run) VALUES (?,?,?,?,?,?,?)",
            (cid, name, definition, vec_to_blob(vec), embed_model, section_id, run_id))
        self.by_id[cid] = {"name": name, "definition": definition, "aliases": []}
        self._append_vec(cid, vec)


class PageIndex:
    """In-memory embedding index over all pages (~8k x 1024 floats, ~30 MB)."""

    def __init__(self, con, model):
        rows = con.execute(
            "SELECT e.page_id, p.section_id, p.ref, e.embedding "
            "FROM page_embeddings e JOIN pages p ON p.page_id = e.page_id "
            "WHERE e.model=?", (model,)).fetchall()
        self.sections = [r[1] for r in rows]
        self.refs = [r[2] for r in rows]
        self.mat = np.vstack([blob_to_vec(r[3]) for r in rows]) if rows else None
        self.by_source: dict[str, np.ndarray] = {}
        for i, r in enumerate(rows):
            self.by_source.setdefault(r[1].split(":", 1)[0], []).append(i)
        self.by_source = {s: np.array(v) for s, v in self.by_source.items()}

    def nearest(self, vec, k, exclude_section):
        """Top-k pages PER SOURCE (deduped by section, excluding the section
        under analysis), best-first overall. Anchor verses are in the
        section's own language and bge-m3 scores same-language matches
        higher, so a global top-k would return same-corpus neighbors only —
        per-source ranking guarantees the classifier sees cross-tradition
        evidence (e.g. a Genesis 7 anchor also surfaces Surah 71's flood)."""
        if self.mat is None:
            return []
        q = np.asarray(vec, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        norms = np.linalg.norm(self.mat, axis=1)
        sims = (self.mat @ q) / np.where(norms == 0, 1.0, norms)
        hits = []
        for src, idx in self.by_source.items():
            seen, taken = set(), 0
            for i in idx[np.argsort(-sims[idx])]:
                if sims[i] < EVIDENCE_MIN_SIM:
                    break
                sec = self.sections[i]
                if sec == exclude_section or sec in seen:
                    continue
                seen.add(sec)
                hits.append((int(i), float(sims[i])))
                taken += 1
                if taken == k:
                    break
        return sorted(hits, key=lambda x: -x[1])


def run_signature(con, section_id, run_id) -> dict[str, float]:
    """{concept_name: weight} for a section under this run (empty if unprocessed)."""
    return {name: w for name, w in con.execute(
        "SELECT c.name, sc.weight FROM section_concepts sc "
        "JOIN concepts c ON c.concept_id = sc.concept_id "
        "WHERE sc.section_id=? AND sc.run_id=?", (section_id, run_id))}


def gather_evidence(con, page_index, draft, section_id, run_id, embed_model, log):
    """Verse-anchored corpus evidence, bucketed per draft concept.

    Each draft entry's anchor verse is a semantic query over all pages. Hits
    carry the retrieved section's concept signature (this run) and its overlap
    with the router's draft signature: sum over shared concepts of min weight.
    """
    draft = [d for d in draft if d.get("name")][:6]
    if not draft:
        return {}
    queries = [(d.get("verse") or "").strip() or d["name"] for d in draft]
    vecs = embed_texts(queries, embed_model)
    draft_sig = {d["name"].lower(): float(d.get("weight", 0)) for d in draft}
    section_text = con.execute(
        "SELECT text FROM sections WHERE section_id=?", (section_id,)).fetchone()[0]
    evidence = {}
    for d, q, v in zip(draft, queries, vecs):
        if d.get("verse") and d["verse"].strip() not in section_text:
            log({"event": "verse_not_verbatim", "section": section_id,
                 "concept": d["name"], "verse": d["verse"][:200]})
        hits = []
        for i, sim in page_index.nearest(v, EVIDENCE_K, exclude_section=section_id):
            sec = page_index.sections[i]
            sig = run_signature(con, sec, run_id)
            overlap = (round(sum(min(w, sig.get(n, 0.0))
                                 for n, w in draft_sig.items()), 3)
                       if sig else None)  # None = section not yet processed
            hits.append({"ref": page_index.refs[i], "section_id": sec,
                         "similarity": round(sim, 3), "signature": sig,
                         "draft_overlap": overlap})
        evidence[d["name"]] = {"verse": q, "hits": hits}
    return evidence


def format_evidence(evidence) -> str:
    if not evidence:
        return "(no evidence retrieved)"
    lines = []
    for name, e in evidence.items():
        lines.append(f'- draft concept: {name}\n  anchor verse: "{e["verse"][:200]}"')
        for h in e["hits"]:
            sig = ", ".join(f"{n} {w:.2f}" for n, w in
                            sorted(h["signature"].items(), key=lambda x: -x[1])) \
                  or "(not yet processed)"
            ov = f", draft-overlap {h['draft_overlap']}" if h["draft_overlap"] is not None else ""
            lines.append(f"    * {h['ref']} (sim {h['similarity']}{ov}) signature: {sig}")
    return "\n".join(lines)


CANON_ORDER = ["tanakh_he_uxlc", "bible_en_web", "quran_ar_tanzil"]  # composition order


def ordered_sections(con, run_id, order="interleaved"):
    """Processing order for remaining sections. Order matters most early,
    while the registry is cold and most vocabulary gets minted.

    interleaved: round-robin across sources so no tradition's vocabulary
      imprints first; the cycle follows canonical composition order
      (Tanakh, Bible, Quran) rather than an alphabetical accident.
    temporal: all of the Tanakh, then the Bible, then the Quran — the
      lineage-prior ordering; later texts are read against the vocabulary
      the earlier ones minted (and face a higher tau doing it).
    """
    done = {r[0] for r in con.execute(
        "SELECT DISTINCT section_id FROM section_concepts WHERE run_id=?", (run_id,))}
    per_source = {}
    for sid, src in con.execute(
            "SELECT section_id, source_id FROM sections ORDER BY source_id, seq"):
        per_source.setdefault(src, []).append(sid)
    lanes = [per_source[s] for s in CANON_ORDER if s in per_source]
    lanes += [per_source[s] for s in sorted(per_source) if s not in CANON_ORDER]
    if order == "temporal":
        ordered = [sid for lane in lanes for sid in lane]
    else:
        ordered = []
        for j in range(max((len(l) for l in lanes), default=0)):
            for lane in lanes:
                if j < len(lane):
                    ordered.append(lane[j])
    return [s for s in ordered if s not in done]


def section_context(con, section_id):
    src, book, ref, text = con.execute(
        "SELECT source_id, book, ref, text FROM sections WHERE section_id=?",
        (section_id,)).fetchone()
    tradition, language = con.execute(
        "SELECT tradition, language FROM sources WHERE source_id=?", (src,)).fetchone()
    if len(text) > MAX_SECTION_CHARS:
        text = text[:MAX_SECTION_CHARS] + "\n[...section truncated for length...]"
    return {"source_id": src, "book": book, "ref": ref, "text": text,
            "tradition": tradition, "language": language}


def section_vecs(con, section_id, embed_model):
    rows = con.execute(
        "SELECT e.embedding FROM page_embeddings e JOIN pages p ON p.page_id = e.page_id "
        "WHERE p.section_id=? AND e.model=?", (section_id, embed_model)).fetchall()
    return [blob_to_vec(r[0]) for r in rows]


def usage_stats(con, cid, run_id):
    n, w = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(weight),0) FROM section_concepts "
        "WHERE concept_id=? AND run_id=?", (cid, run_id)).fetchone()
    ex = [r[0] for r in con.execute(
        "SELECT section_id FROM section_concepts WHERE concept_id=? AND run_id=? "
        "ORDER BY weight DESC LIMIT 3", (cid, run_id))]
    return n, round(w, 2), ex


AGENT_EMOJI = {"router": "🧩", "classifier": "🎓"}


def call_agent(model, system, user, *, think, schema, seed_base, log, label="",
               provider=None):
    opts = dict(THINK_OPTS if think else NOTHINK_OPTS)
    if provider:
        roles = CLOUD_ROLE_MODELS[provider]
        model = roles.get(label, roles["classifier"])
    if label:
        mode = (f"☁️ {model}" if provider
                else f"{'thinking' if think else 'fast'} mode")
        say("🧠" if think else "⚡",
            f"{AGENT_EMOJI.get(label, '🤖')} {label.upper()} "
            f"({mode}, {len(user):,} chars in)…", 1)
    t0 = time.time()
    last_err = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            if provider:
                resp = cloud_chat(
                    provider, model, system, user, seed=seed_base + attempt,
                    temperature=0.2 if think else 0.4, timeout=600)
            else:
                opts["seed"] = seed_base + attempt
                resp = ollama_chat(model, system, user, think=think,
                                   options=opts, json_schema=schema)
            out = parse_json_response(resp["content"])
            if label:
                say("⏱️", f"{label} answered in {time.time() - t0:.0f}s"
                    + (f" (attempt {attempt + 1})" if attempt else ""), 2)
            return out, attempt
        except Exception as e:  # network, JSON, schema
            last_err = e
            if label:
                say("⚠️", f"{label} attempt {attempt + 1} failed: {clip(e, 70)} — retrying", 2)
            log({"event": "agent_retry", "attempt": attempt, "error": str(e)[:500],
                 "provider": provider or "local", "model": model})
    say("❌", f"{label or 'agent'} failed after {MAX_ATTEMPTS} attempts", 1)
    raise RuntimeError(f"agent failed after {MAX_ATTEMPTS} attempts: {last_err}")


def export_artifacts(con, run_id, db_path):
    """Concept dictionary + hash views. Forked DBs (model-bias runs, e.g.
    atlas_grok.db) export under artifacts/<db_stem>/ so parallel runs on
    different DBs don't clobber the main atlas's artifacts."""
    stem = Path(db_path).stem
    out_dir = ARTIFACTS if stem == "atlas" else ARTIFACTS / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    dictionary = {n: d for n, d in con.execute(
        "SELECT name, definition FROM concepts WHERE status='active' ORDER BY created_at")}
    chash = {}
    for name, section_id, weight in con.execute(
            "SELECT c.name, sc.section_id, sc.weight FROM section_concepts sc "
            "JOIN concepts c ON c.concept_id = sc.concept_id "
            "WHERE sc.run_id=? ORDER BY c.name, sc.weight DESC", (run_id,)):
        chash.setdefault(name, {})[section_id] = round(weight, 4)
    for path, obj in [(out_dir / "concept_dictionary.json", dictionary),
                      (out_dir / "concept_hash.json", chash)]:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
        tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--model", default="atlas-conceptor")
    ap.add_argument("--router-model", default=None,
                    help="separate model for the router role (default: same as "
                         "--model); use on dual-model hosts, e.g. "
                         "--router-model atlas-router --model atlas-classifier")
    ap.add_argument("--embed-model", default="bge-m3")
    ap.add_argument("--tau0", type=float, default=0.55)
    ap.add_argument("--tau-max", type=float, default=0.92)
    ap.add_argument("--tau-k", type=float, default=150)
    ap.add_argument("--order", default="interleaved",
                    choices=["interleaved", "temporal"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sections", default=None,
                    help="comma-separated section_ids to process in the given "
                         "order (overrides --order/--limit; used by the eval harness)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--cloud", nargs="?", const="openai", default=None,
                    choices=list(CLOUD_ROLE_MODELS),
                    help="run router/classifier on a cloud provider: "
                         "--cloud [openai|grok] (bare --cloud = openai; "
                         "embeddings stay local via Ollama bge-m3); needs "
                         "OPENAI_API_KEY / SPACEXAI_API_KEY in .env")
    args = ap.parse_args()
    provider = args.cloud
    if provider:
        load_env()
    cloud_models = CLOUD_ROLE_MODELS[provider] if provider else None
    router_model = args.router_model or args.model

    con = db_connect(args.db)

    core = (PROMPTS / "doctrine_core.md").read_text()
    router_sys = core + "\n" + (PROMPTS / "router_role.md").read_text()
    classifier_sys = core + "\n" + (PROMPTS / "classifier_role.md").read_text()
    prompts_sha = hashlib.sha256((router_sys + classifier_sys).encode()).hexdigest()[:16]

    if args.resume and not args.run_id:
        row = con.execute("SELECT run_id FROM runs WHERE finished_at IS NULL "
                          "ORDER BY started_at DESC LIMIT 1").fetchone()
        if not row:
            print("nothing to resume"); return 1
        run_id = row[0]
    else:
        run_id = args.run_id or f"run_{time.strftime('%Y%m%d_%H%M%S')}"
        con.execute(
            "INSERT OR IGNORE INTO runs (run_id, model, embed_model, prompts_sha, params) "
            "VALUES (?,?,?,?,?)",
            (run_id,
             (f"cloud[{provider}]:" + ",".join(f"{r}={m}" for r, m in cloud_models.items())
              if provider else
              (f"router={router_model},classifier={args.model}"
               if router_model != args.model else args.model)),
             args.embed_model, prompts_sha, json.dumps({
                "tau0": args.tau0, "tau_max": args.tau_max, "tau_k": args.tau_k,
                "order": args.order, "seed": args.seed, "cloud": provider,
                "router_model": router_model,
                "cloud_models": cloud_models})))
        con.commit()

    if provider and args.resume:
        # Resuming a local run with cloud agents — stamp the switch in params.
        row = con.execute("SELECT params FROM runs WHERE run_id=?", (run_id,)).fetchone()
        params = json.loads(row[0] or "{}") if row else {}
        params["cloud"] = provider
        params["cloud_models"] = cloud_models
        params["switched_to_cloud_at"] = now_iso()
        con.execute("UPDATE runs SET params=?, model=? WHERE run_id=?",
                    (json.dumps(params),
                     f"cloud[{provider}]:" + ",".join(f"{r}={m}" for r, m in cloud_models.items()),
                     run_id))
        con.commit()

    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    decisions_path = run_dir / "decisions.jsonl"

    def log(obj):
        obj["ts"] = now_iso()
        with open(decisions_path, "a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    registry = Registry(con)
    page_index = PageIndex(con, args.embed_model)
    if args.sections:
        wanted = [s.strip() for s in args.sections.split(",") if s.strip()]
        for s in wanted:
            assert con.execute("SELECT 1 FROM sections WHERE section_id=?",
                               (s,)).fetchone(), f"unknown section_id {s}"
        done = {r[0] for r in con.execute(
            "SELECT DISTINCT section_id FROM section_concepts WHERE run_id=?",
            (run_id,))}
        todo = [s for s in wanted if s not in done]
    else:
        todo = ordered_sections(con, run_id, args.order)
        if args.limit:
            todo = todo[:args.limit]
    total_done = con.execute(
        "SELECT COUNT(DISTINCT section_id) FROM section_concepts WHERE run_id=?",
        (run_id,)).fetchone()[0]
    hr(f"INGESTION RUN {run_id}")
    if provider:
        say("☁️", f"cloud agents [{provider}]: " + ", ".join(
            f"{r}={m}" for r, m in cloud_models.items())
            + " · embeddings stay local (bge-m3)")
    model_desc = (args.model if router_model == args.model
                  else f"router={router_model} classifier={args.model}")
    say("🚀", f"model={model_desc} embed={args.embed_model} "
        f"τ0={args.tau0} τmax={args.tau_max} k={args.tau_k} order={args.order}")
    say("📚", f"{len(todo)} sections to process ({total_done} already done) · "
        f"🧮 registry={len(registry)}")

    def fmt_dur(s: float) -> str:
        s = int(s)
        if s >= 3600:
            return f"{s // 3600}h{(s % 3600) // 60:02d}m"
        if s >= 60:
            return f"{s // 60}m{s % 60:02d}s"
        return f"{s}s"

    # ETA uses a rolling MEDIAN of recent section durations, not the mean of
    # the whole run: one section that sat in network backoff for 10+ minutes
    # would otherwise inflate the projection for every section after it,
    # while rare enough to say nothing about future throughput. The window
    # also tracks genuine drift (prompts grow with the registry). Sections
    # slower than 4x the median are counted and shown but never averaged in.
    ETA_WINDOW = 60
    durations: list[float] = []
    n_outliers = 0
    # An agent call that exhausts its retries (transport backoff included)
    # pauses the pass cleanly: nothing about the section is committed, and
    # resume picks it up first. A traceback here used to kill overnight runs.
    abort_err = None
    for idx, section_id in enumerate(todo, start=1):
        t0 = time.time()
        ctx = section_context(con, section_id)
        seed_base = args.seed + idx * 10
        say(TRAD_EMOJI.get(ctx["tradition"], "•"),
            f"📖 [{idx}/{len(todo)}] {ctx['ref']} — {section_id}")

        try:
            # ---- 1. ROUTER ----
            names = sorted(c["name"] for c in registry.by_id.values())
            names_block = "\n".join(f"- {n}" for n in names) if names else "(none yet - registry is empty)"
            router_user = (
                f"SECTION: {ctx['ref']}  [{ctx['tradition']}, language={ctx['language']}]\n\n"
                f"KNOWN CONCEPTS ({len(names)}):\n{names_block}\n\n"
                f"SECTION TEXT:\n{ctx['text']}")
            router_out, r_attempts = call_agent(
                router_model, router_sys, router_user,
                think=False, schema=ROUTER_SCHEMA, seed_base=seed_base, log=log,
                label="router", provider=provider)
            say("🧩", f"screened {len(names)} known names → "
                f"{len(router_out.get('candidates', []))} candidates, "
                f"{len(router_out.get('new_candidates', []))} new proposals", 1)
            for c in router_out.get("new_candidates", [])[:4]:
                say("💡", f"“{c.get('name', '?')}” (novelty conf {float(c.get('confidence', 0)):.2f})", 2)

            # ---- 2. CANDIDATE SET ----
            name_map = registry.name_to_id()
            cand_ids: dict[str, float] = {}
            for c in router_out.get("candidates", []):
                cid = name_map.get(c.get("name", "").lower())
                if cid:
                    cand_ids[cid] = max(cand_ids.get(cid, 0), float(c.get("confidence", 0)))
            svecs = section_vecs(con, section_id, args.embed_model)
            if svecs and len(registry):
                agg = {}
                for v in svecs:
                    for cid, sim in registry.nearest(v, RETRIEVE_K):
                        agg[cid] = max(agg.get(cid, 0), sim)
                for cid, sim in sorted(agg.items(), key=lambda x: -x[1])[:RETRIEVE_K]:
                    cand_ids.setdefault(cid, sim)
            new_cands = router_out.get("new_candidates", [])[:4]
            nc_vecs = embed_texts(
                [f"{c['name']}: {c['definition']}" for c in new_cands],
                args.embed_model) if new_cands else []
            for c, v in zip(new_cands, nc_vecs):
                c["_vec"] = v
                for cid, sim in registry.nearest(v, NEIGHBOR_K):
                    cand_ids.setdefault(cid, sim)
            cand_list = sorted(cand_ids.items(), key=lambda x: -x[1])[:CANDIDATE_CAP]
            say("🕸️", f"candidate set: {len(cand_list)} concepts "
                f"(router picks ∪ page-embedding retrieval ∪ near-duplicate check)", 1)

            # ---- 2b. VERSE-ANCHORED EVIDENCE ----
            evidence = gather_evidence(con, page_index, router_out.get("draft_distribution", []),
                                       section_id, run_id, args.embed_model, log)
            if evidence:
                n_hits = sum(len(e["hits"]) for e in evidence.values())
                n_labeled = sum(1 for e in evidence.values()
                                for h in e["hits"] if h["signature"])
                say("⚓", f"{len(evidence)} anchor verses → {n_hits} corpus pages "
                    f"({n_labeled} carry 🏷️ signatures)", 1)

            # ---- 3. CLASSIFIER ----
            entries = []
            for cid, score in cand_list:
                c = registry.by_id[cid]
                n_use, w_sum, exemplars = usage_stats(con, cid, run_id)
                e = (f"- concept_id: {cid}\n  name: {c['name']}\n  definition: {c['definition']}")
                if c["aliases"]:
                    e += f"\n  aliases: {', '.join(c['aliases'])}"
                if n_use:
                    e += f"\n  used_in: {n_use} sections (total influence {w_sum}); e.g. {', '.join(exemplars)}"
                entries.append(e)
            cand_block = "\n".join(entries) if entries else (
                "(none - the registry is empty; every assignment must be kind=\"new\" "
                "with name, definition, and novelty_confidence)")
            router_new_block = json.dumps(
                [{k: v for k, v in c.items() if k != "_vec"} for c in new_cands],
                ensure_ascii=False) if new_cands else "[]"
            classifier_user = (
                f"SECTION: {ctx['ref']}  [{ctx['tradition']}, language={ctx['language']}]\n\n"
                f"CANDIDATE SET:\n{cand_block}\n\n"
                f"ROUTER DRAFT DISTRIBUTION: "
                f"{json.dumps(router_out.get('draft_distribution', []), ensure_ascii=False)}\n"
                f"ROUTER NEW-CONCEPT CANDIDATES: {router_new_block}\n"
                f"ROUTER RATIONALE: {router_out.get('rationale','')}\n\n"
                f"CORPUS EVIDENCE (per draft concept; anchor verse -> nearest pages "
                f"elsewhere in the corpus, with their section concept signatures):\n"
                f"{format_evidence(evidence)}\n\n"
                f"SECTION TEXT:\n{ctx['text']}")
            # Semantic guard the JSON schema can't express: a signature needs 2-6
            # entries with raw weights near 1.0. Resample (bumped seed) on breach;
            # accept the final attempt as-is rather than lose the section.
            cls_out, c_attempts = None, 0
            for sem_attempt in range(MAX_ATTEMPTS):
                out, used = call_agent(
                    args.model, classifier_sys, classifier_user,
                    think=True, schema=CLASSIFIER_SCHEMA,
                    seed_base=seed_base + 5 + sem_attempt * 100, log=log,
                    label="classifier", provider=provider)
                c_attempts += used + 1
                usable = [a for a in out.get("assignments", [])
                          if float(a.get("weight", 0)) > 0]
                raw_sum = sum(float(a["weight"]) for a in usable)
                if 2 <= len(usable) <= 6 and 0.7 <= raw_sum <= 1.3:
                    cls_out = out
                    break
                say("⚠️", f"semantic guard: {len(usable)} assignments, "
                    f"raw weight sum {raw_sum:.2f} — resampling", 2)
                log({"event": "semantic_retry", "section": section_id,
                     "attempt": sem_attempt, "n_assignments": len(usable),
                     "raw_weight_sum": round(raw_sum, 3)})
                cls_out = out  # keep last; accepted if retries exhaust

            # ---- 4. GATE + COMMIT ----
            # Cold start: with an empty registry novelty is certain, no gate.
            threshold = 0.0 if len(registry) == 0 else tau(
                len(registry), args.tau0, args.tau_max, args.tau_k)
            valid_ids = {cid for cid, _ in cand_list}
            final, new_created, rejected = [], [], []
            for a in cls_out.get("assignments", []):
                w = float(a.get("weight", 0))
                if w <= 0:
                    continue
                # Content-based dispatch: small models sometimes mislabel `kind`
                # (e.g. kind="existing" with empty concept_id on a cold registry)
                # or emit near-miss ids. Resolution chain: exact id -> slugified
                # name/alias of the id -> the assignment's own name field.
                cid = (a.get("concept_id") or "").strip()
                name = (a.get("name") or "").strip()
                definition = (a.get("definition") or "").strip()
                slug_map = {slugify(n): i for n, i in name_map.items()}
                resolved = (cid if cid in registry.by_id else None) \
                    or slug_map.get(slugify(cid) if cid else "") \
                    or name_map.get(name.lower()) \
                    or slug_map.get(slugify(name) if name else "")
                if resolved:
                    if cid and resolved != cid:
                        log({"event": "recovered_concept_id", "section": section_id,
                             "given": cid, "resolved": resolved})
                    if resolved not in valid_ids:
                        log({"event": "offlist_concept_id", "section": section_id,
                             "cid": resolved})
                    final.append((resolved, w, a.get("rationale", "")))
                else:
                    conf = float(a.get("novelty_confidence") or 0.5)
                    if not name or not definition:
                        log({"event": "unusable_assignment", "section": section_id,
                             "assignment": a})
                        continue
                    if name.lower() in name_map:      # exists under same name: reuse
                        final.append((name_map[name.lower()], w, a.get("rationale", "")))
                        continue
                    pre = next((c for c in new_cands if c["name"].lower() == name.lower()
                                and "_vec" in c), None)
                    vec = pre["_vec"] if pre is not None else embed_texts(
                        [f"{name}: {definition}"], args.embed_model)[0]
                    nearest = registry.nearest(vec, 1)
                    if conf >= threshold:
                        cid = slugify(name)
                        if cid in registry.by_id:
                            cid = f"{cid}_{len(registry)}"
                        registry.add(cid, name, definition, vec, section_id, run_id,
                                     args.embed_model)
                        name_map[name.lower()] = cid
                        new_created.append(cid)
                        final.append((cid, w, a.get("rationale", "")))
                    else:
                        fallback = nearest[0][0] if nearest else None
                        rejected.append({"name": name, "definition": definition,
                                         "confidence": conf, "threshold": round(threshold, 3),
                                         "nearest": fallback})
                        if fallback:  # weight flows to nearest existing concept
                            final.append((fallback, w, f"[gated->nearest] {a.get('rationale','')}"))

            # merge duplicate ids, renormalize to 1.0
            merged: dict[str, list] = {}
            for cid, w, rat in final:
                if cid in merged:
                    merged[cid][0] += w
                else:
                    merged[cid] = [w, rat]
            total_w = sum(v[0] for v in merged.values())
            if not merged or total_w <= 0:
                log({"event": "empty_signature", "section": section_id})
                say("❌", f"EMPTY signature — section skipped (see decisions.jsonl)", 1)
                continue
            con.executemany(
                "INSERT OR REPLACE INTO section_concepts (section_id, concept_id, weight, rationale, run_id) "
                "VALUES (?,?,?,?,?)",
                [(section_id, cid, round(w / total_w, 4), rat, run_id)
                 for cid, (w, rat) in merged.items()])
            con.commit()
            export_artifacts(con, run_id, args.db)

            router_log = dict(router_out)
            router_log["new_candidates"] = [
                {k: v for k, v in c.items() if k != "_vec"} for c in new_cands]
            log({"event": "section_done", "section": section_id,
                 "evidence": evidence,
                 "router": router_log,
                 "classifier": {"assignments": cls_out.get("assignments", []),
                                "rationale": cls_out.get("rationale", "")},
                 "candidate_set": [c for c, _ in cand_list],
                 "tau": round(threshold, 3), "new_created": new_created,
                 "rejected": rejected, "attempts": [r_attempts, c_attempts],
                 "renormalized_from": round(total_w, 3),
                 "elapsed_s": round(time.time() - t0, 1)})
            for cid, (w, _rat) in sorted(merged.items(), key=lambda x: -x[1][0]):
                say("🌱" if cid in new_created else "♻️",
                    f"{registry.by_id[cid]['name']}  {w / total_w:.2f}", 1)
            for r in rejected:
                say("🚧", f"“{r['name']}” conf {r['confidence']:.2f} < τ {r['threshold']} "
                    f"→ weight to {r['nearest'] or '(none)'}", 1)
            say("✅", f"committed {len(merged)} concepts · 🧮 registry {len(registry)} "
                f"τ {threshold:.2f} · ⏱️ {time.time() - t0:.0f}s", 1)
            dur = time.time() - t0
            med = statistics.median(durations[-ETA_WINDOW:]) if durations else dur
            if durations and dur > 4 * med:
                n_outliers += 1        # backoff stall etc. — excluded from the ETA
            else:
                durations.append(dur)
                med = statistics.median(durations[-ETA_WINDOW:])
            eta_s = med * (len(todo) - idx)
            say("⏳", f"{idx}/{len(todo)} done · median {fmt_dur(med)}/section "
                f"(last {min(len(durations), ETA_WINDOW)}"
                + (f", {n_outliers} stall(s) excluded" if n_outliers else "") + ") · "
                f"ETA {fmt_dur(eta_s)} (~{time.strftime('%H:%M', time.localtime(time.time() + eta_s))})", 1)
        except RuntimeError as e:
            # Cloud/Ollama exhausted backoff — pause cleanly; section not committed.
            abort_err = str(e)
            break

    if abort_err:
        log({"event": "run_aborted", "section": section_id,
             "error": abort_err[:500]})
        say("❌", f"pass aborted at {section_id}: {clip(abort_err, 140)}")
    remaining = ordered_sections(con, run_id, args.order)
    if remaining:
        say("⏸️", f"run paused with {len(remaining)} sections still unsigned "
            f"(partial pass — not marking finished)")
    else:
        con.execute("UPDATE runs SET finished_at=? WHERE run_id=?", (now_iso(), run_id))
        con.commit()
        say("✅", f"[{run_id}] complete · 🧮 registry={len(registry)}")
    hr()
    art_dir = ARTIFACTS if Path(args.db).stem == "atlas" else ARTIFACTS / Path(args.db).stem
    say("💾", f"artifacts: {art_dir / 'concept_dictionary.json'} + concept_hash.json")
    say("💾", f"decisions: {decisions_path}")
    if not remaining:
        pass  # complete line already printed
    else:
        say("📚", f"[{run_id}] partial · 🧮 registry={len(registry)} · "
            f"resume with: make agent-resume "
            + (f"PROVIDER={provider}" if provider else "CLOUD=1"))
    return 1 if abort_err else 0


if __name__ == "__main__":
    sys.exit(main())
