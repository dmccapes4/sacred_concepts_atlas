# =========================
# Validation — bibliography <-> disk <-> database
# =========================

.PHONY: validate validate-disk validate-db

validate: ## Full validation (bibliography vs disk vs DB)
	@$(PY) scripts/validate_bibliography.py --bib $(BIB) --raw-dir $(RAW_DIR) --db $(DB)

validate-disk: ## Skip DB checks (pre-Phase-1)
	@$(PY) scripts/validate_bibliography.py --bib $(BIB) --raw-dir $(RAW_DIR) --skip-db
