#!/usr/bin/env python3
"""Phase 4: materialize graph edges from a completed signature run.

Every edge is DERIVED — a pure function of section_concepts (+ sections for
structural/temporal). No LLM calls, no embeddings, no Ollama: safe to run
alongside a live ingestion on another DB. The edges table is a
materialization, droppable and rebuildable at any time (strategy §9).

Edge kinds (connascence taxonomy):
  structural    section↔section  adjacency in-book; Tanakh↔WEB parallel chapters
  temporal      section→section  reading order (seq) per source
  conceptual    section↔section  signature overlap Σ_c min(w_a[c], w_b[c])
  co_occurrence concept↔concept  NPMI over shared section signatures
  co_variance   concept↔concept  Pearson r of weights over jointly-signed sections

Usage:
    python scripts/build_edges.py --db db/atlas.db --run <run_id> --kind all
    python scripts/build_edges.py --db db/atlas.db --run <run_id> --discover
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atlas_lib import db_connect, say, hr, now_iso  # noqa: E402

# Tanakh books that the WEB names with a leading number. Every other book
# shares its slug across the two sources (genesis ↔ genesis), giving us the
# parallel-chapter mapping for free from section_ids.
TANAKH_TO_WEB_SLUG = {
    "chronicles_1": "1_chronicles", "chronicles_2": "2_chronicles",
    "kings_1": "1_kings", "kings_2": "2_kings",
    "samuel_1": "1_samuel", "samuel_2": "2_samuel",
}

ALL_KINDS = ["structural", "temporal", "conceptual", "co_occurrence", "co_variance"]


# ---------- shared loaders ----------

def load_signatures(con, run):
    """-> (sections list, concepts list, dense weight matrix S×C float32)"""
    rows = con.execute(
        "SELECT section_id, concept_id, weight FROM section_concepts "
        "WHERE run_id=?", (run,)).fetchall()
    sections = sorted({r[0] for r in rows})
    concepts = sorted({r[1] for r in rows})
    s_idx = {s: i for i, s in enumerate(sections)}
    c_idx = {c: i for i, c in enumerate(concepts)}
    mat = np.zeros((len(sections), len(concepts)), dtype=np.float32)
    for sec, cid, w in rows:
        mat[s_idx[sec], c_idx[cid]] = w
    return sections, concepts, mat


def section_meta(con):
    """section_id -> (source_id, book, ref, seq, tradition)"""
    return {r[0]: r[1:] for r in con.execute("""
        SELECT sec.section_id, sec.source_id, sec.book, sec.ref, sec.seq,
               s.tradition
        FROM sections sec JOIN sources s ON s.source_id = sec.source_id""")}


def insert_edges(con, rows, kind, run, method):
    con.execute("DELETE FROM edges WHERE kind=? AND run_id IS ? AND method=?",
                (kind, run, method))
    con.executemany(
        "INSERT INTO edges (kind, src_type, src_id, dst_type, dst_id, weight,"
        " metadata, run_id, method) VALUES (?,?,?,?,?,?,?,?,?)",
        [(kind, st, s, dt, d, w, json.dumps(m) if m else None, run, method)
         for st, s, dt, d, w, m in rows])
    con.commit()
    say("🔗", f"{kind}: {len(rows):,} edges ({method})", 1)
    return len(rows)


# ---------- builders ----------

def build_structural(con, run):
    """Adjacency within a book + Tanakh↔WEB parallel chapters.

    Signature-independent, but stamped with run_id anyway so a full edge set
    for a run is self-contained and diffable as a unit.
    """
    meta = section_meta(con)
    rows = []
    # in-book adjacency (consecutive seq within same source+book)
    by_book = defaultdict(list)
    for sid, (src, book, ref, seq, trad) in meta.items():
        by_book[(src, book)].append((seq, sid))
    for (src, book), lst in by_book.items():
        lst.sort()
        for (_, a), (_, b) in zip(lst, lst[1:]):
            rows.append(("section", a, "section", b, 1.0, {"sub": "adjacent"}))
    # parallel chapters: same text, two languages. section_id tail = chapter.
    web = {}
    for sid, (src, book, ref, seq, trad) in meta.items():
        if src == "bible_en_web":
            _, slug, ch = sid.split(":")
            web[(slug, ch)] = sid
    n_par = 0
    for sid, (src, book, ref, seq, trad) in meta.items():
        if src != "tanakh_he_uxlc":
            continue
        _, slug, ch = sid.split(":")
        mate = web.get((TANAKH_TO_WEB_SLUG.get(slug, slug), ch))
        if mate:
            rows.append(("section", sid, "section", mate, 1.0,
                         {"sub": "parallel_text"}))
            n_par += 1
    say("⚓", f"parallel Tanakh↔WEB chapters matched: {n_par}", 1)
    return insert_edges(con, rows, "structural", run, "adjacency_parallel_v1")


def build_temporal(con, run):
    """Reading order per source (directed src→dst). Quran revelation order is
    a future method (revelation_v1) once the Tanzil order index is ingested."""
    meta = section_meta(con)
    by_src = defaultdict(list)
    for sid, (src, book, ref, seq, trad) in meta.items():
        by_src[src].append((seq, sid))
    rows = []
    for src, lst in by_src.items():
        lst.sort()
        for (_, a), (_, b) in zip(lst, lst[1:]):
            rows.append(("section", a, "section", b, 1.0, None))
    return insert_edges(con, rows, "temporal", run, "seq_v1")


def build_conceptual(con, run, cutoff):
    """Pairwise min-sum signature overlap. Dense pass: the matrix is
    2,348 × |registry| ≈ 7 MB; row-vs-all min-sum is the only O(n²·d) step
    in the whole graph build (~30 s)."""
    sections, concepts, mat = load_signatures(con, run)
    n = len(sections)
    say("🧮", f"min-sum over {n:,}×{n:,} section pairs, "
        f"{len(concepts)} concepts, cutoff {cutoff}", 1)
    c_names = dict(con.execute("SELECT concept_id, name FROM concepts"))
    rows = []
    t0 = time.time()
    for i in range(n):
        # Σ_c min(a, b) against all following rows in one vectorized shot
        sims = np.minimum(mat[i], mat[i + 1:]).sum(axis=1)
        for j_off in np.nonzero(sims >= cutoff)[0]:
            j = i + 1 + int(j_off)
            shared_idx = np.nonzero(np.minimum(mat[i], mat[j]) > 0)[0]
            shared = [c_names.get(concepts[k], concepts[k])
                      for k in shared_idx]
            rows.append(("section", sections[i], "section", sections[j],
                         float(sims[j_off]), {"shared_concepts": shared}))
        if i and i % 500 == 0:
            say("⏳", f"{i}/{n} rows · {len(rows):,} edges so far "
                f"· {time.time() - t0:.0f}s", 2)
    return insert_edges(con, rows, "conceptual", run, "minsum_v1")


def build_co_occurrence(con, run, min_count, npmi_floor):
    """Concept↔concept NPMI over section signatures. NPMI ∈ [-1, 1]:
    1 = always together, 0 = independent. Floor keeps only positive
    association with enough support to mean something."""
    sections, concepts, mat = load_signatures(con, run)
    n_sec = len(sections)
    present = mat > 0
    counts = present.sum(axis=0)                     # sections per concept
    c_names = dict(con.execute("SELECT concept_id, name FROM concepts"))
    pair_counts = Counter()
    for row in present:
        idx = np.nonzero(row)[0]
        for a_i in range(len(idx)):
            for b_i in range(a_i + 1, len(idx)):
                pair_counts[(int(idx[a_i]), int(idx[b_i]))] += 1
    rows = []
    for (a, b), c_ab in pair_counts.items():
        if c_ab < min_count:
            continue
        p_ab = c_ab / n_sec
        pmi = math.log(p_ab / ((counts[a] / n_sec) * (counts[b] / n_sec)))
        npmi = pmi / -math.log(p_ab)
        if npmi < npmi_floor:
            continue
        rows.append(("concept", concepts[a], "concept", concepts[b],
                     round(npmi, 4),
                     {"count": int(c_ab), "n_a": int(counts[a]),
                      "n_b": int(counts[b]),
                      "names": [c_names.get(concepts[a]),
                                c_names.get(concepts[b])]}))
    say("🧮", f"{len(pair_counts):,} co-occurring pairs → "
        f"{len(rows):,} above count≥{min_count}, NPMI≥{npmi_floor}", 1)
    return insert_edges(con, rows, "co_occurrence", run, "npmi_v1")


def build_co_variance(con, run, min_joint, r_floor):
    """Pearson r of two concepts' weights over sections where BOTH appear —
    'when they co-occur, do their influences rise and fall together?'
    Anti-covariance (negative r, one displacing the other in a signature)
    is kept too: weight = |r|, sign in metadata."""
    sections, concepts, mat = load_signatures(con, run)
    present = mat > 0
    c_names = dict(con.execute("SELECT concept_id, name FROM concepts"))
    pair_secs = defaultdict(list)
    for s_i, row in enumerate(present):
        idx = np.nonzero(row)[0]
        for a_i in range(len(idx)):
            for b_i in range(a_i + 1, len(idx)):
                pair_secs[(int(idx[a_i]), int(idx[b_i]))].append(s_i)
    rows = []
    for (a, b), secs in pair_secs.items():
        if len(secs) < min_joint:
            continue
        wa, wb = mat[secs, a], mat[secs, b]
        if wa.std() < 1e-9 or wb.std() < 1e-9:
            continue
        r = float(np.corrcoef(wa, wb)[0, 1])
        if abs(r) < r_floor:
            continue
        rows.append(("concept", concepts[a], "concept", concepts[b],
                     round(abs(r), 4),
                     {"r": round(r, 4), "n_joint": len(secs),
                      "names": [c_names.get(concepts[a]),
                                c_names.get(concepts[b])]}))
    say("🧮", f"{sum(1 for s in pair_secs.values() if len(s) >= min_joint):,} "
        f"pairs with ≥{min_joint} joint sections → {len(rows):,} with |r|≥{r_floor}", 1)
    return insert_edges(con, rows, "co_variance", run, "pearson_joint_v1")


# ---------- discovery queue ----------

def norm_book_key(source_id, section_id):
    """Book identity across sources, so Genesis(he) and Genesis(en) count as
    the same book for the 'different book' discovery filter."""
    slug = section_id.split(":")[1]
    if source_id == "tanakh_he_uxlc":
        slug = TANAKH_TO_WEB_SLUG.get(slug, slug)
    return slug


def scripture_families(con):
    """section_id -> quran | hebrew_bible | new_testament.

    Source tradition labels mislead the discovery ranking: Tanakh↔WEB-OT
    pairs are 'cross-tradition' (Judaism vs Christianity) but the same
    scripture in two languages. Family is the honest unit of distance —
    a Quran↔NT link is a discovery; a Psalms(he)↔Isaiah(en) link is an
    intra-canon parallel that happens to cross a language."""
    tanakh_books = {norm_book_key("tanakh_he_uxlc", sid) for (sid,) in con.execute(
        "SELECT section_id FROM sections WHERE source_id='tanakh_he_uxlc'")}
    fam = {}
    for sid, src in con.execute("SELECT section_id, source_id FROM sections"):
        if src == "quran_ar_tanzil":
            fam[sid] = "quran"
        elif norm_book_key(src, sid) in tanakh_books:
            fam[sid] = "hebrew_bible"
        else:
            fam[sid] = "new_testament"
    return fam


def discover(con, run, top_n, out_dir):
    """The Phase 4 flagship: conceptual weight DESC, no structural edge,
    different books, cross-scripture-family pairs ranked first. Ships each
    entry with shared concepts + both rationales for human adjudication."""
    meta = section_meta(con)
    fam = scripture_families(con)
    structural = set()
    for a, b in con.execute(
            "SELECT src_id, dst_id FROM edges WHERE kind='structural' AND run_id=?",
            (run,)):
        structural.add((a, b))
        structural.add((b, a))
    rat = {(s, c): r for s, c, r in con.execute(
        "SELECT section_id, concept_id, rationale FROM section_concepts "
        "WHERE run_id=?", (run,))}
    name_to_id = {n: c for c, n in con.execute("SELECT concept_id, name FROM concepts")}

    entries = []
    for a, b, w, md in con.execute(
            "SELECT src_id, dst_id, weight, metadata FROM edges "
            "WHERE kind='conceptual' AND run_id=? ORDER BY weight DESC", (run,)):
        if (a, b) in structural:
            continue
        src_a, book_a, ref_a, _, trad_a = meta[a]
        src_b, book_b, ref_b, _, trad_b = meta[b]
        if norm_book_key(src_a, a) == norm_book_key(src_b, b):
            continue
        shared = json.loads(md)["shared_concepts"] if md else []
        entries.append({
            "a": {"section": a, "ref": ref_a, "tradition": trad_a,
                  "family": fam[a]},
            "b": {"section": b, "ref": ref_b, "tradition": trad_b,
                  "family": fam[b]},
            "overlap": round(w, 4),
            "cross_family": fam[a] != fam[b],
            "shared_concepts": shared,
            "rationales": {
                a: {c: rat.get((a, name_to_id.get(c, "")), "") for c in shared},
                b: {c: rat.get((b, name_to_id.get(c, "")), "") for c in shared},
            },
        })
    entries.sort(key=lambda e: (not e["cross_family"], -e["overlap"]))
    # Dedupe language twins: Job(he)↔X and Job(en)↔X are one finding. Key on
    # normalized book+chapter for both sides; keep the highest-ranked copy.
    seen, deduped = set(), []
    for e in entries:
        ka = norm_book_key(meta[e["a"]["section"]][0], e["a"]["section"]) \
            + ":" + e["a"]["section"].split(":")[2]
        kb = norm_book_key(meta[e["b"]["section"]][0], e["b"]["section"]) \
            + ":" + e["b"]["section"].split(":")[2]
        key = frozenset((ka, kb))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    entries = deduped
    queue = entries[:top_n]
    n_cross = sum(1 for e in queue if e["cross_family"])
    say("💎", f"discovery queue: {len(entries):,} candidate pairs → top {len(queue)} "
        f"({n_cross} cross-scripture-family)", 1)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "discovery_queue.json").write_text(json.dumps(
        {"run_id": run, "generated_at": now_iso(), "n_candidates": len(entries),
         "queue": queue}, ensure_ascii=False, indent=1))

    lines = [f"# Discovery Queue — run `{run}`",
             "",
             f"Generated {now_iso()} · {len(entries):,} candidate pairs "
             f"(conceptual overlap, no structural edge, different books) · "
             f"top {len(queue)} shown, cross-scripture-family first "
             f"(quran / hebrew_bible / new_testament).",
             "",
             "Adjudication: for each pair, is the shared analysis real "
             "(genuine thematic parallel), an artifact (over-broad concept), "
             "or already catalogued somewhere the structural layer missed?",
             ""]
    for i, e in enumerate(queue, 1):
        tag = "🌍 cross-family" if e["cross_family"] else "same family"
        lines.append(f"## {i}. {e['a']['ref']} ({e['a']['family']}) ↔ "
                     f"{e['b']['ref']} ({e['b']['family']}) — "
                     f"overlap {e['overlap']:.2f} · {tag}")
        lines.append("")
        lines.append(f"**Shared concepts:** {', '.join(e['shared_concepts'])}")
        lines.append("")
        for side in ("a", "b"):
            sec = e[side]["section"]
            for c, r in e["rationales"][sec].items():
                if r:
                    lines.append(f"- *{e[side]['ref']}* · **{c}**: {r}")
        lines.append("")
    (out_dir / "discovery_queue.md").write_text("\n".join(lines))
    say("💾", f"{out_dir}/discovery_queue.md + .json", 1)
    return queue


# ---------- export ----------

def export_graphml(con, run, out_dir, kinds):
    """Minimal GraphML for external exploration (Gephi, yEd, networkx)."""
    from xml.sax.saxutils import escape
    meta = section_meta(con)
    c_names = dict(con.execute("SELECT concept_id, name FROM concepts"))
    nodes, edges = {}, []
    for kind in kinds:
        for st, s, dt, d, w in con.execute(
                "SELECT src_type, src_id, dst_type, dst_id, weight FROM edges "
                "WHERE kind=? AND run_id=?", (kind, run)):
            for typ, nid in ((st, s), (dt, d)):
                if nid not in nodes:
                    if typ == "section":
                        src, book, ref, seq, trad = meta[nid]
                        nodes[nid] = {"type": "section", "label": ref,
                                      "tradition": trad}
                    else:
                        nodes[nid] = {"type": "concept",
                                      "label": c_names.get(nid, nid),
                                      "tradition": ""}
            edges.append((s, d, kind, w))
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
           '<key id="d0" for="node" attr.name="label" attr.type="string"/>',
           '<key id="d1" for="node" attr.name="type" attr.type="string"/>',
           '<key id="d2" for="node" attr.name="tradition" attr.type="string"/>',
           '<key id="d3" for="edge" attr.name="kind" attr.type="string"/>',
           '<key id="d4" for="edge" attr.name="weight" attr.type="double"/>',
           '<graph edgedefault="undirected">']
    for nid, a in nodes.items():
        out.append(f'<node id="{escape(nid)}"><data key="d0">{escape(a["label"])}'
                   f'</data><data key="d1">{a["type"]}</data>'
                   f'<data key="d2">{a["tradition"]}</data></node>')
    for s, d, kind, w in edges:
        out.append(f'<edge source="{escape(s)}" target="{escape(d)}">'
                   f'<data key="d3">{kind}</data><data key="d4">{w}</data></edge>')
    out.append('</graph></graphml>')
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"graph_{'_'.join(kinds)}.graphml"
    p.write_text("\n".join(out))
    say("💾", f"{p} ({len(nodes):,} nodes, {len(edges):,} edges)", 1)


# ---------- main ----------

def latest_finished_run(con):
    row = con.execute(
        "SELECT run_id FROM runs WHERE finished_at IS NOT NULL "
        "ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        raise SystemExit("no finished run in this DB — edges need a completed pass")
    return row[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="db/atlas.db")
    ap.add_argument("--run", help="run_id (default: latest finished run)")
    ap.add_argument("--kind", default="all",
                    choices=ALL_KINDS + ["all"], help="edge kind to build")
    ap.add_argument("--conceptual-cutoff", type=float, default=0.35)
    ap.add_argument("--cooc-min-count", type=int, default=3)
    ap.add_argument("--npmi-floor", type=float, default=0.10)
    ap.add_argument("--covar-min-joint", type=int, default=5)
    ap.add_argument("--covar-r-floor", type=float, default=0.40)
    ap.add_argument("--discover", action="store_true",
                    help="build the discovery queue (needs conceptual+structural)")
    ap.add_argument("--discover-top", type=int, default=100)
    ap.add_argument("--export", action="store_true",
                    help="export GraphML (conceptual + structural)")
    args = ap.parse_args()

    con = db_connect(args.db)
    con.executescript(Path("db/schema.sql").read_text())  # ensure edges table
    run = args.run or latest_finished_run(con)
    signed = con.execute(
        "SELECT COUNT(DISTINCT section_id) FROM section_concepts WHERE run_id=?",
        (run,)).fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM sections").fetchone()[0]
    hr(f"GRAPH BUILD {run}")
    say("🚀", f"db={args.db} · {signed}/{total} sections signed")
    if signed < total:
        say("⚠️", f"run is INCOMPLETE ({signed}/{total}) — edges from a partial "
            "pass are biased toward early-processed traditions (strategy §9.5)")

    out_dir = Path("artifacts") / Path(args.db).stem / "graph"
    kinds = ALL_KINDS if args.kind == "all" else [args.kind]
    t0 = time.time()
    for kind in kinds:
        if kind == "structural":
            build_structural(con, run)
        elif kind == "temporal":
            build_temporal(con, run)
        elif kind == "conceptual":
            build_conceptual(con, run, args.conceptual_cutoff)
        elif kind == "co_occurrence":
            build_co_occurrence(con, run, args.cooc_min_count, args.npmi_floor)
        elif kind == "co_variance":
            build_co_variance(con, run, args.covar_min_joint, args.covar_r_floor)
    if args.discover:
        discover(con, run, args.discover_top, out_dir)
    if args.export:
        export_graphml(con, run, out_dir, ["conceptual", "structural"])
    say("✅", f"graph build done in {time.time() - t0:.0f}s")
    for kind, n in con.execute(
            "SELECT kind, COUNT(*) FROM edges WHERE run_id=? GROUP BY kind", (run,)):
        say("🔗", f"{kind}: {n:,}", 1)


if __name__ == "__main__":
    main()
