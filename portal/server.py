#!/usr/bin/env python3
"""Sacred Concepts Atlas — query portal (sacred.dylanmccapes.systems).

Thin FastAPI wrapper around scripts/atlas_query.run_query. Sessions are
ephemeral: in-memory registry + a sessions/<id>/session_log.jsonl on disk for
export. No users table, nothing survives a restart on purpose.

Session context: a FIFO char bucket (PORTAL_CONTEXT_CHARS, default 12k) of
prior exchanges (query + executive summary + cited refs). It is injected into
the probe/gap/report stages; the evidence stage never sees it.

Auth/keys: server-side keys from .env (OPENAI_API_KEY, SPACEXAI_API_KEY);
the UI's model selector only offers providers whose key is present. Mid-
session switches are logged as model_switch events in session_log.jsonl.
Client-supplied per-session keys would arrive via a header and need a
per-request key override in atlas_lib.cloud_chat — deliberately not
implemented yet.

Run:  make portal   (or: venv/bin/uvicorn portal.server:app --port 8877)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from atlas_lib import db_connect, load_env, now_iso, say  # noqa: E402
from atlas_query import CLOUD_ROLE_MODELS, run_query  # noqa: E402
from concept_space import ConceptSpace  # noqa: E402

load_env()

DB = os.environ.get("PORTAL_DB", str(ROOT / "db/atlas.db"))
EMBED_MODEL = os.environ.get("PORTAL_EMBED_MODEL", "bge-m3")
CLOUD = os.environ.get("PORTAL_CLOUD", "1") != "0"   # PORTAL_CLOUD=0 -> local Ollama agents
MODEL = os.environ.get("PORTAL_MODEL", "atlas-conceptor")  # local fallback model
CONTEXT_CHAR_BUDGET = int(os.environ.get("PORTAL_CONTEXT_CHARS", "12000"))
SESSIONS_DIR = ROOT / "sessions"

# Providers the UI may offer: cloud providers whose API key is present, plus
# the local Ollama stack. "local" maps to cloud=None in run_query.
PROVIDERS = [p for p, env in (("openai", "OPENAI_API_KEY"),
                              ("grok", "SPACEXAI_API_KEY"))
             if os.environ.get(env)] + ["local"]
DEFAULT_PROVIDER = os.environ.get(
    "PORTAL_PROVIDER", PROVIDERS[0] if CLOUD and PROVIDERS[0] != "local" else "local")


def provider_models(provider: str) -> dict:
    return CLOUD_ROLE_MODELS[provider] if provider != "local" else {"all": MODEL}

# One query at a time process-wide: embeddings share one Ollama, and the
# portal is a single-operator tool for now.
RUN_LOCK = threading.Lock()


class Session:
    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.dir = SESSIONS_DIR / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.dir / "session_log.jsonl"
        self.bucket: list[str] = []          # FIFO context entries
        self.n_exchanges = 0
        self.provider = DEFAULT_PROVIDER
        self.log({"event": "session_start", "session_id": self.id,
                  "provider": self.provider,
                  "models": provider_models(self.provider),
                  "db": DB, "context_char_budget": CONTEXT_CHAR_BUDGET})

    def switch_provider(self, provider: str):
        """Mid-session model switches are part of the record: the same context
        answered by different models is exactly the bias-comparison data."""
        if provider == self.provider:
            return
        self.log({"event": "model_switch", "from": self.provider,
                  "to": provider, "models": provider_models(provider),
                  "after_exchanges": self.n_exchanges})
        self.provider = provider

    def log(self, obj: dict):
        obj["ts"] = now_iso()
        with open(self.log_path, "a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def context(self) -> str:
        return "\n\n".join(self.bucket)

    def context_chars(self) -> int:
        return len(self.context())

    def push_exchange(self, query: str, summary: str, refs: list[str]):
        self.n_exchanges += 1
        entry = (f"[exchange {self.n_exchanges}]\n"
                 f"USER: {query}\n"
                 f"ATLAS: {summary}")
        if refs:
            entry += f"\nCITED: {'; '.join(refs[:12])}"
        self.bucket.append(entry)
        evicted = 0
        while self.context_chars() > CONTEXT_CHAR_BUDGET and len(self.bucket) > 1:
            evicted += len(self.bucket.pop(0))
        if len(self.bucket) == 1 and len(self.bucket[0]) > CONTEXT_CHAR_BUDGET:
            self.bucket[0] = self.bucket[0][-CONTEXT_CHAR_BUDGET:]
        if evicted:
            self.log({"event": "context_evicted", "dropped_chars": evicted,
                      "bucket_chars": self.context_chars()})


SESSIONS: dict[str, Session] = {}

app = FastAPI(title="Sacred Concepts Atlas — Query Portal")

# Concept space is read-only and shared across sessions: load lazily once per
# run_id (the frozen ingestion run) and cache. A separate connection because
# it is read-only and may be touched concurrently with a query's own.
_SPACES: dict[str, ConceptSpace] = {}
_SPACE_LOCK = threading.Lock()


def get_space(run_id: str | None = None) -> ConceptSpace:
    key = run_id or "__latest__"
    with _SPACE_LOCK:
        sp = _SPACES.get(key)
        if sp is None:
            sp = ConceptSpace(db_connect(DB), EMBED_MODEL, run_id)
            _SPACES[key] = sp
            if key == "__latest__":
                _SPACES[sp.run_id] = sp
        return sp


def corpus_stats() -> dict:
    import sqlite3
    con = sqlite3.connect(DB)
    try:
        sources = [{"source_id": s, "text_name": t, "tradition": tr, "language": lg}
                   for s, t, tr, lg in con.execute(
                       "SELECT source_id, text_name, tradition, language FROM sources")]
        pages = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        sigs = con.execute(
            "SELECT COUNT(DISTINCT section_id) FROM section_concepts").fetchone()[0]
    finally:
        con.close()
    return {"sources": sources, "pages": pages, "signed_sections": sigs}


class QueryBody(BaseModel):
    session_id: str
    query: str
    model: str | None = None       # provider choice: "openai" | "grok" | "local"


class ConceptSearchBody(BaseModel):
    session_id: str | None = None
    text: str


class NodeBody(BaseModel):
    session_id: str | None = None
    node_id: str
    kinds: list[str] | None = None


def get_session(sid: str) -> Session:
    s = SESSIONS.get(sid)
    if not s:
        raise HTTPException(404, "unknown or expired session — start a new one")
    return s


@app.post("/api/session")
def new_session():
    s = Session()
    SESSIONS[s.id] = s
    return {"session_id": s.id, "provider": s.provider,
            "providers": {p: provider_models(p) for p in PROVIDERS},
            "models": provider_models(s.provider),
            "context_char_budget": CONTEXT_CHAR_BUDGET, **corpus_stats()}


@app.post("/api/session/{sid}/clear")
def clear_session(sid: str):
    old = SESSIONS.pop(sid, None)
    if old:
        old.log({"event": "session_cleared"})
        shutil.rmtree(old.dir, ignore_errors=True)
    return new_session()


@app.get("/api/session/{sid}/export")
def export_session(sid: str):
    s = get_session(sid)
    return FileResponse(s.log_path, media_type="application/jsonl",
                        filename=f"atlas_session_{s.id}.jsonl")


@app.post("/api/query")
def query(body: QueryBody):
    s = get_session(body.session_id)
    q = body.query.strip()
    if not q:
        raise HTTPException(400, "empty query")
    if body.model:
        if body.model not in PROVIDERS:
            raise HTTPException(400, f"unknown model provider {body.model!r} "
                                     f"(available: {', '.join(PROVIDERS)})")
        s.switch_provider(body.model)
    if not RUN_LOCK.acquire(blocking=False):
        raise HTTPException(429, "a query is already running — wait for it to finish")
    t0 = time.time()
    try:
        ctx = s.context()
        s.log({"event": "user_query", "query": q,
               "provider": s.provider,
               "context_chars_sent": len(ctx),
               "context_exchanges": len(s.bucket)})
        say("🌐", f"[portal {s.id}] query ({s.provider}): {q}")
        try:
            result = run_query(q, db=DB, model=MODEL,
                               cloud=None if s.provider == "local" else s.provider,
                               out_dir=str(s.dir / "queries"),
                               session_context=ctx)
        except Exception as e:
            s.log({"event": "error", "query": q, "error": str(e)[:800]})
            raise HTTPException(502, f"pipeline failed: {e}")
        summary = result["report"].get("executive_summary", "")
        s.log({"event": "report", "query": q, "provider": s.provider,
               "report_md": result["report_md"],
               "executive_summary": summary,
               "evidence_summary": result["evidence"].get("summary", ""),
               "gap_report": result["gap_report"],
               "referenced_pages": result["referenced_pages"],
               "out_dir": result["out_dir"],
               "elapsed_s": result["elapsed_s"]})
        s.push_exchange(q, summary, result["referenced_pages"])
        return {"report_md": result["report_md"],
                "referenced_pages": result["referenced_pages"],
                "concept_signal": result.get("concept_signal", []),
                "provider": s.provider,
                "elapsed_s": result["elapsed_s"],
                "context_chars": s.context_chars(),
                "context_char_budget": CONTEXT_CHAR_BUDGET,
                "exchanges": s.n_exchanges}
    finally:
        RUN_LOCK.release()
        say("🌐", f"[portal {s.id}] done in {time.time() - t0:.0f}s")


@app.get("/api/concept-space")
def concept_space_meta():
    """Frozen-run metadata + seed concepts for the graph view."""
    try:
        sp = get_space()
    except ValueError as e:
        raise HTTPException(503, str(e))
    return {"run_id": sp.run_id, "n_concepts": len(sp.cid),
            "n_signed_sections": len(sp.sig),
            "top_concepts": sp.top_concepts(40)}


@app.post("/api/concept-search")
def concept_search(body: ConceptSearchBody):
    """Route+classify pasted text into a query signature; rank sections by
    signature similarity. Read-only, describe-mode (never mints concepts)."""
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "empty text")
    try:
        sp = get_space()
    except ValueError as e:
        raise HTTPException(503, str(e))
    t0 = time.time()
    result = sp.classify_text(text)
    result["elapsed_s"] = round(time.time() - t0, 2)
    result["text_chars"] = len(text)
    if body.session_id and body.session_id in SESSIONS:
        SESSIONS[body.session_id].log(
            {"event": "concept_search", "text_chars": len(text),
             "run_id": sp.run_id,
             "signature": [s["name"] for s in result["signature"]],
             "top_sections": [s["ref"] for s in result["sections"][:8]],
             "elapsed_s": result["elapsed_s"]})
    return result


@app.get("/api/section/{section_id:path}/preview")
def section_preview(section_id: str):
    sp = get_space()
    text = sp.section_preview(section_id)
    if not text:
        raise HTTPException(404, "unknown section")
    return {"section_id": section_id, "preview": text}


@app.post("/api/graph/node")
def graph_node(body: NodeBody):
    """Center node + edges to neighbors (click a neighbor to re-center)."""
    try:
        sp = get_space()
    except ValueError as e:
        raise HTTPException(503, str(e))
    result = sp.node(body.node_id, kinds=body.kinds)
    if body.session_id and body.session_id in SESSIONS:
        SESSIONS[body.session_id].log(
            {"event": "graph_node", "node_id": body.node_id,
             "center": result["center"]["label"],
             "kinds": result["kinds"],
             "n_neighbors": len(result["neighbors"])})
    return result


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static",
                           html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("PORTAL_HOST", "127.0.0.1"),
                port=int(os.environ.get("PORTAL_PORT", "8877")))
