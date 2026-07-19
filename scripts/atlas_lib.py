"""Shared helpers for the Sacred Concepts Atlas pipeline."""

import hashlib
import json
import os
import re
import sqlite3
import struct
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import requests

OLLAMA = "http://localhost:11434"

# Cloud providers share the OpenAI chat-completions wire format; xAI's API is
# OpenAI-compatible. Per-provider differences live in cloud_chat, not callers.
CLOUD_PROVIDERS = {
    "openai": {"url": "https://api.openai.com/v1/chat/completions",
               "key_env": "OPENAI_API_KEY"},
    "grok": {"url": "https://api.x.ai/v1/chat/completions",
             "key_env": "SPACEXAI_API_KEY"},
}


def load_env(path=None):
    """Minimal .env loader (KEY=VALUE lines); never overrides real env vars."""
    p = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# Transport resilience for cloud calls. Overnight runs die to transient DNS/
# connection blips and rate limits if attempts fire back-to-back (observed
# twice: "Failed to resolve api.openai.com" burned all 3 caller attempts in
# ~5 s). Backoff belongs here, in the transport layer: callers' MAX_ATTEMPTS
# stay reserved for model-output problems (bad JSON, schema misses).
NET_BACKOFF = [5, 15, 45, 90, 180, 300, 300]      # ~15.5 min tolerance per call
RETRYABLE_STATUS = {429, 500, 502, 503, 504}      # rate limit / server-side


def cloud_chat(provider, model, system, user, *, temperature=0.3, seed=None,
               timeout=300, reasoning_effort=None):
    """Cloud chat completion in json_object mode (role prompts all say JSON).

    Grammar-constrained decoding isn't available here the way Ollama's
    json_schema is; format safety comes from parse_json_response + the
    caller's retry/semantic guards, same as the local path. Network errors
    and retryable HTTP statuses back off and retry here (NET_BACKOFF) before
    surfacing to the caller.

    Provider quirks: xAI's grok reasoning models reject some OpenAI sampler
    params and don't document seed support, so the grok payload stays minimal
    (model + messages + response_format) — determinism there rides on the
    caller's retry guards only. grok-4.5 reasons at effort "high" by default
    (~40s+ of thinking per call, billed as output tokens); we default it to
    "low" — the latency/cost tier, and the closest match to gpt-4.1's
    non-reasoning character for the cross-provider bias comparison.
    """
    cfg = CLOUD_PROVIDERS[provider]
    key = os.environ.get(cfg["key_env"])
    if not key:
        raise RuntimeError(f"{cfg['key_env']} not set — put it in .env")
    payload = {"model": model,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}],
               "response_format": {"type": "json_object"}}
    if provider == "openai":
        payload["temperature"] = temperature
        if seed is not None:
            payload["seed"] = seed
    elif provider == "grok" and model.startswith("grok-4.5"):
        # Only grok-4.5 supports the knob — grok-4.20-0309-reasoning 400s on
        # it ("does not support parameter reasoningEffort"), non-reasoning
        # models don't need it. Measured on the real classifier prompt:
        # high (default) 41-47s, low 31s, same 5-assignment signature.
        payload["reasoning_effort"] = reasoning_effort or "low"
    last = None
    for i, wait in enumerate([0] + NET_BACKOFF):
        if wait:
            say("📡", f"cloud API unreachable ({clip(last, 90)}) — "
                f"retrying in {wait}s ({i}/{len(NET_BACKOFF)})", 2)
            time.sleep(wait)
        try:
            r = requests.post(cfg["url"], json=payload, timeout=timeout,
                              headers={"Authorization": f"Bearer {key}"})
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            # ConnectionError covers DNS failures and RST during send;
            # ChunkedEncodingError is an RST mid-response (observed on long
            # grok-4.5 generations) — same remedy, retry with backoff.
            last = e
            continue
        if r.status_code in RETRYABLE_STATUS:
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            continue
        r.raise_for_status()   # non-retryable client errors surface immediately
        return {"content": r.json()["choices"][0]["message"]["content"]}
    raise RuntimeError(
        f"{provider} API unreachable after {len(NET_BACKOFF)} backoff retries "
        f"(~{sum(NET_BACKOFF) // 60} min): {last}")

# ---------- console (see EMOJI_DICTIONARY.md) ----------

TRAD_EMOJI = {"Judaism": "🕎", "Christianity": "✝️", "Islam": "☪️"}


def say(emoji: str, msg: str, indent: int = 0) -> None:
    print(f"{'   ' * indent}{emoji} {msg}", flush=True)


def hr(title: str = "") -> None:
    line = f"──── {title} " if title else "─" * 10
    print(f"\n{line}{'─' * max(0, 66 - len(line))}", flush=True)


