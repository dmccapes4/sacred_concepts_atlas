# =========================
# Concept-extraction agents (Phase 3) — the point of the project
# =========================
# Local agents (Ollama + modelfiles/atlas-conceptor.Modelfile) walk sections,
# propose weighted concepts (sum = 1.0), and grow the registry under a rising
# novelty threshold tau(n) = TAU_MAX - (TAU_MAX - TAU_0) * K / (K + n).

# Threshold knobs (recorded in runs.params)
TAU_0    ?= 0.55
TAU_MAX  ?= 0.92
TAU_K    ?= 150
LIMIT    ?= 0        # 0 = all remaining sections
ORDER    ?= interleaved   # interleaved (fairness prior) | temporal (Tanakh->Bible->Quran lineage prior)
CLOUD    ?=            # CLOUD=1 -> OpenAI agents (OPENAI_API_KEY in .env)
ROUTER_MODEL ?=        # optional split: ROUTER_MODEL=atlas-router AGENT_MODEL=atlas-classifier (4090)

.PHONY: agent-modelfile agent-modelfiles agent-run agent-resume concepts-export concepts-stats concept-space-export

agent-modelfile: ## Build the single-model Ollama agent (3060 config)
	@ollama create $(AGENT_MODEL) -f modelfiles/atlas-conceptor.Modelfile

agent-modelfiles: ## Build the dual-model pair (4090 config: atlas-router + atlas-classifier)
	@ollama create atlas-router -f modelfiles/atlas-router.Modelfile
	@ollama create atlas-classifier -f modelfiles/atlas-classifier.Modelfile

agent-run: ## Full concept-extraction pass (new run_id; CLOUD=1 for OpenAI)
	@$(PY) scripts/agent_conceptor.py --db $(DB) --model $(AGENT_MODEL) \
		--embed-model $(EMBED_MODEL) \
		--tau0 $(TAU_0) --tau-max $(TAU_MAX) --tau-k $(TAU_K) \
		--order $(ORDER) --limit $(LIMIT) $(if $(CLOUD),--cloud) \
		$(if $(ROUTER_MODEL),--router-model $(ROUTER_MODEL))

agent-resume: ## Resume most recent unfinished run (CLOUD=1 switches agents to OpenAI)
	@$(PY) scripts/agent_conceptor.py --db $(DB) --model $(AGENT_MODEL) \
		--embed-model $(EMBED_MODEL) --resume $(if $(CLOUD),--cloud) \
		$(if $(ROUTER_MODEL),--router-model $(ROUTER_MODEL))
concepts-export: ## Export concept registry to JSON (browsable hash view)
	@$(SQL) --json "SELECT concept_id, name, definition, created_by, status FROM concepts ORDER BY created_at;" > concepts.json
	@echo "wrote concepts.json"

concepts-stats: ## Registry size, growth, weight sanity
	@$(SQL) "SELECT COUNT(*) AS concepts FROM concepts WHERE status='active';"
	@$(SQL) "SELECT run_id, COUNT(DISTINCT section_id) sections_done FROM section_concepts GROUP BY run_id;"
	@$(SQL) "SELECT section_id, run_id, ROUND(SUM(weight),3) s FROM section_concepts GROUP BY section_id, run_id HAVING ABS(s-1.0) > 0.01 LIMIT 10;"

concept-space-export: ## Snapshot concept space → HTML+JSON (artifacts/ + reviews/)
	@$(PY) scripts/export_concept_space.py --db $(DB) --out-dir artifacts
	@run=$$($(SQL) "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1;"); \
	  mkdir -p reviews; \
	  cp -f artifacts/concept_space_$${run}_latest.html \
	        reviews/concept_space_$${run}_latest.html; \
	  cp -f artifacts/concept_space_$${run}_latest.json \
	        reviews/concept_space_$${run}_latest.json; \
	  echo "also: reviews/concept_space_$${run}_latest.html"
