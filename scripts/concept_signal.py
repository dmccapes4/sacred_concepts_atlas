"""Concept-signal search: retrieval through the concept space + graph.

Separation of responsibilities (KISS): this arm never touches FTS or page
embeddings for *ranking* — it matches the query against concept DEFINITIONS,
walks one co-occurrence hop in the concept graph, and scores sections by
their signature weights. Text retrieval finds pages that share words or
embedding-space neighborhoods with the query; this finds sections the
ingestion classifier *analyzed* as being about the matched concepts. The two
buckets stay separate all the way to the gap agent, which judges both.

Page reduction happens in the caller: a section is 1-6 pages, so the caller
picks the best page(s) per signal section using its own query embedding.

Pure numpy + SQL over concepts / section_concepts / edges — no LLM calls,
safe and fast (~ms) on any DB with a completed ingestion run.
"""
from __future__ import annotations

import numpy as np

from atlas_lib import blob_to_vec

CONCEPT_SIM_FLOOR = 0.45   # min query↔definition cosine for a seed concept
EXPAND_PER_SEED = 2        # co-occurrence neighbors added per seed concept
EXPAND_DISCOUNT = 0.5      # neighbor score = seed_sim * npmi * discount


class ConceptSignal:
    """Query embedding -> weighted sections, via concepts + co-occurrence edges."""

    def __init__(self, con, run_id: str | None = None):
        self.ok = False
        row = con.execute(
            "SELECT run_id FROM runs WHERE finished_at IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 1").fetchone() \
            if run_id is None else (run_id,)
        if not row:
            return
        self.run_id = row[0]
        rows = con.execute(
            "SELECT concept_id, name, embedding FROM concepts "
            "WHERE status='active' AND embedding IS NOT NULL").fetchall()
        if not rows:
            return
        self.concept_ids = [r[0] for r in rows]
        self.names = {r[0]: r[1] for r in rows}
        mat = np.vstack([blob_to_vec(r[2]) for r in rows])
        self.mat = mat / np.linalg.norm(mat, axis=1, keepdims=True)
        # signatures: concept -> [(section, weight)]
        self.sections_of: dict[str, list[tuple[str, float]]] = {}
        n_sig = 0
        for sec, cid, w in con.execute(
                "SELECT section_id, concept_id, weight FROM section_concepts "
                "WHERE run_id=?", (self.run_id,)):
            self.sections_of.setdefault(cid, []).append((sec, w))
            n_sig += 1
        if not n_sig:
            return
        # co-occurrence graph (undirected): concept -> [(neighbor, npmi)]
        self.neighbors: dict[str, list[tuple[str, float]]] = {}
        for a, b, w in con.execute(
                "SELECT src_id, dst_id, weight FROM edges "
                "WHERE kind='co_occurrence' AND run_id=?", (self.run_id,)):
            self.neighbors.setdefault(a, []).append((b, w))
            self.neighbors.setdefault(b, []).append((a, w))
        for lst in self.neighbors.values():
            lst.sort(key=lambda x: -x[1])
        self.n_edges = sum(len(v) for v in self.neighbors.values()) // 2
        self.ok = True

    def match_concepts(self, qvec: np.ndarray, top_c: int = 6,
                       floor: float = CONCEPT_SIM_FLOOR):
        """-> [(concept_id, score, via)] seeds + one-hop expansion."""
        q = np.asarray(qvec, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        sims = self.mat @ q
        order = np.argsort(-sims)[:top_c]
        scored: dict[str, tuple[float, str | None]] = {}
        for i in order:
            if sims[i] < floor:
                break
            scored[self.concept_ids[i]] = (float(sims[i]), None)
        for cid, (s, _) in list(scored.items()):
            for nb, npmi in self.neighbors.get(cid, [])[:EXPAND_PER_SEED]:
                cand = s * npmi * EXPAND_DISCOUNT
                if nb not in scored or scored[nb][0] < cand:
                    scored[nb] = (cand, cid)
        return sorted(((cid, s, via) for cid, (s, via) in scored.items()),
                      key=lambda x: -x[1])

    def search(self, qvec: np.ndarray, top_c: int = 6,
               top_sections: int = 8, floor: float = CONCEPT_SIM_FLOOR):
        """-> (sections, concepts): ranked [(section_id, score, evidence)]
        where evidence = [(concept_name, sig_weight, concept_score, via_name)],
        plus the matched-concept list for logging."""
        concepts = self.match_concepts(qvec, top_c=top_c, floor=floor)
        sec_score: dict[str, float] = {}
        sec_ev: dict[str, list] = {}
        for cid, cs, via in concepts:
            for sec, w in self.sections_of.get(cid, []):
                sec_score[sec] = sec_score.get(sec, 0.0) + cs * w
                sec_ev.setdefault(sec, []).append(
                    (self.names[cid], w, round(cs, 3),
                     self.names.get(via) if via else None))
        ranked = sorted(sec_score.items(), key=lambda x: -x[1])[:top_sections]
        return ([(sec, round(s, 4), sorted(sec_ev[sec], key=lambda e: -e[1] * e[2]))
                 for sec, s in ranked],
                [(self.names[c], round(s, 3), self.names.get(v) if v else None)
                 for c, s, v in concepts])
