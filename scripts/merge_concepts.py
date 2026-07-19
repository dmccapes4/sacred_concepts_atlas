#!/usr/bin/env python3
"""Concept merge pass: collapse near-duplicate registry concepts.

Mechanical shortlist (definition-embedding cosine >= --floor), LLM
adjudication of the borderline band (cosine alone cannot distinguish
'ritual expiation for unknown manslaughter' ~ 'ceremonial cleansing and
atonement for unknown homicide' [duplicate] from 'priestly diagnosis of skin
diseases' ~ 'ritual impurity of garments' [distinct]). Merges re-point
section_concepts rows to the canonical concept (weights summed when both
appear in one signature — the sum-to-1.0 invariant is preserved), losers keep
their row with status='merged' + merged_into, names move to aliases. Edges
must be rebuilt afterwards (make graph).

Usage:
    python scripts/merge_concepts.py --db db/atlas.db                 # adjudicated (openai)
    python scripts/merge_concepts.py --db db/atlas.db --dry-run
    python scripts/merge_concepts.py --db db/atlas.db --no-llm        # cosine >= --auto only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atlas_lib import (blob_to_vec, clip, cloud_chat, db_connect, hr,
                       load_env, now_iso, parse_json_response, say)

ROOT = Path(__file__).resolve().parent.parent

ADJUDICATOR = ("openai", "gpt-4.1")

SYSTEM = """You adjudicate near-duplicate concepts in a comparative-religion
concept registry (Tanakh, Bible, Quran). For each candidate pair decide:

merge — the two names describe the SAME analytical concept; a scholar using
        the registry would be annoyed to find both. Wording differences,
        synonyms, or one being a trivial rephrase of the other.
keep  — they encode a real analytical distinction (different ritual object,
        different actor, different doctrine, different scope), even if the
        wording is similar.

When merging, choose the canonical concept: prefer the more general/accurate
name, breaking ties toward the one used in more sections.

Respond with JSON only:
{"decisions": [{"pair": <int>, "verdict": "merge"|"keep",
                "canonical": "a"|"b", "reason": "<one sentence>"}]}
