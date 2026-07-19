#!/usr/bin/env python3
"""Concept-signal harness: does retrieval through the concept space + graph
surface material the text arms miss?

Retrieval-level experiment (no LLM in the loop — mechanical, reproducible):
for each battery query, build two buckets and compare:

  A. semantic-hybrid baseline — per-source embedding search on the query,
     RRF-fused (the strongest text arm the real pipeline has for an
     English question against Hebrew/Arabic sources)
  B. concept-signal bucket — query ↔ concept-definition cosine, one
     co-occurrence hop in the graph, sections scored by signature weight,
     each reduced to its best page (concept_signal.ConceptSignal)

Metrics per query: section-level overlap (Jaccard), signal-only novelty,
per-tradition coverage of each bucket, graph-expansion contribution.
Outputs: runs/concept_signal_harness_<ts>/{log.jsonl, report.md} + a copy of
the report under reviews/.

Usage:
    python scripts/concept_signal_harness.py --db db/atlas.db
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atlas_lib import TRAD_EMOJI, db_connect, embed_texts, hr, now_iso, say
from atlas_query import QueryIndex, rrf_fuse
from concept_signal import ConceptSignal

ROOT = Path(__file__).resolve().parent.parent

BASELINE_K = 8          # per-source semantic top-k before fusion
BUCKET_SECTIONS = 8     # sections per bucket compared

# Thematic (concept-shaped), narrative, legal, and lexical-control queries.
# The control is where the concept arm SHOULD lose: text search nails
# genealogies by vocabulary alone.
BATTERY = [
    ("covenant", "How is a covenant ratified, and what ceremonies seal it?"),
    ("afterlife", "What happens to the wicked after death and at the final judgment?"),
    ("annunciation", "Miraculous birth announcements delivered by angels"),
    ("warfare", "Rules of warfare and the treatment of captives and spoils"),
    ("mercy", "Divine mercy toward sinners who repent and reform"),
    ("reluctant_prophet", "A prophet who resists or fears his divine calling"),
    ("charity", "Obligations of charity and care for the poor and the orphan"),
    ("creation", "The creation of the world and of humanity"),
    ("false_prophets", "False prophets and how to recognize deceptive prophecy"),
    ("genealogy_control", "Genealogical records listing family descendants"),
]


def sections_of(index, pids):
    return [index.meta[p]["section_id"] for p in pids]


def tradition_mix(index, pids):
    return dict(Counter(index.tradition[index.meta[p]["source_id"]]
                        for p in pids))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "db/atlas.db"))
    ap.add_argument("--embed-model", default="bge-m3")
    args = ap.parse_args()

    out_dir = ROOT / "runs" / f"concept_signal_harness_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "log.jsonl"

    def log(obj):
        obj["ts"] = now_iso()
        with open(log_path, "a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    con = db_connect(args.db)
    sources = [r[0] for r in con.execute("SELECT source_id FROM sources")]
    index = QueryIndex(con, args.embed_model, sources)
    signal = ConceptSignal(con)
    if not signal.ok:
        raise SystemExit("no finished ingestion run — concept arm has no input")

    hr("CONCEPT-SIGNAL HARNESS")
    say("🧿", f"run {signal.run_id} · {len(signal.concept_ids)} concepts · "
        f"{signal.n_edges} co-occurrence edges · {len(index.page_ids):,} pages")
    log({"event": "start", "db": args.db, "run_id": signal.run_id,
         "n_concepts": len(signal.concept_ids), "n_edges": signal.n_edges,
         "battery": [q for _, q in BATTERY]})

    results = []
    for qid, query in BATTERY:
        say("❓", f"[{qid}] {query}")
        qvec = embed_texts([query], args.embed_model)[0]

        # A. semantic-hybrid baseline -> top sections
        per_source = index.semantic_search_per_source(qvec, BASELINE_K)
        fused = rrf_fuse([per_source[s] for s in sources if per_source.get(s)])
        base_pages, seen = [], set()
        for pid in sorted(fused, key=lambda p: -fused[p]):
            sec = index.meta[pid]["section_id"]
            if sec in seen:
                continue
            seen.add(sec)
            base_pages.append(pid)
            if len(base_pages) >= BUCKET_SECTIONS:
                break

        # B. concept-signal bucket
        sec_ranked, matched = signal.search(qvec, top_sections=BUCKET_SECTIONS)
        sig_pages = [p for p in
                     (index.best_page_in_section(sec, qvec)
                      for sec, _s, _e in sec_ranked) if p]

        base_secs, sig_secs = set(sections_of(index, base_pages)), \
            set(sections_of(index, sig_pages))
        inter = base_secs & sig_secs
        union = base_secs | sig_secs
        novel = sig_secs - base_secs
        n_via_graph = sum(1 for _c, _s, via in matched if via)
        row = {
            "id": qid, "query": query,
            "matched_concepts": [{"name": n, "score": s, "via": v}
                                 for n, s, v in matched],
            "n_concepts_direct": len(matched) - n_via_graph,
            "n_concepts_via_graph": n_via_graph,
            "baseline": {"pages": base_pages,
                         "refs": [index.meta[p]["ref"] for p in base_pages],
                         "traditions": tradition_mix(index, base_pages)},
            "signal": {"pages": sig_pages,
                       "refs": [index.meta[p]["ref"] for p in sig_pages],
                       "traditions": tradition_mix(index, sig_pages),
                       "top_evidence": [
                           {"section": sec, "score": s,
                            "concepts": [{"name": n, "w": w} for n, w, _cs, _v
                                         in ev[:2]]}
                           for sec, s, ev in sec_ranked[:3]]},
            "jaccard": round(len(inter) / len(union), 3) if union else 0.0,
            "novel_sections": sorted(novel),
            "n_novel": len(novel),
        }
        results.append(row)
        log({"event": "query_result", **row})
        say("🕸️", f"overlap {row['jaccard']:.2f} · {row['n_novel']} signal-only "
            f"sections · {len(matched)} concepts "
            f"({n_via_graph} via graph)", 1)
        for t, n in row["signal"]["traditions"].items():
            say(TRAD_EMOJI.get(t, "•"), f"signal bucket: {n} {t}", 2)

    # ---- aggregate ----
    n_matched = [r for r in results if r["matched_concepts"]]
    mean_j = sum(r["jaccard"] for r in results) / len(results)
    mean_novel = sum(r["n_novel"] for r in results) / len(results)
    trad_base = Counter()
    trad_sig = Counter()
    for r in results:
        trad_base.update(r["baseline"]["traditions"])
        trad_sig.update(r["signal"]["traditions"])
    agg = {"event": "aggregate", "queries": len(results),
           "queries_with_concept_match": len(n_matched),
           "mean_jaccard": round(mean_j, 3),
           "mean_novel_sections": round(mean_novel, 2),
           "tradition_totals_baseline": dict(trad_base),
           "tradition_totals_signal": dict(trad_sig),
           "graph_expansion_used_in": sum(
               1 for r in results if r["n_concepts_via_graph"])}
    log(agg)

    # ---- report ----
    date = time.strftime("%Y-%m-%d")
    L = [f"# Concept-Signal Retrieval Harness — {date}",
         "",
         f"Run `{signal.run_id}` · {len(signal.concept_ids)} active concepts · "
         f"{signal.n_edges} co-occurrence edges · {len(BATTERY)} queries · "
         f"buckets of {BUCKET_SECTIONS} sections.",
         "",
         "**Question:** does retrieval through the concept space + graph "
         "(query ↔ concept-definition cosine → one co-occurrence hop → "
         "sections by signature weight → best page per section) surface "
         "material the semantic text arm misses — and is what it adds "
         "on-topic?",
         "",
         "## Aggregate",
         "",
         f"| metric | value |",
         f"|---|---|",
         f"| mean bucket overlap (Jaccard, section level) | **{mean_j:.2f}** |",
         f"| mean signal-only sections per query | **{mean_novel:.1f}** / {BUCKET_SECTIONS} |",
         f"| queries where concepts matched above floor | {len(n_matched)}/{len(results)} |",
         f"| queries using a graph-expanded concept | {agg['graph_expansion_used_in']}/{len(results)} |",
         f"| baseline bucket tradition totals | {dict(trad_base)} |",
         f"| signal bucket tradition totals | {dict(trad_sig)} |",
         "",
         "## Per query",
         ""]
    for r in results:
        L.append(f"### {r['id']} — “{r['query']}”")
        L.append("")
        c_str = ", ".join(
            f"{c['name']} {c['score']}" + (f" (←{c['via']})" if c["via"] else "")
            for c in r["matched_concepts"][:6]) or "*(none above floor)*"
        L.append(f"- concepts: {c_str}")
        L.append(f"- overlap {r['jaccard']:.2f} · signal-only sections: "
                 f"{r['n_novel']}")
        L.append(f"- baseline: {', '.join(r['baseline']['refs'])}")
        L.append(f"- signal:   {', '.join(r['signal']['refs'])}")
        L.append(f"- traditions: baseline {r['baseline']['traditions']} → "
                 f"signal {r['signal']['traditions']}")
        L.append("")
    L += ["## Reading the numbers", "",
          "- **Low overlap + on-topic signal refs** = the arm adds real "
          "coverage (the design goal: a separate bucket, not a re-ranking).",
          "- **The genealogy control** should favor the baseline — genealogies "
          "are a vocabulary problem, not an analysis problem.",
          "- Signal-bucket tradition mix vs baseline shows whether concept "
          "matching (English definitions regardless of source language) "
          "counteracts the embedder's same-language bias.",
          "",
          f"_log: `{log_path.relative_to(ROOT)}`_", ""]
    report = "\n".join(L)
    (out_dir / "report.md").write_text(report)
    review = ROOT / "reviews" / f"REPORT_CONCEPT_SIGNAL_HARNESS_{date}.md"
    review.write_text(report)
    say("💾", f"report: {review}")
    say("💾", f"log:    {log_path}")
    say("✅", f"mean overlap {mean_j:.2f}, mean novelty {mean_novel:.1f} "
        f"sections/query")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
