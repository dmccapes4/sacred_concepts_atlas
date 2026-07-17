# =========================
# Bible — English — World English Bible 2020 (ebible.org) — source_id: bible_en_web
# =========================

BIBLE_WEB_ID   := bible_en_web
BIBLE_WEB_DIR  ?= $(RAW_DIR)/$(BIBLE_WEB_ID)
BIBLE_WEB_URL  ?= https://ebible.org/Scriptures/eng-web_usfm.zip
BIBLE_WEB_ZIP  := $(BIBLE_WEB_DIR)/eng-web_usfm.zip
# Human-readable alternate (reference only, not ingested)
BIBLE_WEB_PDF_URL ?= https://ebible.org/pdf/eng-web/eng-web_all.pdf

.PHONY: bible-web-fetch bible-web-verify bible-web-unpack bible-web-ingest bible-web-stats bible-web-all

bible-web-fetch: ## Download WEB Bible USFM archive
	@mkdir -p $(BIBLE_WEB_DIR)
	@[ -s $(BIBLE_WEB_ZIP) ] || $(WGET) -O $(BIBLE_WEB_ZIP) "$(BIBLE_WEB_URL)"
	@echo "OK: $(BIBLE_WEB_ZIP)"

bible-web-verify: ## Zip integrity + expected USFM count (66 canonical + apocrypha + front matter)
	@unzip -tq $(BIBLE_WEB_ZIP) >/dev/null && echo "zip OK"
	@n=$$(unzip -l $(BIBLE_WEB_ZIP) '*.usfm' | grep -c '\.usfm$$'); \
	echo "usfm files: $$n"; \
	[ "$$n" -ge 66 ] || { echo "ERROR: expected >= 66 USFM files"; exit 1; }

bible-web-unpack: ## Extract USFM books
	@unzip -oq $(BIBLE_WEB_ZIP) -d $(BIBLE_WEB_DIR)/usfm
	@echo "unpacked to $(BIBLE_WEB_DIR)/usfm"

bible-web-ingest: ## (Phase 1) Parse USFM -> sources/sections rows (66-book canon)
	@$(PY) scripts/ingest_bible_web.py --dir $(BIBLE_WEB_DIR)/usfm --db $(DB) --source-id $(BIBLE_WEB_ID)

bible-web-stats: ## Section counts for this source
	@$(SQL) "SELECT book, COUNT(*) FROM sections WHERE source_id='$(BIBLE_WEB_ID)' GROUP BY book ORDER BY MIN(seq);"

bible-web-all: bible-web-fetch bible-web-verify bible-web-unpack bible-web-ingest bible-web-stats
