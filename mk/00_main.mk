# ===============================
# Sacred Concepts Atlas — 00_main.mk
# ===============================
# MakefileBook: imperative execution layer (fetch, ingest, index, agents, validation).
# Source of truth for texts: BIBLIOGRAPHY_SACRED_CONCEPTS_ATLAS.md

# ---------- Shell ----------
SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -eo pipefail -c

# ---------- Core Vars ----------
RAW_DIR   ?= data/raw
DB_DIR    ?= db
DB        ?= $(DB_DIR)/atlas.db
BIB       ?= BIBLIOGRAPHY_SACRED_CONCEPTS_ATLAS.md
PY        ?= $(shell [ -x venv/bin/python ] && echo venv/bin/python || echo python3)
SQL       := $(PY) scripts/sql.py $(DB)   # sqlite3 CLI not installed; python stand-in
# Fetch pattern: targets guard with `[ -s file ] ||` so re-runs are no-ops.
# UA required: tanach.us 403s the default wget agent.
WGET      := wget -q --show-progress -U "Mozilla/5.0 (X11; Linux x86_64)"
SHASUMS   := $(RAW_DIR)/SHA256SUMS

# ---------- Ollama knobs (Phase 2/3) ----------
EMBED_MODEL ?= bge-m3
AGENT_MODEL ?= atlas-conceptor   # built from modelfiles/ in Phase 3

.PHONY: help dirs py-venv fetch-all verify-all validate clean-raw db-init

help:
	@grep -hE '^[a-zA-Z0-9_\-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2}'

dirs:
	@mkdir -p $(RAW_DIR) $(DB_DIR) scripts modelfiles

py-venv: ## Create local venv and install deps
	@[ -x venv/bin/python ] || python3 -m venv venv
	@venv/bin/python -m pip install --quiet --upgrade pip
	@[ -f requirements.txt ] && venv/bin/python -m pip install --quiet -r requirements.txt || true

# ---------- Aggregates (per-source targets live in mk/NN_*.mk) ----------
fetch-all: tanakh-fetch bible-web-fetch quran-fetch ## Fetch all Wave-1 artifacts

verify-all: tanakh-verify bible-web-verify quran-verify ## Integrity-check fetched artifacts

sha256-log: ## Record sha256 of every raw artifact
	@cd $(RAW_DIR) && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
	@echo "wrote $(SHASUMS)"

db-init: dirs ## Create SQLite schema (Phase 1)
	@$(SQL) --file $(DB_DIR)/schema.sql

clean-raw: ## Remove all fetched artifacts
	rm -rf $(RAW_DIR)

# =========================
# Include per-source targets (single fan-out point)
# =========================
-include mk/01_tanakh.mk
-include mk/02_bible_web.mk
-include mk/03_quran.mk
-include mk/70_query.mk
-include mk/80_embeddings.mk
-include mk/85_agents.mk
-include mk/86_eval.mk
-include mk/90_validate.mk
