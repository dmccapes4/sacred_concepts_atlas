#!/usr/bin/env python3
"""Ingest Tanzil Uthmani Quran into sources/sections.

Sectioning (strategy doc section 4.Q2): a surah with <= MAX_AYAT ayat is one
section; longer surahs are split on ruku boundaries (from quran-data.xml),
grouping consecutive rukus so each section lands in roughly TARGET_MIN..TARGET_MAX ayat.
"""

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from atlas_lib import db_connect, replace_sections, slugify, upsert_source

MAX_AYAT = 40      # surah at or under this size stays whole
TARGET_MAX = 40    # grow a ruku group until adding the next would exceed this


def load_ayat(text_path: Path) -> dict[int, list[str]]:
    """Tanzil plain text: ayat in order, surah/aya structure from counts."""
    lines = [ln for ln in text_path.read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    return lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True, type=Path)
    ap.add_argument("--meta", required=True, type=Path)
    ap.add_argument("--db", required=True)
    ap.add_argument("--source-id", default="quran_ar_tanzil")
    args = ap.parse_args()

    meta = ET.parse(args.meta).getroot()
    suras = [{"index": int(s.get("index")), "ayas": int(s.get("ayas")),
              "start": int(s.get("start")), "tname": s.get("tname"),
              "name": s.get("name"), "type": s.get("type")}
             for s in meta.iter("sura")]
    rukus_by_sura: dict[int, list[int]] = {}
    for r in meta.iter("ruku"):
        rukus_by_sura.setdefault(int(r.get("sura")), []).append(int(r.get("aya")))

    lines = load_ayat(args.text)
    assert len(lines) == 6236, f"expected 6236 ayat, got {len(lines)}"

    con = db_connect(args.db)
    rows, seq = [], 0
    for s in suras:
        idx, n_ayas, start = s["index"], s["ayas"], s["start"]
        ayat = lines[start:start + n_ayas]
        book = f"{idx:03d} {s['tname']}"
        base_meta = {"sura": idx, "arabic_name": s["name"], "revelation": s["type"]}

        if n_ayas <= MAX_AYAT:
            groups = [(1, n_ayas)]
        else:
            bounds = sorted(rukus_by_sura.get(idx, [1]))
            spans = [(a, (bounds[i + 1] - 1) if i + 1 < len(bounds) else n_ayas)
                     for i, a in enumerate(bounds)]
            groups, cur = [], None
            for a, b in spans:
                if cur is None:
                    cur = [a, b]
                elif (b - cur[0] + 1) <= TARGET_MAX:
                    cur[1] = b
                else:
                    groups.append(tuple(cur))
                    cur = [a, b]
            if cur:
                groups.append(tuple(cur))

        for gi, (a, b) in enumerate(groups, start=1):
            text = "\n".join(ayat[a - 1:b])
            seq += 1
            rows.append((
                f"{args.source_id}:s{idx:03d}:{gi}",
                book, f"{s['tname']} {idx}:{a}-{b}", seq, text,
                json.dumps({**base_meta, "ayat": f"{a}-{b}", "n_ayat": b - a + 1}),
            ))

    upsert_source(con, args.source_id, "Quran", "Islam", "ar",
                  "Tanzil Uthmani v1.1",
                  "https://tanzil.net/pub/download/index.php?quranType=uthmani&outType=txt&agree=true")
    replace_sections(con, args.source_id, rows)
    con.commit()
    print(f"{args.source_id}: {seq} sections from {len(suras)} suras")
    return 0


if __name__ == "__main__":
    sys.exit(main())
