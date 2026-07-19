# ===============================
# 87_graph.mk — Phase 4: edge materialization + discovery queue
# ===============================
# All targets are pure SQL/numpy over section_concepts — no LLM, no Ollama,
# no embeddings. Safe to run while an ingestion is live on ANOTHER db file
# (SQLite WAL handles a reader+writer on the same file too, but keep graph
# builds off a db that is mid-ingestion: edges need a COMPLETED run).

RUN_ID ?=                     # default: latest finished run in $(DB)
RUN_FLAG = $(if $(RUN_ID),--run $(RUN_ID))

# Tunable thresholds (mirror the tau knobs in 85_agents.mk)
CONCEPTUAL_CUTOFF ?= 0.35     # min-sum overlap floor for section-section edges
COOC_MIN_COUNT    ?= 3        # co-occurrences before an NPMI edge is trusted
NPMI_FLOOR        ?= 0.10     # positive-association floor
COVAR_MIN_JOINT   ?= 5        # joint sections before an r-value is trusted
COVAR_R_FLOOR     ?= 0.40     # |r| floor

.PHONY: graph graph-discover graph-export graph-stats graph-clean \
        concept-merge signal-harness

concept-merge: ## Merge near-duplicate concepts (LLM-adjudicated; DRY=1 to preview)
	@$(PY) scripts/merge_concepts.py --db $(DB) $(if $(DRY),--dry-run)

signal-harness: ## Concept-signal vs semantic-baseline retrieval comparison
	@$(PY) scripts/concept_signal_harness.py --db $(DB)

graph: ## Build all 5 edge kinds from latest finished run (RUN_ID= to pin)
	@$(PY) scripts/build_edges.py --db $(DB) $(RUN_FLAG) --kind all \
		--conceptual-cutoff $(CONCEPTUAL_CUTOFF) \
		--cooc-min-count $(COOC_MIN_COUNT) --npmi-floor $(NPMI_FLOOR) \
		--covar-min-joint $(COVAR_MIN_JOINT) --covar-r-floor $(COVAR_R_FLOOR)

graph-discover: ## Discovery queue: high conceptual overlap, no structural edge
	@$(PY) scripts/build_edges.py --db $(DB) $(RUN_FLAG) --kind conceptual \
		--conceptual-cutoff $(CONCEPTUAL_CUTOFF) --discover \
		--discover-top $(or $(TOP),100)

graph-export: ## Export GraphML (conceptual + structural) for Gephi/networkx
	@$(PY) scripts/build_edges.py --db $(DB) $(RUN_FLAG) --kind structural --export

graph-stats: ## Edge counts by kind/method
	@$(SQL) "SELECT kind, method, COUNT(*) AS edges, ROUND(AVG(weight),3) AS avg_w \
		FROM edges GROUP BY kind, method ORDER BY kind"

graph-clean: ## Drop all derived edges (rebuildable at any time)
	@$(SQL) "DELETE FROM edges;"
	@echo "edges wiped — rebuild with: make graph"
