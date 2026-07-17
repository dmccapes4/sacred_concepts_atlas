#!/usr/bin/env python3
"""Ingest UXLC Tanakh XML (tanach.us) into sources/sections. One section per chapter.

Uses qere (q) over ketiv (k) for readable text. Skips .DH variants and header/index files.
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from atlas_lib import db_connect, replace_sections, slugify, upsert_source

# Canonical Tanakh order (UXLC file names)
BOOK_ORDER = [
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy",
    "Joshua", "Judges", "Samuel_1", "Samuel_2", "Kings_1", "Kings_2",
    "Isaiah", "Jeremiah", "Ezekiel",
    "Hosea", "Joel", "Amos", "Obadiah", "Jonah", "Micah", "Nahum",
    "Habakkuk", "Zephaniah", "Haggai", "Zechariah", "Malachi",
    "Psalms", "Proverbs", "Job",
    "Song_of_Songs", "Ruth", "Lamentations", "Ecclesiastes", "Esther",
    "Daniel", "Ezra", "Nehemiah", "Chronicles_1", "Chronicles_2",
]


def verse_text(v: ET.Element) -> str:
    words = []
    for el in v:
        if el.tag == "w" and el.text:
            words.append("".join(el.itertext()).strip())
        elif el.tag == "q" and el.text:  # qere preferred over ketiv
            words.append("".join(el.itertext()).strip())
    return " ".join(w for w in words if w)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, type=Path, help="dir containing Books/")
    ap.add_argument("--db", required=True)
    ap.add_argument("--source-id", default="tanakh_he_uxlc")
    args = ap.parse_args()

    books_dir = args.dir / "Books" if (args.dir / "Books").is_dir() else args.dir
    con = db_connect(args.db)
    rows, seq = [], 0
    for book in BOOK_ORDER:
        path = books_dir / f"{book}.xml"
        root = ET.parse(path).getroot()
        book_name = book.replace("_", " ")
        for c in root.iter("c"):
            cn = c.get("n")
            verses = [verse_text(v) for v in c.findall("v")]
            text = "\n".join(t for t in verses if t)
            seq += 1
            rows.append((
                f"{args.source_id}:{slugify(book_name)}:{cn}",
                book_name, f"{book_name} {cn}", seq, text,
                json.dumps({"n_verses": len(verses)}),
            ))

    upsert_source(con, args.source_id, "Tanakh", "Judaism", "he",
                  "UXLC (fork of WLC 4.20)", "https://tanach.us/Books/Tanach.xml.zip")
    replace_sections(con, args.source_id, rows)
    con.commit()
    print(f"{args.source_id}: {seq} sections from {len(BOOK_ORDER)} books")
    return 0


if __name__ == "__main__":
    sys.exit(main())
