# =========================
# Tanakh — Hebrew — UXLC (tanach.us) — source_id: tanakh_he_uxlc
# =========================

TANAKH_ID   := tanakh_he_uxlc
TANAKH_DIR  ?= $(RAW_DIR)/$(TANAKH_ID)
TANAKH_URL  ?= https://tanach.us/Books/Tanach.xml.zip
TANAKH_ZIP  := $(TANAKH_DIR)/Tanach.xml.zip
# Human-readable alternate (reference only, not ingested)
TANAKH_PDF_URL ?= https://tanach.us/PDFFiles/Tanach.pdf.zip

.PHONY: tanakh-fetch tanakh-verify tanakh-unpack tanakh-ingest tanakh-stats tanakh-all

tanakh-fetch: ## Download UXLC Tanakh XML archive
	@mkdir -p $(TANAKH_DIR)
	@[ -s $(TANAKH_ZIP) ] || $(WGET) -O $(TANAKH_ZIP) "$(TANAKH_URL)"
	@echo "OK: $(TANAKH_ZIP)"

tanakh-verify: ## Zip integrity + expected book count (39 books + DH variants + header/index)
	@unzip -tq $(TANAKH_ZIP) >/dev/null && echo "zip OK"
	@n=$$(unzip -l $(TANAKH_ZIP) '*.xml' | grep -c '\.xml$$'); \
	echo "xml files: $$n"; \
	[ "$$n" -ge 39 ] || { echo "ERROR: expected >= 39 book XMLs"; exit 1; }

tanakh-unpack: ## Extract XML books
	@unzip -oq $(TANAKH_ZIP) -d $(TANAKH_DIR)/books
	@echo "unpacked to $(TANAKH_DIR)/books"

tanakh-ingest: ## (Phase 1) Parse UXLC XML -> sources/sections rows
	@$(PY) scripts/ingest_tanakh.py --dir $(TANAKH_DIR)/books --db $(DB) --source-id $(TANAKH_ID)

tanakh-stats: ## Section counts for this source
	@$(SQL) "SELECT book, COUNT(*) FROM sections WHERE source_id='$(TANAKH_ID)' GROUP BY book ORDER BY MIN(seq);"

tanakh-all: tanakh-fetch tanakh-verify tanakh-unpack tanakh-ingest tanakh-stats
