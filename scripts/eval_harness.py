#!/usr/bin/env python3
"""Pre-flight eval harness for concept ingestion (run BEFORE the full pass).

Runs the agent pipeline twice over 12 golden sections in ISOLATED copies of
the database (concept state wiped, different seeds), then checks:

  1. Signature invariants  - every section signed, 2-6 concepts, weights = 1.0
  2. Registry health       - size bounds, stopword concepts, near-duplicate defs
  3. Parallel convergence  - known-parallel sections share concepts
                             (incl. Genesis 1 in Hebrew vs English: same text,
                             two languages - the cross-lingual acid test)
  4. Inter-run agreement   - concepts matched across runs by definition
                             embedding; mean signature overlap A vs B
                             (low = signatures reflect sampling noise, not text)

The real db/atlas.db is never written. Exit: 0 pass, 1 warnings, 2 fail.

Usage:
  python scripts/eval_harness.py                # full harness (~30 min GPU)
  python scripts/eval_harness.py --check-only runs/eval_YYYYMMDD_HHMMSS
"""

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from atlas_lib import blob_to_vec, hr, say

ROOT = Path(__file__).resolve().parent.parent

GOLDEN = [
    # cross-lingual anchor: the same text in Hebrew and English
    "tanakh_he_uxlc:genesis:1",
    "bible_en_web:genesis:1",
    # cross-tradition narrative parallel: the flood
    "tanakh_he_uxlc:genesis:7",
    "quran_ar_tanzil:s071:1",          # Nuh
    # cross-tradition covenant/law parallel
    "tanakh_he_uxlc:exodus:20",        # decalogue
    "quran_ar_tanzil:s002:2",          # Al-Baqara 2:40-71, covenant with Israel
    # intra-tradition doctrinal tension
    "bible_en_web:romans:3",
    "bible_en_web:james:2",
    # thematic echo across corpora
    "bible_en_web:john:1",             # logos/creation
    # edge cases
    "quran_ar_tanzil:s112:1",          # Al-Ikhlaas: 144 chars, shortest
    "quran_ar_tanzil:s001:1",          # Al-Fatiha: short liturgical
    "tanakh_he_uxlc:chronicles_1:1",   # genealogy: the "boring control"
]

# (a, b, level): pairs expected to share concepts. "fail" pairs break the
# harness when disjoint; "warn" pairs only warn (looser parallels).
PARALLEL_PAIRS = [
    ("tanakh_he_uxlc:genesis:1", "bible_en_web:genesis:1", "fail"),
    ("tanakh_he_uxlc:genesis:7", "quran_ar_tanzil:s071:1", "warn"),
    ("tanakh_he_uxlc:exodus:20", "quran_ar_tanzil:s002:2", "warn"),
    ("bible_en_web:romans:3", "bible_en_web:james:2", "warn"),
]

# Registry bounds for 12 sections at 2-6 concepts each
REGISTRY_MIN, REGISTRY_MAX = 6, 45
STOPWORD_SHARE = 0.75          # concept used in more sections than this -> warn
DUP_COS_WARN, DUP_COS_FAIL = 0.90, 0.97
MATCH_COS = 0.75               # cross-run concept match threshold
AGREEMENT_PASS, AGREEMENT_WARN = 0.40, 0.25


def wiped_copy(src: Path, dst: Path):
    shutil.copy(src, dst)
    con = sqlite3.connect(dst)
    con.executescript(
        "DELETE FROM section_concepts; DELETE FROM concepts; DELETE FROM runs;")
    con.commit()
    con.close()


def run_agent(db: Path, run_id: str, seed: int, model: str, embed_model: str) -> int:
    cmd = [sys.executable, str(ROOT / "scripts/agent_conceptor.py"),
           "--db", str(db), "--run-id", run_id, "--seed", str(seed),
           "--model", model, "--embed-model", embed_model,
           "--sections", ",".join(GOLDEN)]
    say("🚀", f"launching {run_id} (seed {seed}) against {db.name}")
    return subprocess.run(cmd).returncode


