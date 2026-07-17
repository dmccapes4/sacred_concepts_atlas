#!/usr/bin/env python3
"""Ingest World English Bible USFM (ebible.org) into sources/sections.

One section per chapter, 66-book Protestant canon (matches briefing scope;
apocrypha/front-matter files in the archive are skipped).
Strips USFM markup: footnotes, cross-refs, word-level \\w ...|strong="..."\\w* wrappers.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from atlas_lib import db_connect, replace_sections, slugify, upsert_source

# USFM book code -> display name, in canonical order (66 books)
CANON = [
    ("GEN", "Genesis"), ("EXO", "Exodus"), ("LEV", "Leviticus"), ("NUM", "Numbers"),
    ("DEU", "Deuteronomy"), ("JOS", "Joshua"), ("JDG", "Judges"), ("RUT", "Ruth"),
    ("1SA", "1 Samuel"), ("2SA", "2 Samuel"), ("1KI", "1 Kings"), ("2KI", "2 Kings"),
    ("1CH", "1 Chronicles"), ("2CH", "2 Chronicles"), ("EZR", "Ezra"), ("NEH", "Nehemiah"),
    ("EST", "Esther"), ("JOB", "Job"), ("PSA", "Psalms"), ("PRO", "Proverbs"),
    ("ECC", "Ecclesiastes"), ("SNG", "Song of Songs"), ("ISA", "Isaiah"), ("JER", "Jeremiah"),
    ("LAM", "Lamentations"), ("EZK", "Ezekiel"), ("DAN", "Daniel"), ("HOS", "Hosea"),
    ("JOL", "Joel"), ("AMO", "Amos"), ("OBA", "Obadiah"), ("JON", "Jonah"),
    ("MIC", "Micah"), ("NAM", "Nahum"), ("HAB", "Habakkuk"), ("ZEP", "Zephaniah"),
    ("HAG", "Haggai"), ("ZEC", "Zechariah"), ("MAL", "Malachi"),
    ("MAT", "Matthew"), ("MRK", "Mark"), ("LUK", "Luke"), ("JHN", "John"),
    ("ACT", "Acts"), ("ROM", "Romans"), ("1CO", "1 Corinthians"), ("2CO", "2 Corinthians"),
    ("GAL", "Galatians"), ("EPH", "Ephesians"), ("PHP", "Philippians"), ("COL", "Colossians"),
    ("1TH", "1 Thessalonians"), ("2TH", "2 Thessalonians"), ("1TI", "1 Timothy"),
    ("2TI", "2 Timothy"), ("TIT", "Titus"), ("PHM", "Philemon"), ("HEB", "Hebrews"),
    ("JAS", "James"), ("1PE", "1 Peter"), ("2PE", "2 Peter"), ("1JN", "1 John"),
    ("2JN", "2 John"), ("3JN", "3 John"), ("JUD", "Jude"), ("REV", "Revelation"),
]

FOOTNOTE_RE = re.compile(r"\\f\s.*?\\f\*|\\x\s.*?\\x\*", re.S)
W_OPEN_RE = re.compile(r"\\\+?w\s+([^|\\]*)(?:\|[^\\]*)?\\\+?w\*")
MARKER_RE = re.compile(r"\\[a-z0-9\+]+\*?\s?")


def clean_usfm(s: str) -> str:
    s = FOOTNOTE_RE.sub("", s)
    s = W_OPEN_RE.sub(r"\1", s)
    s = MARKER_RE.sub(" ", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def parse_book(path: Path) -> dict[str, list[str]]:
    """Return {chapter_number: [verse_texts]}."""
    chapters: dict[str, list[str]] = {}
    chap = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("\\c "):
            chap = line.split()[1].strip()
            chapters[chap] = []
        elif line.startswith("\\v ") and chap is not None:
            m = re.match(r"\\v\s+(\S+)\s*(.*)", line)
            if m:
                chapters[chap].append(clean_usfm(m.group(2)))
        elif chap is not None and line.startswith(("\\p", "\\q", "\\m", "\\li")):
            # continuation content on poetry/paragraph lines
            rest = clean_usfm(line)
            if rest and chapters[chap]:
                chapters[chap][-1] += " " + rest
    return chapters


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--db", required=True)
    ap.add_argument("--source-id", default="bible_en_web")
    args = ap.parse_args()

    files = {}
    for p in args.dir.glob("*.usfm"):
        m = re.match(r"\d+-([A-Z0-9]{3})eng-web\.usfm", p.name)
        if m:
            files[m.group(1)] = p

    con = db_connect(args.db)
    rows, seq = [], 0
    for code, book_name in CANON:
        chapters = parse_book(files[code])
        for cn, verses in chapters.items():
            text = "\n".join(v for v in verses if v)
            seq += 1
            rows.append((
                f"{args.source_id}:{slugify(book_name)}:{cn}",
                book_name, f"{book_name} {cn}", seq, text,
                json.dumps({"n_verses": len(verses), "usfm_code": code}),
            ))

    upsert_source(con, args.source_id, "Bible", "Christianity", "en",
                  "World English Bible 2020 stable text",
                  "https://ebible.org/Scriptures/eng-web_usfm.zip")
    replace_sections(con, args.source_id, rows)
    con.commit()
    print(f"{args.source_id}: {seq} sections from {len(CANON)} books")
    return 0


if __name__ == "__main__":
    sys.exit(main())
