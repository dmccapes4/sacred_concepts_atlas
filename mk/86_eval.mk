# =========================
# Eval harness & run watchdog (pre-flight + in-flight checks for Phase 3)
# =========================
# eval-harness: 12 golden sections x 2 isolated runs (~30 min GPU), checks
#   signature invariants, registry health, parallel convergence, and
#   inter-run agreement. Never writes db/atlas.db. Run BEFORE agent-run.
# watchdog: codified abort criteria for a live run (A1 stall, A2 empty rate,
#   A3 registry runaway, A4 slowdown + warns). Run in a second terminal.

WATCH ?= 0    # seconds between watchdog re-checks (0 = single shot)

.PHONY: eval-harness watchdog agents-reset

eval-harness: ## Pre-flight eval: dual isolated golden-section runs + checks
	@$(PY) scripts/eval_harness.py --db $(DB) --model $(AGENT_MODEL) \
		--embed-model $(EMBED_MODEL)

watchdog: ## Health-check the latest unfinished run (WATCH=300 to loop)
	@$(PY) scripts/ingestion_watchdog.py --db $(DB) --watch $(WATCH)

agents-reset: ## Wipe ALL concept state (concepts, signatures, runs). Needs CONFIRM=1
ifndef CONFIRM
	@echo "This deletes all concepts, section_concepts, and runs rows."
	@echo "Sections, pages, embeddings, FTS are untouched. Re-run with CONFIRM=1."
else
	@$(SQL) "DELETE FROM section_concepts;"
	@$(SQL) "DELETE FROM concepts;"
	@$(SQL) "DELETE FROM runs;"
	@echo "concept state wiped — next agent-run starts from a cold registry"
endif
