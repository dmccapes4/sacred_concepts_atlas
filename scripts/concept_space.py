"""Read-only concept-space & graph access for the portal (Iteration 0).

Two capabilities over a frozen ingestion run, both pure numpy/SQL, no
mutation of the concept space (the roadmap's "output can't grade itself"
rule — exploration never writes):

  classify_text(text)  route+classify pasted text into a query SIGNATURE
                       (existing concepts only, describe-mode) and rank
                       sections by signature min-sum similarity — the same
                       metric build_edges.py uses for conceptual edges.
  node(node_id)        graph traversal: a center node + its edges to
                       neighbors with metadata; the caller re-centers on
                       click.

Loaded once per run and cached; the portal holds one ConceptSpace per run_id.
"""
from __future__ import annotations

import numpy as np

from atlas_lib import blob_to_vec, embed_texts, slugify

# describe-mode signature: how the pasted text is expressed in the corpus's
# OWN vocabulary. Top concepts by query↔definition cosine, floored and
# renormalized to sum 1.0 so it is comparable to a real section signature.
QUERY_SIG_TOPK = 6
QUERY_SIG_FLOOR = 0.40
SECTION_RESULT_K = 30


class ConceptSpace:
    def __init__(self, con, embed_model="bge-m3", run_id=None):
        self.con = con
        self.embed_model = embed_model
        row = (run_id,) if run_id else con.execute(
            "SELECT run_id FROM runs WHERE finished_at IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 1").fetchone()
        if not row:
            raise ValueError("no finished ingestion run in this DB")
        self.run_id = row[0]

        crows = con.execute(
            "SELECT concept_id, name, definition, embedding FROM concepts "
            "WHERE status='active' AND embedding IS NOT NULL").fetchall()
        self.cid = [r[0] for r in crows]
        self.cidx = {c: i for i, c in enumerate(self.cid)}
        self.name = {r[0]: r[1] for r in crows}
        self.definition = {r[0]: r[2] for r in crows}
        self.cmat = np.vstack([blob_to_vec(r[3]) for r in crows])
        self.cmat = self.cmat / np.linalg.norm(self.cmat, axis=1, keepdims=True)

        # section signatures (sparse dicts) + metadata
        self.sig: dict[str, dict[str, float]] = {}
        for sec, cid, w in con.execute(
                "SELECT section_id, concept_id, weight FROM section_concepts "
                "WHERE run_id=?", (self.run_id,)):
            self.sig.setdefault(sec, {})[cid] = w
        self.sec_meta = {r[0]: {"ref": r[1], "book": r[2], "tradition": r[3],
                                "source_id": r[4], "text_name": r[5]}
                         for r in con.execute("""
            SELECT sec.section_id, sec.ref, sec.book, s.tradition, s.source_id,
                   s.text_name
            FROM sections sec JOIN sources s ON s.source_id = sec.source_id""")}
        # usage counts for concept-node metadata
        self.usage = dict(con.execute(
            "SELECT concept_id, COUNT(*) FROM section_concepts WHERE run_id=? "
            "GROUP BY concept_id", (self.run_id,)))

    # ---------- concept search ----------

    def classify_text(self, text: str):
        """Pasted text -> (query_signature, ranked_sections). Describe-mode:
        selects from existing concepts only, never mints. Empty signature if
        nothing clears the floor (honest 'I can't express this')."""
        qvec = embed_texts([text], self.embed_model)[0]
        q = np.asarray(qvec, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        sims = self.cmat @ q
        order = np.argsort(-sims)[:QUERY_SIG_TOPK]
        picks = [(self.cid[i], float(sims[i])) for i in order
                 if sims[i] >= QUERY_SIG_FLOOR]
        signature = self._normalize(picks)
        sections = self._rank_sections(signature)
        return {
            "run_id": self.run_id,
            "signature": [{"concept_id": c, "name": self.name[c],
                           "weight": round(w, 3),
                           "definition": self.definition[c]}
                          for c, w in signature],
            "sections": sections,
        }

    @staticmethod
    def _normalize(picks):
        tot = sum(s for _c, s in picks)
        if tot <= 0:
            return []
        return [(c, s / tot) for c, s in picks]

    def _rank_sections(self, signature):
        if not signature:
            return []
        qd = dict(signature)
        scored = []
        for sec, sig in self.sig.items():
            overlap = sum(min(qd[c], sig[c]) for c in qd if c in sig)
            if overlap <= 0:
                continue
            shared = sorted(((self.name[c], round(sig[c], 2))
                             for c in qd if c in sig),
                            key=lambda x: -x[1])
            scored.append((sec, overlap, shared))
        scored.sort(key=lambda x: -x[1])
        out = []
        for sec, ov, shared in scored[:SECTION_RESULT_K]:
            m = self.sec_meta.get(sec, {})
            out.append({
                "section_id": sec, "ref": m.get("ref", sec),
                "book": m.get("book", ""), "tradition": m.get("tradition", ""),
                "text_name": m.get("text_name", ""),
                "score": round(ov, 4),
                "matched_concepts": [{"name": n, "weight": w} for n, w in shared],
                "signature": [{"name": self.name[c], "weight": round(w, 2)}
                              for c, w in sorted(self.sig[sec].items(),
                                                 key=lambda x: -x[1])],
            })
        return out

    def section_preview(self, section_id: str, cap=1200) -> str:
        row = self.con.execute(
            "SELECT text FROM sections WHERE section_id=?", (section_id,)).fetchone()
        if not row:
            return ""
        t = row[0]
        return t if len(t) <= cap else t[:cap] + " […]"

    # ---------- graph traversal ----------

    def node(self, node_id: str, kinds=None, limit=24):
        """Center node + neighbors. Concept nodes accept a concept_id OR a
        name (slugified); section nodes accept a section_id. Returns edges
        with per-kind metadata for click-to-recenter."""
        node_type = "section" if node_id in self.sec_meta else "concept"
        if node_type == "concept" and node_id not in self.name:
            slug = slugify(node_id)
            node_id = slug if slug in self.name else node_id
        center = self._node_info(node_type, node_id)

        kinds = kinds or (["conceptual", "structural"] if node_type == "section"
                          else ["co_occurrence", "co_variance"])
        ph = ",".join("?" * len(kinds))
        rows = self.con.execute(
            f"SELECT kind, src_type, src_id, dst_type, dst_id, weight, metadata "
            f"FROM edges WHERE run_id=? AND kind IN ({ph}) "
            f"AND (src_id=? OR dst_id=?) ORDER BY weight DESC LIMIT ?",
            (self.run_id, *kinds, node_id, node_id, limit)).fetchall()
        import json as _json
        neighbors = []
        for kind, st, s, dt, d, w, md in rows:
            other_type, other = (dt, d) if s == node_id else (st, s)
            neighbors.append({
                "node": self._node_info(other_type, other),
                "kind": kind, "weight": round(w, 4),
                "metadata": _json.loads(md) if md else None,
            })
        return {"run_id": self.run_id, "center": center,
                "kinds": kinds, "neighbors": neighbors}

    def _node_info(self, node_type, node_id):
        if node_type == "concept":
            return {"id": node_id, "type": "concept",
                    "label": self.name.get(node_id, node_id),
                    "definition": self.definition.get(node_id, ""),
                    "usage": self.usage.get(node_id, 0)}
        m = self.sec_meta.get(node_id, {})
        return {"id": node_id, "type": "section",
                "label": m.get("ref", node_id),
                "tradition": m.get("tradition", ""),
                "text_name": m.get("text_name", ""),
                "signature": [{"name": self.name[c], "weight": round(w, 2)}
                              for c, w in sorted(
                                  self.sig.get(node_id, {}).items(),
                                  key=lambda x: -x[1])]}

    def top_concepts(self, k=40):
        """Seed list for the graph view's starting point."""
        top = sorted(self.usage.items(), key=lambda x: -x[1])[:k]
        return [{"id": c, "label": self.name.get(c, c), "usage": n,
                 "definition": self.definition.get(c, "")}
                for c, n in top if c in self.name]
