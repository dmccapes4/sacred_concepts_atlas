# =========================
# Quran — Arabic — Tanzil Uthmani v1.1 — source_id: quran_ar_tanzil
# =========================

QURAN_ID    := quran_ar_tanzil
QURAN_DIR   ?= $(RAW_DIR)/$(QURAN_ID)
# Tanzil's download endpoint takes query params; quote carefully.
QURAN_URL   ?= https://tanzil.net/pub/download/index.php?quranType=uthmani&outType=txt&agree=true
QURAN_TXT   := $(QURAN_DIR)/quran-uthmani.txt
# Companion metadata: surah/juz/hizb/ruku boundaries (drives sectioning of long surahs)
QURAN_META_URL ?= https://tanzil.net/res/text/metadata/quran-data.xml
QURAN_META  := $(QURAN_DIR)/quran-data.xml

.PHONY: quran-fetch quran-verify quran-ingest quran-stats quran-all

quran-fetch: ## Download Tanzil Uthmani text + metadata XML
	@mkdir -p $(QURAN_DIR)
	@[ -s $(QURAN_TXT) ]  || $(WGET) -O $(QURAN_TXT)  "$(QURAN_URL)"
	@[ -s $(QURAN_META) ] || $(WGET) -O $(QURAN_META) "$(QURAN_META_URL)"
	@echo "OK: $(QURAN_TXT) + $(QURAN_META)"

quran-verify: ## 6236 ayat + metadata well-formed
	@n=$$(grep -cv -e '^\s*$$' -e '^#' $(QURAN_TXT)); \
	echo "ayat lines: $$n"; \
	[ "$$n" -eq 6236 ] || { echo "ERROR: expected 6236 ayat, got $$n"; exit 1; }
	@grep -q '<suras' $(QURAN_META) && echo "metadata OK" || { echo "ERROR: bad quran-data.xml"; exit 1; }

quran-ingest: ## (Phase 1) Sectionize (surah / ruku-groups) -> sources/sections rows
	@$(PY) scripts/ingest_quran.py --text $(QURAN_TXT) --meta $(QURAN_META) --db $(DB) --source-id $(QURAN_ID)

quran-stats: ## Section counts for this source
	@$(SQL) "SELECT book, COUNT(*) FROM sections WHERE source_id='$(QURAN_ID)' GROUP BY book ORDER BY MIN(seq) LIMIT 20;"

quran-all: quran-fetch quran-verify quran-ingest quran-stats