def load_run(db: Path, run_id: str):
    con = sqlite3.connect(db)
    sigs: dict[str, dict[str, float]] = {}
    for sec, cid, w in con.execute(
            "SELECT section_id, concept_id, weight FROM section_concepts "
            "WHERE run_id=?", (run_id,)):
        sigs.setdefault(sec, {})[cid] = w
    concepts = {}
    for cid, name, definition, emb in con.execute(
            "SELECT concept_id, name, definition, embedding FROM concepts"):
        concepts[cid] = {"name": name, "definition": definition,
                         "vec": blob_to_vec(emb) if emb else None}
    con.close()
    return sigs, concepts


class Tally:
    def __init__(self):
        self.fails, self.warns = [], []

    def check(self, ok, msg, level="fail"):
        if ok:
            say("✅", msg, 1)
        elif level == "warn":
            say("⚠️", msg + "  — WARN", 1)
            self.warns.append(msg)
        else:
            say("❌", msg + "  — FAIL", 1)
            self.fails.append(msg)


def check_run(tag, sigs, concepts, tally):
    hr(f"CHECKS · run {tag}")
    missing = [s for s in GOLDEN if s not in sigs]
    tally.check(not missing, f"all {len(GOLDEN)} sections signed"
                + (f" (missing: {missing})" if missing else ""))
    bad_n = {s: len(c) for s, c in sigs.items() if not 2 <= len(c) <= 6}
    tally.check(not bad_n, f"2-6 concepts per signature"
                + (f" (violations: {bad_n})" if bad_n else ""))
    bad_w = {s: round(sum(c.values()), 3) for s, c in sigs.items()
             if abs(sum(c.values()) - 1.0) > 0.01}
    tally.check(not bad_w, "weights sum to 1.0 ± 0.01"
                + (f" (violations: {bad_w})" if bad_w else ""))

    n = len(concepts)
    tally.check(REGISTRY_MIN <= n <= REGISTRY_MAX,
                f"registry size {n} within [{REGISTRY_MIN}, {REGISTRY_MAX}]",
                level="warn")
    usage = {}
    for c in sigs.values():
        for cid in c:
            usage[cid] = usage.get(cid, 0) + 1
    stop = {concepts[c]["name"]: u for c, u in usage.items()
            if u / max(len(sigs), 1) > STOPWORD_SHARE}
    tally.check(not stop, "no stopword concepts (used in >75% of sections)"
                + (f" — {stop}" if stop else ""), level="warn")

    vecs = {c: d["vec"] for c, d in concepts.items() if d["vec"] is not None}
    if len(vecs) >= 2:
        ids = list(vecs)
        m = np.vstack([vecs[c] for c in ids])
        m = m / np.linalg.norm(m, axis=1, keepdims=True)
        sim = m @ m.T
        np.fill_diagonal(sim, -1)
        i, j = np.unravel_index(np.argmax(sim), sim.shape)
        worst = sim[i, j]
        pair = f"“{concepts[ids[i]]['name']}” / “{concepts[ids[j]]['name']}”"
        tally.check(worst < DUP_COS_FAIL,
                    f"max definition similarity {worst:.3f} ({pair}) < {DUP_COS_FAIL}")
        tally.check(worst < DUP_COS_WARN,
                    f"max definition similarity {worst:.3f} < {DUP_COS_WARN}",
                    level="warn")

    for a, b, level in PARALLEL_PAIRS:
        sa, sb = sigs.get(a, {}), sigs.get(b, {})
        shared = set(sa) & set(sb)
        overlap = sum(min(sa[c], sb[c]) for c in shared)
        names = [concepts[c]["name"] for c in shared]
        tally.check(bool(shared),
                    f"parallel {a.split(':', 1)[1]} ↔ {b.split(':', 1)[1]}: "
                    f"{len(shared)} shared, overlap {overlap:.2f}"
                    + (f" ({', '.join(names[:3])})" if names else ""),
                    level=level)


def match_concepts(ca, cb):
    """Greedy 1:1 match of run-A concepts to run-B by definition cosine."""
    ia = [c for c in ca if ca[c]["vec"] is not None]
    ib = [c for c in cb if cb[c]["vec"] is not None]
    if not ia or not ib:
        return {}
    ma = np.vstack([ca[c]["vec"] for c in ia])
    mb = np.vstack([cb[c]["vec"] for c in ib])
    ma = ma / np.linalg.norm(ma, axis=1, keepdims=True)
    mb = mb / np.linalg.norm(mb, axis=1, keepdims=True)
    sim = ma @ mb.T
    pairs = [(sim[i, j], i, j) for i in range(len(ia)) for j in range(len(ib))]
    mapping, used_a, used_b = {}, set(), set()
    for s, i, j in sorted(pairs, reverse=True):
        if s < MATCH_COS:
            break
        if i in used_a or j in used_b:
            continue
        mapping[ia[i]] = ib[j]
        used_a.add(i)
        used_b.add(j)
    return mapping


