#!/usr/bin/env python3
"""Build pages: verse-aligned chunks of sections, the hybrid-RAG retrieval unit.

Section text stores one verse per line (all three parsers). Pages accumulate
whole verses greedily up to TARGET_CHARS; a verse is never split. Refs are
computed from the section's verse numbering (chapters start at verse 1; Quran
sections carry their ayah range in metadata).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from atlas_lib import db_connect

TARGET_CHARS = 1200   # soft cap per page; always at least one verse


def page_ranges(verses: list[str]) -> list[tuple[int, int]]:
    """Return (first_idx, last_idx) 0-based inclusive verse ranges per page."""
    ranges, start, size = [], 0, 0
    for i, v in enumerate(verses):
        if size and size + len(v) > TARGET_CHARS:
            ranges.append((start, i - 1))
            start, size = i, 0
        size += len(v)
    ranges.append((start, len(verses) - 1))
    return ranges


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    args = ap.parse_args()

    con = db_connect(args.db)
    sections = con.execute(
        "SELECT section_id, source_id, book, ref, text, metadata FROM sections").fetchall()
    con.execute("DELETE FROM pages")

    rows = []
    for section_id, source_id, book, ref, text, meta_json in sections:
        verses = [v for v in text.split("\n") if v.strip()]
        meta = json.loads(meta_json or "{}")
        # first verse number of this section (Quran sections may start mid-surah)
        v0 = int(meta["ayat"].split("-")[0]) if "ayat" in meta else 1
        ref_base = ref.rsplit(":", 1)[0] if "ayat" in meta else ref
        for pno, (a, b) in enumerate(page_ranges(verses), start=1):
            page_text = "\n".join(verses[a:b + 1])
            page_ref = f"{ref_base}:{v0 + a}-{v0 + b}"
            rows.append((f"{section_id}:p{pno:02d}", section_id, source_id, pno,
                         page_ref, page_text,
                         json.dumps({"first_verse": v0 + a, "last_verse": v0 + b,
                                     "n_verses": b - a + 1})))

    con.executemany(
        "INSERT INTO pages (page_id, section_id, source_id, page_no, ref, text, metadata) "
        "VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    n_multi = con.execute(
        "SELECT COUNT(*) FROM (SELECT section_id FROM pages GROUP BY section_id HAVING COUNT(*)>1)"
    ).fetchone()[0]
    print(f"pages: {len(rows)} built over {len(sections)} sections ({n_multi} sections span >1 page)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