def clip(s, n: int = 100) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def db_connect(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=60)
    con.execute("PRAGMA foreign_keys = ON")
    return con


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def strip_marks(text: str) -> str:
    """Normalize Hebrew/Arabic for lexical matching: drop combining marks
    (niqqud, cantillation, harakat), map dagger-alif to alif, drop tatweel.
    FTS5's remove_diacritics only handles Latin script, so we do this ourselves
    at both index time and query time. English text passes through unchanged."""
    import unicodedata
    out = []
    for ch in text:
        if ch == "\u0670":            # Arabic superscript (dagger) alif -> alif
            out.append("\u0627")
        elif ch == "\u0640":          # tatweel
            continue
        elif unicodedata.category(ch) == "Mn":
            continue
        else:
            out.append(ch)
    return "".join(out)


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return re.sub(r"_+", "_", s)


def upsert_source(con: sqlite3.Connection, source_id: str, text_name: str,
                  tradition: str, language: str, version: str, url: str,
                  artifact: Path | None = None) -> None:
    con.execute(
        "INSERT INTO sources (source_id, text_name, tradition, language, version, url, fetched_at, sha256) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source_id) DO UPDATE SET fetched_at=excluded.fetched_at, sha256=excluded.sha256",
        (source_id, text_name, tradition, language, version, url, now_iso(),
         sha256_file(artifact) if artifact else None))


def replace_sections(con: sqlite3.Connection, source_id: str, rows: list[tuple]) -> None:
    """rows: (section_id, book, ref, seq, text, metadata_json). Idempotent per source."""
    con.execute("DELETE FROM sections WHERE source_id=?", (source_id,))
    con.executemany(
        "INSERT INTO sections (section_id, source_id, book, ref, seq, text, metadata) "
        "VALUES (?,?,?,?,?,?,?)",
        [(sid, source_id, book, ref, seq, text, meta) for sid, book, ref, seq, text, meta in rows])
    view = f"v_{source_id}"
    con.execute(f"DROP VIEW IF EXISTS {view}")
    con.execute(f"CREATE VIEW {view} AS SELECT * FROM sections WHERE source_id='{source_id}'")


# ---------- embeddings ----------

def embed_texts(texts: list[str], model: str) -> list[np.ndarray]:
    """Embed via local Ollama. Same backoff as cloud_chat: overnight runs
    die to Ollama stalls (model unload, GPU contention) if a single timeout
    is fatal — observed mid-pass on both Grok and GPT runs.

    Timeout is short (60s): a healthy bge-m3 embed of a few strings returns
    in <2s, so 60s already means Ollama is wedged. Better to fail fast and
    retry through NET_BACKOFF than sit dark for 5 minutes per attempt.
    """
    if not texts:
        return []
    last = None
    for i, wait in enumerate([0] + NET_BACKOFF):
        if wait:
            say("📡", f"ollama embed unreachable ({clip(last, 90)}) — "
                f"retrying in {wait}s ({i}/{len(NET_BACKOFF)})", 2)
            time.sleep(wait)
        try:
            r = requests.post(f"{OLLAMA}/api/embed",
                              json={"model": model, "input": texts},
                              timeout=60)
            r.raise_for_status()
            return [np.asarray(e, dtype=np.float32)
                    for e in r.json()["embeddings"]]
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            last = e
            continue
    raise RuntimeError(
        f"ollama embed unreachable after {len(NET_BACKOFF)} backoff retries "
        f"(~{sum(NET_BACKOFF) // 60} min): {last}")


def vec_to_blob(v: np.ndarray) -> bytes:
    return struct.pack(f"<{len(v)}f", *v.tolist())


def blob_to_vec(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype="<f4")


def cosine_top_k(query: np.ndarray, mat: np.ndarray, k: int) -> list[tuple[int, float]]:
    """Return [(row_index, similarity)] best-first. mat rows need not be normalized."""
    qn = query / (np.linalg.norm(query) + 1e-9)
    mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    sims = mn @ qn
    idx = np.argsort(-sims)[:k]
    return [(int(i), float(sims[i])) for i in idx]


# ---------- ollama chat ----------

def ollama_chat(model: str, system: str, user: str, *, think: bool,
                options: dict, json_schema: dict | None = None,
                timeout: int = 900) -> dict:
    """Single-turn chat. Returns {"content": str, "thinking": str|None}."""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "think": think,
        "options": options,
    }
    if json_schema is not None:
        payload["format"] = json_schema
    r = requests.post(f"{OLLAMA}/api/chat", json=payload, timeout=timeout)
    r.raise_for_status()
    msg = r.json()["message"]
    return {"content": msg.get("content", ""), "thinking": msg.get("thinking")}


def parse_json_response(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(json)?\s*|\s*```$", "", content, flags=re.S)
    return json.loads(content)


# ---------- rising novelty threshold ----------

def tau(n: int, tau0: float, tau_max: float, k: float) -> float:
    return tau_max - (tau_max - tau0) * k / (k + n)