def check_agreement(sa, ca, sb, cb, tally):
    hr("CHECKS · inter-run agreement (A vs B)")
    mapping = match_concepts(ca, cb)
    say("🕸️", f"{len(mapping)} of {len(ca)} run-A concepts matched to run-B "
        f"(definition cosine ≥ {MATCH_COS})", 1)
    per_section = {}
    for sec in GOLDEN:
        a, b = sa.get(sec, {}), sb.get(sec, {})
        per_section[sec] = sum(min(w, b[mapping[c]])
                               for c, w in a.items()
                               if c in mapping and mapping[c] in b)
    mean = sum(per_section.values()) / max(len(per_section), 1)
    for sec, ov in sorted(per_section.items(), key=lambda x: x[1]):
        say("⚖️", f"{sec}: overlap {ov:.2f}", 2)
    tally.check(mean >= AGREEMENT_WARN,
                f"mean inter-run signature overlap {mean:.2f} ≥ {AGREEMENT_WARN}")
    tally.check(mean >= AGREEMENT_PASS,
                f"mean inter-run signature overlap {mean:.2f} ≥ {AGREEMENT_PASS}",
                level="warn")
    return mean, per_section


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "db/atlas.db"))
    ap.add_argument("--model", default="atlas-conceptor")
    ap.add_argument("--embed-model", default="bge-m3")
    ap.add_argument("--check-only", default=None, metavar="EVAL_DIR",
                    help="skip the runs; re-check an existing eval directory")
    args = ap.parse_args()

    if args.check_only:
        eval_dir = Path(args.check_only)
    else:
        eval_dir = ROOT / "runs" / f"eval_{time.strftime('%Y%m%d_%H%M%S')}"
        eval_dir.mkdir(parents=True)

    hr("EVAL HARNESS")
    say("🚀", f"12 golden sections × 2 isolated runs → {eval_dir}")

    dbs = {"A": eval_dir / "atlas_A.db", "B": eval_dir / "atlas_B.db"}
    if not args.check_only:
        # check for leftover concept state in the real DB (informational only:
        # eval copies are wiped regardless, but the FULL run should start cold)
        con = sqlite3.connect(args.db)
        leftover = con.execute("SELECT COUNT(*) FROM concepts").fetchone()[0]
        con.close()
        if leftover:
            say("⚠️", f"main DB has {leftover} leftover concepts from earlier "
                f"runs — wipe before the full pass (make agents-reset)")
        for tag, db in dbs.items():
            wiped_copy(Path(args.db), db)
        for tag, seed in (("A", 1000), ("B", 9000)):
            rc = run_agent(dbs[tag], f"eval_{tag}", seed, args.model, args.embed_model)
            if rc != 0:
                say("❌", f"run eval_{tag} exited {rc}")
                return 2

    sa, ca = load_run(dbs["A"], "eval_A")
    sb, cb = load_run(dbs["B"], "eval_B")
    tally = Tally()
    check_run("A", sa, ca, tally)
    check_run("B", sb, cb, tally)
    mean, per_section = check_agreement(sa, ca, sb, cb, tally)

    summary = {
        "golden": GOLDEN,
        "registry_sizes": {"A": len(ca), "B": len(cb)},
        "mean_agreement": round(mean, 3),
        "agreement_per_section": {k: round(v, 3) for k, v in per_section.items()},
        "fails": tally.fails, "warns": tally.warns,
    }
    (eval_dir / "summary.json").write_text(json.dumps(summary, indent=1))

    hr("VERDICT")
    say("💾", f"summary: {eval_dir / 'summary.json'}")
    if tally.fails:
        say("❌", f"{len(tally.fails)} failure(s), {len(tally.warns)} warning(s) "
            f"— fix before the full run")
        return 2
    if tally.warns:
        say("⚠️", f"0 failures, {len(tally.warns)} warning(s) — review, then proceed")
        return 1
    say("✅", "all checks passed — clear to launch the full ingestion")
    return 0


if __name__ == "__main__":
    sys.exit(main())
