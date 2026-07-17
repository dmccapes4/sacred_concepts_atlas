# =========================
# Pages + retrieval indexes (Phase 2) — hybrid RAG layer
# =========================
# Pages (verse-aligned ~1200-char chunks) are the retrieval unit.
# Sections keep the concept signatures; pages inherit via page_concepts view.

.PHONY: pages-build fts-build embed-pages embed-concepts index-all

pages-build: ## Chunk sections into verse-aligned pages
	@$(PY) scripts/build_pages.py --db $(DB)

fts-build: ## Build/rebuild FTS5 (BM25) index over pages
	@$(PY) scripts/build_fts.py --db $(DB)

embed-pages: ## Embed all pages via Ollama ($(EMBED_MODEL))
	@$(PY) scripts/embed.py --db $(DB) --target pages --model $(EMBED_MODEL)

embed-concepts: ## Embed concept definitions via Ollama ($(EMBED_MODEL))
	@$(PY) scripts/embed.py --db $(DB) --target concepts --model $(EMBED_MODEL)

index-all: pages-build fts-build embed-pages