`canonical` is required for merge verdicts (use "a" for keep verdicts)."""


def load_registry(con):
    rows = con.execute(
        "SELECT concept_id, name, definition, embedding FROM concepts "
        "WHERE status='active' AND embedding IS NOT NULL").fetchall()
    usage = dict(con.execute(
        "SELECT concept_id, COUNT(*) FROM section_concepts GROUP BY concept_id"))
    ids = [r[0] for r in rows]
    meta = {r[0]: {"name": r[1], "definition": r[2], "n": usage.get(r[0], 0)}
            for r in rows}
    mat = np.vstack([blob_to_vec(r[3]) for r in rows])
    mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
    return ids, meta, mat


def shortlist(ids, meta, mat, floor):
    sims = mat @ mat.T
    np.fill_diagonal(sims, 0)
    iu = np.triu_indices(len(ids), 1)
    pairs = []
    for o in np.argsort(-sims[iu]):
        i, j = int(iu[0][o]), int(iu[1][o])
        if sims[i, j] < floor:
            break
        pairs.append({"a": ids[i], "b": ids[j], "cos": float(sims[i, j])})
    return pairs


def adjudicate(pairs, meta):
    lines = []
    for k, p in enumerate(pairs):
        a, b = meta[p["a"]], meta[p["b"]]
        lines.append(f"PAIR {k} (cosine {p['cos']:.3f})\n"
                     f"  a: \"{a['name']}\" (used in {a['n']} sections)\n"
                     f"     def: {a['definition']}\n"
                     f"  b: \"{b['name']}\" (used in {b['n']} sections)\n"
                     f"     def: {b['definition']}")
    provider, model = ADJUDICATOR
    say("🧠", f"🎓 adjudicating {len(pairs)} pairs (☁️ {model})…")
    resp = cloud_chat(provider, model, SYSTEM, "\n\n".join(lines),
                      temperature=0.1, seed=42)
    out = parse_json_response(resp["content"])
    return {d["pair"]: d for d in out["decisions"]}


def apply_merge(con, loser, winner, log):
    """Re-point every section_concepts row from loser to winner (all runs)."""
    moved = summed = 0
    for sec, run, w in con.execute(
            "SELECT section_id, run_id, weight FROM section_concepts "
            "WHERE concept_id=?", (loser,)).fetchall():
        dup = con.execute(
            "SELECT weight FROM section_concepts WHERE section_id=? "
            "AND concept_id=? AND run_id=?", (sec, winner, run)).fetchone()
        if dup:
            con.execute(
                "UPDATE section_concepts SET weight=? WHERE section_id=? "
                "AND concept_id=? AND run_id=?",
                (round(dup[0] + w, 4), sec, winner, run))
            con.execute(
                "DELETE FROM section_concepts WHERE section_id=? "
                "AND concept_id=? AND run_id=?", (sec, loser, run))
            summed += 1
        else:
            con.execute(
                "UPDATE section_concepts SET concept_id=? WHERE section_id=? "
                "AND concept_id=? AND run_id=?", (winner, sec, loser, run))
            moved += 1
    l_name, l_alias = con.execute(
        "SELECT name, aliases FROM concepts WHERE concept_id=?", (loser,)).fetchone()
    aliases = json.loads(con.execute(
        "SELECT aliases FROM concepts WHERE concept_id=?", (winner,)).fetchone()[0])
    aliases = sorted(set(aliases + json.loads(l_alias) + [l_name]))
    con.execute("UPDATE concepts SET aliases=? WHERE concept_id=?",
                (json.dumps(aliases), winner))
    con.execute("UPDATE concepts SET status='merged', merged_into=? "
                "WHERE concept_id=?", (winner, loser))
    log({"event": "merge_applied", "loser": loser, "winner": winner,
         "rows_moved": moved, "rows_summed": summed})
    return moved, summed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "db/atlas.db"))
    ap.add_argument("--floor", type=float, default=0.82,
                    help="cosine floor for the candidate shortlist")
    ap.add_argument("--auto", type=float, default=0.93,
                    help="--no-llm: auto-merge at/above this cosine")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_env()

    out_dir = ROOT / "runs" / f"concept_merge_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "log.jsonl"

    def log(obj):
        obj["ts"] = now_iso()
        with open(log_path, "a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    con = db_connect(args.db)
    ids, meta, mat = load_registry(con)
    hr("CONCEPT MERGE PASS")
    say("🧮", f"registry: {len(ids)} active concepts · shortlist floor {args.floor}")
    pairs = shortlist(ids, meta, mat, args.floor)
    say("🔍", f"{len(pairs)} candidate pairs at cosine ≥ {args.floor}")
    log({"event": "shortlist", "floor": args.floor,
         "pairs": [{**p, "a_name": meta[p["a"]]["name"],
                    "b_name": meta[p["b"]]["name"]} for p in pairs]})

    if args.no_llm:
        decisions = {k: {"verdict": "merge" if p["cos"] >= args.auto else "keep",
                         "canonical": "a" if meta[p["a"]]["n"] >= meta[p["b"]]["n"] else "b",
                         "reason": f"mechanical: cosine threshold {args.auto}"}
                     for k, p in enumerate(pairs)}
    else:
        decisions = adjudicate(pairs, meta)
    log({"event": "decisions", "decisions": decisions if not args.no_llm else
         {k: v for k, v in decisions.items()}})

    # Union-find so transitive merges (A->B, B->C) resolve to one canonical.
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    merges, keeps = [], []
    for k, p in enumerate(pairs):
        d = decisions.get(k) or decisions.get(str(k))
        if not d:
            continue
        if d["verdict"] != "merge":
            keeps.append((p, d))
            say("✅", f"keep  {meta[p['a']]['name']}  ≠  {meta[p['b']]['name']} "
                f"({p['cos']:.2f}) — {clip(d['reason'], 90)}", 1)
            continue
        win_id = p["a"] if d.get("canonical", "a") == "a" else p["b"]
        lose_id = p["b"] if win_id == p["a"] else p["a"]
        ra, rb = find(win_id), find(lose_id)
        if ra != rb:
            parent[rb] = ra
        merges.append((p, d, win_id, lose_id))
        say("♻️", f"merge {meta[lose_id]['name']}  →  {meta[win_id]['name']} "
            f"({p['cos']:.2f}) — {clip(d['reason'], 90)}", 1)

    applied = []
    if not args.dry_run:
        for p, d, win_id, lose_id in merges:
            winner = find(win_id)
            if winner == lose_id:
                continue
            moved, summed = apply_merge(con, lose_id, winner, log)
            applied.append((lose_id, winner, moved, summed, p["cos"], d["reason"]))
        con.commit()

    n_active = con.execute(
        "SELECT COUNT(*) FROM concepts WHERE status='active'").fetchone()[0]

    # singleton census (report-only: a singleton with no near neighbor is a
    # legitimately unique concept, not a defect)
    singles = [cid for cid in ids if meta[cid]["n"] == 1
               and con.execute("SELECT status FROM concepts WHERE concept_id=?",
                               (cid,)).fetchone()[0] == "active"]
    sims = mat @ mat.T
    np.fill_diagonal(sims, 0)
    idx = {c: i for i, c in enumerate(ids)}
    single_rows = []
    for cid in singles:
        j = int(np.argmax(sims[idx[cid]]))
        single_rows.append((meta[cid]["name"], float(sims[idx[cid], j]),
                            meta[ids[j]]["name"]))
    single_rows.sort(key=lambda r: -r[1])

    date = time.strftime("%Y-%m-%d")
    L = [f"# Concept Merge Pass — {date}",
         "",
         f"DB `{args.db}` · shortlist floor {args.floor} · adjudicator "
         f"{'mechanical' if args.no_llm else ADJUDICATOR[1]} · "
         f"{'DRY RUN' if args.dry_run else 'applied'}",
         "",
         f"**{len(pairs)} candidate pairs → {len(merges)} merged, "
         f"{len(keeps)} kept as distinct. Registry: {len(ids)} → "
         f"{n_active} active concepts.**",
         "", "## Merged", ""]
    for lose_id, winner, moved, summed, cos, reason in applied:
        losem = con.execute("SELECT name FROM concepts WHERE concept_id=?",
                            (lose_id,)).fetchone()[0]
        winm = con.execute("SELECT name FROM concepts WHERE concept_id=?",
                           (winner,)).fetchone()[0]
        L.append(f"- **{losem}** → **{winm}** (cos {cos:.2f}, {moved} rows "
                 f"re-pointed, {summed} weight-summed) — {reason}")
    if args.dry_run:
        for p, d, win_id, lose_id in merges:
            L.append(f"- (dry) **{meta[lose_id]['name']}** → "
                     f"**{meta[win_id]['name']}** (cos {p['cos']:.2f}) — {d['reason']}")
    L += ["", "## Kept as distinct", ""]
    for p, d in keeps:
        L.append(f"- {meta[p['a']]['name']}  ≠  {meta[p['b']]['name']} "
                 f"(cos {p['cos']:.2f}) — {d['reason']}")
    L += ["", f"## Singletons ({len(single_rows)} used-once concepts, by "
          "nearest-neighbor cosine)", "",
          "Highest-cosine singletons are merge candidates for a future pass; "
          "the long tail is legitimately unique (tradition-specific rituals, "
          "single-narrative events).", ""]
    for name, cos, near in single_rows[:20]:
        L.append(f"- {name} — nearest: {near} ({cos:.2f})")
    L += ["", f"_log: `{log_path.relative_to(ROOT)}`_", ""]

    report = "\n".join(L)
    (out_dir / "report.md").write_text(report)
    review = ROOT / "reviews" / f"REPORT_CONCEPT_MERGE_{date}.md"
    review.write_text(report)
    log({"event": "done", "merged": len(applied), "kept": len(keeps),
         "registry_after": n_active})
    say("💾", f"report: {review}")
    say("💾", f"log:    {log_path}")
    say("✅", f"registry {len(ids)} → {n_active} active"
        + (" (dry run — nothing written)" if args.dry_run else
           " — rebuild edges: make graph && make graph-discover"))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
