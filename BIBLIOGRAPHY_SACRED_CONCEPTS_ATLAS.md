# BIBLIOGRAPHY — Sacred Concepts Atlas

**Status:** Wave 1 (primary language, newest freely licensed version per tradition)
**Date:** 2026-07-16

This file is the **source of truth** for what the database must contain. The
validation script (`scripts/validate_bibliography.py`) parses the table below and
checks it against `data/raw/` and the database. Rules for editing:

- Do not remove or reorder columns.
- `id` is the canonical source key: `{text}_{language}_{version}` (lowercase,
  snake_case). It names the `data/raw/<id>/` directory, the row in the `sources`
  table, and the per-source Make targets.
- Every `primary_url` must be retrievable non-interactively (wget/curl, no login,
  no form). Verify before adding.
- `status` is one of: `verified` (URL checked, returns the artifact),
  `candidate` (found but not yet verified), `unreachable` (was verified once,
  currently failing).

## Wave 1 — Primary texts

| id | text | tradition | language | version | format | primary_url | status |
|----|------|-----------|----------|---------|--------|-------------|--------|
| tanakh_he_uxlc | Tanakh | Judaism | Hebrew | UXLC (Unicode/XML Leningrad Codex, fork of WLC 4.20, updated semi-annually) | XML (zip) | https://tanach.us/Books/Tanach.xml.zip | verified |
| bible_en_web | Bible | Christianity | English | World English Bible, 2020 stable text (public domain) | USFM (zip) | https://ebible.org/Scriptures/eng-web_usfm.zip | verified |
| quran_ar_tanzil | Quran | Islam | Arabic | Tanzil Uthmani v1.1 (2021, CC-BY 3.0) | UTF-8 text | https://tanzil.net/pub/download/index.php?quranType=uthmani&outType=txt&agree=true | verified |

## Companion artifacts (fetched alongside, not sections themselves)

| id | belongs_to | purpose | format | url | status |
|----|-----------|---------|--------|-----|--------|
| quran_metadata | quran_ar_tanzil | Surah/juz/hizb/rukūʿ boundaries — drives sectioning of long surahs | XML | https://tanzil.net/res/text/metadata/quran-data.xml | verified |

## Human-readable PDF alternates (reference only, not ingested)

| belongs_to | url | status |
|-----------|-----|--------|
| tanakh_he_uxlc | https://tanach.us/PDFFiles/Tanach.pdf.zip | verified |
| bible_en_web | https://ebible.org/pdf/eng-web/eng-web_all.pdf | verified |
| quran_ar_tanzil | (King Fahd Complex mushaf PDF — qurancomplex.gov.sa unreachable at verification time) | unreachable |

## Notes on selection

- **Tanakh (Hebrew).** UXLC at tanach.us is the actively maintained fork of the
  Westminster Leningrad Codex, provided without licensing restrictions, with
  version-controlled text and per-book XML including chapter/verse markup. This
  is the best machine-readable Masoretic text available for free.
- **Bible (English).** The World English Bible is the newest complete
  modern-English translation in the public domain (2020 stable text). USFM markup
  gives us book/chapter/verse structure directly. Copyrighted modern versions
  (NIV, ESV, NRSVue) are not freely redistributable and are excluded from Wave 1.
  The original-language future versions (Hebrew OT = already covered by UXLC;
  Greek NT, e.g. SBLGNT) slot in as new rows later.
- **Quran (Arabic).** Tanzil is the de facto standard machine-readable Quran text
  (CC-BY). Uthmani script matches the Medina Mushaf. The companion
  `quran-data.xml` provides rukūʿ and hizb boundaries used to split long surahs
  into chapter-length sections.

## Future waves (structure is ready, rows to be added when verified)

Examples of rows Wave 2+ would add: `bible_el_sblgnt` (Greek NT),
`tanakh_en_jps1917` (public-domain English Tanakh), `quran_en_saheeh` or a Tanzil
translation, `bible_la_vulgate`, etc. Adding a language/version is: add a row
here, add an `mk/NN_*.mk` fetch/ingest include (or reuse the existing parser if
the format matches), run `make fetch-all validate`.
