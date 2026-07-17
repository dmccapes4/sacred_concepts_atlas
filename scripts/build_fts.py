#!/usr/bin/env python3
"""Rebuild the FTS5 (BM25) index over pages (the hybrid-RAG retrieval unit)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from atlas_lib import db_connect, strip_marks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    args = ap.parse_args()

    con = db_connect(args.db)
    con.execute("DELETE FROM pages_fts")
    # Index mark-stripped text (bare Hebrew/Arabic) so unpointed term queries
    # match pointed scripture; queries are normalized the same way.
    rows = [(pid, sec, book, strip_marks(text)) for pid, sec, book, text in con.execute(
        "SELECT p.page_id, p.section_id, s.book, p.text "
        "FROM pages p JOIN sections s ON s.section_id = p.section_id")]
    con.executemany("INSERT INTO pages_fts (page_id, section_id, book, text) "
                    "VALUES (?,?,?,?)", rows)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0]
    print(f"pages_fts rebuilt: {n} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
