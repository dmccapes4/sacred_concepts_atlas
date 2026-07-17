#!/usr/bin/env python3
"""Watchdog for a long concept-ingestion run: codified abort criteria.

Reads runs/<run_id>/decisions.jsonl plus the DB and evaluates the run against
explicit health bounds, so "should I kill this at hour 3?" is a command, not
a judgment call over scrolling logs.

ABORT criteria (exit 2 - kill and diagnose):
  A1 stall        decisions.jsonl silent for > --stall-min minutes (unfinished run)
  A2 empty rate   > 5% of sections produced empty signatures      (n >= 30)
  A3 runaway      registry > --registry-cap, OR > 35 new concepts
                  in the trailing 100 sections after 150 done (mint
                  rate must decline for tau to be doing its job)
  A4 slowdown     median section time (trailing 20) > 300s (ETA blown ~4x)

WARN criteria (exit 1 - watch closely):
  W1 stopword     a concept appears in > 50% of sections           (n >= 60)
  W2 retries      > 40% of trailing 100 sections needed semantic retries
  W3 slow         median section time (trailing 20) > 150s
  W4 near-dupes   > 5 concept-definition pairs with cosine > 0.92

Exit 0 = healthy. Single-shot by default; --watch N repeats every N seconds
until abort (for `watch`-style monitoring in a second terminal).

Usage:
  python scripts/ingestion_watchdog.py                 # latest unfinished run
  python scripts/ingestion_watchdog.py --run-id run_X --watch 300
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from statistics import median

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from atlas_lib import blob_to_vec, hr, say

ROOT = Path(__file__).resolve().parent.parent


def load_events(path: Path):
    done, empties, retry_secs = [], 0, set()
    if not path.exists():
        return done, empties, retry_secs
    with open(path) as f:
        for line in f:
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            ev = o.get("event")
            if ev == "section_done":
                done.append(o)
            elif ev == "empty_signature":
                empties += 1
            elif ev == "semantic_retry":
                retry_secs.add(o.get("section"))
    return done, empties, retry_secs


def check(con, run_id, args) -> int:
    decisions = ROOT / "runs" / run_id / "decisions.jsonl"
    done, empties, retry_secs = load_events(decisions)
    n = len(done)
    finished = con.execute("SELECT finished_at FROM runs WHERE run_id=?",
                           (run_id,)).fetchone()
    finished = finished and finished[0]
    registry = con.execute(
        "SELECT COUNT(*) FROM concepts WHERE status='active'").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM sections").fetchone()[0]

    fails, warns = [], []
    hr(f"WATCHDOG · {run_id}")
    say("🧮", f"{n} sections done of {total} · registry {registry} · "
        f"{empties} empty signatures" + (" · run FINISHED" if finished else ""))

    # A1 stall
    if not finished and decisions.exists():
        silent_min = (time.time() - decisions.stat().st_mtime) / 60
        if silent_min > args.stall_min:
            fails.append(f"A1 stall: no decision for {silent_min:.0f} min "
                         f"(> {args.stall_min})")
        else:
            say("✅", f"A1 last decision {silent_min:.1f} min ago", 1)

    # A2 empty-signature rate
    if n + empties >= 30:
        rate = empties / (n + empties)
        if rate > 0.05:
            fails.append(f"A2 empty-signature rate {rate:.1%}")
        elif rate > 0.02:
            warns.append(f"A2 empty-signature rate {rate:.1%} (warn band)")
        else:
            say("✅", f"A2 empty-signature rate {rate:.1%}", 1)

    # A3 registry runaway
    if registry > args.registry_cap:
        fails.append(f"A3 registry {registry} > cap {args.registry_cap}")
    elif n >= 150:
        trailing_mint = sum(len(o.get("new_created", [])) for o in done[-100:])
        if trailing_mint > 35:
            fails.append(f"A3 mint rate: {trailing_mint} new concepts in "
                         f"trailing 100 sections (tau not biting)")
        else:
            say("✅", f"A3 registry {registry}, trailing-100 mint {trailing_mint}", 1)
    else:
        say("✅", f"A3 registry {registry} (cap {args.registry_cap}; "
            f"mint-rate check starts at 150 done)", 1)

    # A4 / W3 pace
    if n >= 20:
        med = median(o.get("elapsed_s", 0) for o in done[-20:])
        eta_h = med * (total - n) / 3600
        if med > 300:
            fails.append(f"A4 median section time {med:.0f}s (ETA {eta_h:.0f}h)")
        elif med > 150:
            warns.append(f"W3 median section time {med:.0f}s (ETA {eta_h:.0f}h)")
        else:
            say("✅", f"A4 median section time {med:.0f}s → ETA {eta_h:.0f}h remaining", 1)

    # W1 stopword concepts
    if n >= 60:
        rows = con.execute(
            "SELECT c.name, COUNT(DISTINCT sc.section_id) u FROM section_concepts sc "
            "JOIN concepts c ON c.concept_id = sc.concept_id WHERE sc.run_id=? "
            "GROUP BY sc.concept_id ORDER BY u DESC LIMIT 3", (run_id,)).fetchall()
        stop = [(name, u) for name, u in rows if u / n > 0.5]
        if stop:
            warns.append(f"W1 stopword concepts: {stop}")
        elif rows:
            say("✅", f"W1 most-used concept “{rows[0][0]}” in {rows[0][1]}/{n} sections", 1)

    # W2 semantic-retry rate
    if n >= 30:
        recent = {o["section"] for o in done[-100:]}
        rate = len(recent & retry_secs) / max(len(recent), 1)
        if rate > 0.4:
            warns.append(f"W2 semantic-retry rate {rate:.0%} (trailing 100)")
        else:
            say("✅", f"W2 semantic-retry rate {rate:.0%}", 1)

    # W4 near-duplicate definitions
    vecs = [blob_to_vec(e) for (e,) in con.execute(
        "SELECT embedding FROM concepts WHERE status='active' AND embedding IS NOT NULL")]
    if len(vecs) >= 2:
        m = np.vstack(vecs)
        m = m / np.linalg.norm(m, axis=1, keepdims=True)
        sim = m @ m.T
        np.fill_diagonal(sim, 0)
        dupes = int((sim > 0.92).sum() // 2)
        if dupes > 5:
            warns.append(f"W4 {dupes} concept pairs with definition cosine > 0.92 "
                         f"(merge pass needed)")
        else:
            say("✅", f"W4 near-duplicate definition pairs: {dupes}", 1)

    for w in warns:
        say("⚠️", w, 1)
    for f_ in fails:
        say("❌", f_, 1)
    if fails:
        say("❌", "ABORT RECOMMENDED — kill the run and diagnose before resuming")
        return 2
    if warns:
        say("⚠️", f"{len(warns)} warning(s) — run may continue, watch closely")
        return 1
    say("✅", "run healthy")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "db/atlas.db"))
    ap.add_argument("--run-id", default=None, help="default: latest unfinished run")
    ap.add_argument("--stall-min", type=float, default=15)
    ap.add_argument("--registry-cap", type=int, default=400)
    ap.add_argument("--watch", type=int, default=0, metavar="SECONDS",
                    help="repeat every N seconds until abort")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    run_id = args.run_id
    if not run_id:
        row = con.execute("SELECT run_id FROM runs WHERE finished_at IS NULL "
                          "ORDER BY started_at DESC LIMIT 1").fetchone()
        if not row:
            say("❌", "no unfinished run found (pass --run-id)")
            return 2
        run_id = row[0]

    while True:
        rc = check(con, run_id, args)
        if not args.watch or rc == 2:
            return rc
        time.sleep(args.watch)


if __name__ == "__main__":
    sys.exit(main())
