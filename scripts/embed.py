#!/usr/bin/env python3
"""Embed pages or concept definitions via Ollama into the database.

Pages are the retrieval unit (~1200 chars, verse-aligned), so nothing here is
truncated: every page fits comfortably in bge-m3's window. Sections are NOT
embedded; section-level retrieval aggregates page hits (max page similarity).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from atlas_lib import db_connect, embed_texts, vec_to_blob

BATCH = 16


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--target", choices=["pages", "concepts"], required=True)
    ap.add_argument("--model", default="bge-m3")
    args = ap.parse_args()

    con = db_connect(args.db)
    if args.target == "pages":
        todo = con.execute(
            "SELECT p.page_id, p.text FROM pages p "
            "LEFT JOIN page_embeddings e ON e.page_id = p.page_id AND e.model = ? "
            "WHERE e.page_id IS NULL ORDER BY p.source_id, p.section_id, p.page_no",
            (args.model,)).fetchall()
    else:
        todo = con.execute(
            "SELECT concept_id, name || ': ' || definition FROM concepts "
            "WHERE embedding IS NULL AND status='active'").fetchall()

    print(f"{args.target}: {len(todo)} to embed with {args.model}", flush=True)
    done = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        vecs = embed_texts([t for _, t in batch], args.model)
        if args.target == "pages":
            con.executemany(
                "INSERT OR REPLACE INTO page_embeddings (page_id, model, dim, embedding) "
                "VALUES (?,?,?,?)",
                [(pid, args.model, len(v), vec_to_blob(v)) for (pid, _), v in zip(batch, vecs)])
        else:
            con.executemany(
                "UPDATE concepts SET embedding=?, embed_model=? WHERE concept_id=?",
                [(vec_to_blob(v), args.model, cid) for (cid, _), v in zip(batch, vecs)])
        con.commit()
        done += len(batch)
        if done % 480 == 0 or done == len(todo):
            print(f"  {done}/{len(todo)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
