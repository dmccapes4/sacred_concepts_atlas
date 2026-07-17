#!/usr/bin/env python3
"""Validate the Sacred Concepts Atlas pipeline against the bibliography.

The bibliography markdown table is the source of truth. Checks, in order:
  1. bibliography parses; ids unique and snake_case
  2. disk: every `verified` source has a non-empty artifact in data/raw/<id>/
  3. db (if present): every source has a `sources` row and >0 sections,
     section counts within expected ranges, concept weights sum to 1.0 per run

Exit code is non-zero on any failure so it can gate `make all`.
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path

# Expected section-count ranges per source (Phase 1 exit criteria).
# Tanakh: 929 chapters; WEB 66-book canon: 1,189 chapters;
# Quran: 114 surahs sectionized to roughly 250-320 units.
EXPECTED_SECTIONS = {
    "tanakh_he_uxlc": (920, 940),
    "bible_en_web": (1180, 1200),
    "quran_ar_tanzil": (114, 340),
}

ID_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")


def fail(errors: list, msg: str) -> None:
    errors.append(msg)
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def parse_bibliography(bib_path: Path) -> list[dict]:
    """Extract rows from markdown tables whose header has `id` and `primary_url` columns."""
    lines = bib_path.read_text(encoding="utf-8").splitlines()
    rows, header = [], None
    for line in lines:
        if not line.strip().startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            if cells and cells[0] == "id" and "primary_url" in cells:
                header = cells
            continue
        if set(line) <= {"|", "-", " ", ":"}:
            continue
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells)))
    return rows


def check_bibliography(rows: list[dict], errors: list) -> None:
    print("== 1. bibliography")
    if not rows:
        fail(errors, "no rows parsed from bibliography table")
        return
    ids = [r["id"] for r in rows]
    if len(ids) != len(set(ids)):
        fail(errors, f"duplicate ids: {sorted({i for i in ids if ids.count(i) > 1})}")
    for r in rows:
        if not ID_RE.match(r["id"]):
            fail(errors, f"id not snake_case: {r['id']!r}")
        if r["status"] not in ("verified", "candidate", "unreachable"):
            fail(errors, f"{r['id']}: bad status {r['status']!r}")
        if not r["primary_url"].startswith("http"):
            fail(errors, f"{r['id']}: primary_url is not a URL")
    ok(f"{len(rows)} source rows parsed: {', '.join(ids)}")


def check_disk(rows: list[dict], raw_dir: Path, errors: list) -> None:
    print("== 2. disk vs bibliography")
    expected_dirs = set()
    for r in rows:
        if r["status"] != "verified":
            print(f"  skip  {r['id']} (status={r['status']})")
            continue
        expected_dirs.add(r["id"])
        src_dir = raw_dir / r["id"]
        if not src_dir.is_dir():
            fail(errors, f"{r['id']}: missing {src_dir} (run `make fetch-all`)")
            continue
        artifacts = [p for p in src_dir.rglob("*") if p.is_file() and p.stat().st_size > 0]
        if not artifacts:
            fail(errors, f"{r['id']}: {src_dir} has no non-empty artifacts")
        else:
            ok(f"{r['id']}: {len(artifacts)} artifact(s), "
               f"largest {max(p.stat().st_size for p in artifacts):,} bytes")
    if raw_dir.is_dir():
        unexpected = {p.name for p in raw_dir.iterdir() if p.is_dir()} - expected_dirs
        if unexpected:
            fail(errors, f"directories in {raw_dir} not in bibliography: {sorted(unexpected)}")


def check_db(rows: list[dict], db_path: Path, errors: list) -> None:
    print("== 3. database vs bibliography")
    if not db_path.exists():
        print(f"  skip  {db_path} does not exist yet (Phase 1)")
        return
    con = sqlite3.connect(db_path)
    try:
        tables = {t for (t,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        if "sources" not in tables or "sections" not in tables:
            fail(errors, "db exists but sources/sections tables missing")
            return
        for r in rows:
            if r["status"] != "verified":
                continue
            sid = r["id"]
            n_src = con.execute(
                "SELECT COUNT(*) FROM sources WHERE source_id=?", (sid,)).fetchone()[0]
            if n_src != 1:
                fail(errors, f"{sid}: expected 1 sources row, found {n_src}")
                continue
            n_sec = con.execute(
                "SELECT COUNT(*) FROM sections WHERE source_id=?", (sid,)).fetchone()[0]
            lo, hi = EXPECTED_SECTIONS.get(sid, (1, 10**9))
            if not (lo <= n_sec <= hi):
                fail(errors, f"{sid}: {n_sec} sections, expected {lo}-{hi}")
            else:
                ok(f"{sid}: {n_sec} sections (expected {lo}-{hi})")
            n_empty = con.execute(
                "SELECT COUNT(*) FROM sections WHERE source_id=? "
                "AND (text IS NULL OR TRIM(text)='')", (sid,)).fetchone()[0]
            if n_empty:
                fail(errors, f"{sid}: {n_empty} sections with empty text")
        extra = con.execute(
            "SELECT source_id, COUNT(*) FROM sections WHERE source_id NOT IN "
            f"({','.join('?' * len(rows))}) GROUP BY 1",
            [r["id"] for r in rows]).fetchall()
        for sid, n in extra:
            fail(errors, f"db has {n} sections for {sid!r} which is not in the bibliography")

        if "pages" in tables:
            n_pages = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            if n_pages:
                unpaged = con.execute(
                    "SELECT COUNT(*) FROM sections s LEFT JOIN pages p "
                    "ON p.section_id = s.section_id WHERE p.page_id IS NULL").fetchone()[0]
                if unpaged:
                    fail(errors, f"{unpaged} sections have no pages (rerun `make pages-build`)")
                else:
                    ok(f"pages: {n_pages}, every section covered")

        if "section_concepts" in tables:
            print("== 4. concept weights")
            bad = con.execute(
                "SELECT section_id, run_id, ROUND(SUM(weight),3) FROM section_concepts "
                "GROUP BY section_id, run_id HAVING ABS(SUM(weight)-1.0) > 0.01 LIMIT 5"
            ).fetchall()
            if bad:
                fail(errors, f"weight sums != 1.0 (first 5): {bad}")
            else:
                ok("all (section, run) weight sums within 1.0 +/- 0.01")
            orphans = con.execute(
                "SELECT COUNT(*) FROM section_concepts sc "
                "LEFT JOIN concepts c ON c.concept_id = sc.concept_id "
                "WHERE c.concept_id IS NULL").fetchone()[0]
            if orphans:
                fail(errors, f"{orphans} section_concepts rows reference missing concepts")
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bib", default="BIBLIOGRAPHY_SACRED_CONCEPTS_ATLAS.md", type=Path)
    ap.add_argument("--raw-dir", default="data/raw", type=Path)
    ap.add_argument("--db", default="db/atlas.db", type=Path)
    ap.add_argument("--skip-db", action="store_true")
    args = ap.parse_args()

    if not args.bib.exists():
        print(f"FATAL: bibliography not found: {args.bib}")
        return 2

    errors: list[str] = []
    rows = parse_bibliography(args.bib)
    check_bibliography(rows, errors)
    check_disk(rows, args.raw_dir, errors)
    if args.skip_db:
        print("== 3. database (skipped by --skip-db)")
    else:
        check_db(rows, args.db, errors)

    print()
    if errors:
        print(f"VALIDATION FAILED: {len(errors)} error(s)")
        return 1
    print("VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
