# =========================
# Query orchestration — probe -> retrieve -> gap -> retrieve -> report -> evidence map
# =========================
# Usage:
#   make query Q="How do the three traditions treat covenant?"
#   make query Q="..." SOURCES=quran_ar_tanzil,tanakh_he_uxlc

SOURCES ?=
CLOUD   ?=    # CLOUD=1 -> OpenAI agents (needs OPENAI_API_KEY in .env)

.PHONY: query

query: ## Run a query (Q="...", optional SOURCES=a,b CLOUD=1)
	@test -n "$(Q)" || { echo 'Usage: make query Q="your question"'; exit 1; }
	@$(PY) scripts/atlas_query.py --db $(DB) --model $(AGENT_MODEL) \
		--embed-model $(EMBED_MODEL) $(if $(SOURCES),--sources $(SOURCES)) \
		$(if $(CLOUD),--cloud) "$(Q)"
