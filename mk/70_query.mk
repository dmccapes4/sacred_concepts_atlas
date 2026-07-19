# =========================
# Query orchestration — probe -> retrieve -> gap -> retrieve -> report -> evidence map
# =========================
# Usage:
#   make query Q="How do the three traditions treat covenant?"
#   make query Q="..." SOURCES=quran_ar_tanzil,tanakh_he_uxlc

SOURCES ?=
CLOUD   ?=    # CLOUD=1 -> OpenAI agents (needs OPENAI_API_KEY in .env)
PROVIDER ?=   # PROVIDER=grok|openai -> cloud agents on that provider (overrides CLOUD)

.PHONY: query portal

query: ## Run a query (Q="...", optional SOURCES=a,b CLOUD=1 or PROVIDER=grok)
	@test -n "$(Q)" || { echo 'Usage: make query Q="your question"'; exit 1; }
	@$(PY) scripts/atlas_query.py --db $(DB) --model $(AGENT_MODEL) \
		--embed-model $(EMBED_MODEL) $(if $(SOURCES),--sources $(SOURCES)) \
		$(if $(PROVIDER),--cloud $(PROVIDER),$(if $(CLOUD),--cloud)) "$(Q)"

PORTAL_PORT ?= 8877
PORTAL_HOST ?= 127.0.0.1   # put a reverse proxy (sacred.dylanmccapes.systems) in front

portal: ## Serve the query portal (PORTAL_HOST/PORTAL_PORT; PORTAL_CLOUD=0 for local agents)
	@PORTAL_HOST=$(PORTAL_HOST) PORTAL_PORT=$(PORTAL_PORT) \
		$(PY) -m uvicorn portal.server:app --host $(PORTAL_HOST) --port $(PORTAL_PORT)
