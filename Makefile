# Eval suite commands (see evals/AGENTS.md). They need OPENAI_API_KEY; LLM_MODE=fake only checks the
# pipeline (it writes evals/results/latest.fake.json, which the gate never reads).
EVAL_COMMAND ?= cd backend && uv run python -m evals.run --suite full
EVAL_SMOKE_COMMAND ?= cd backend && uv run python -m evals.run --suite smoke

.PHONY: scaffold verify adr task check-env init help up down eval eval-smoke lint test test-integration fmt hooks npm-lock audit loadtest loadtest-restart

## scaffold: Create the initial project structure (run once after cloning)
scaffold:
	@echo "Scaffolding Nexus AI..."
	@mkdir -p docs/02_outputs
	@mkdir -p tasks/active tasks/done
	@mkdir -p tests/e2e tests/contract
	@mkdir -p verify/scripts
	@mkdir -p backend frontend
	@echo "Done. Next: fill in docs/00_intake/intake_questionnaire.md"

## verify: Run all verification checks (e2e + DOM)
APP_URL ?= http://localhost:8080
verify:
	@echo "Running verification against $(APP_URL) (start the stack with: make up)..."
	@APP_URL=$(APP_URL) bash verify/scripts/run-e2e.sh
	@APP_URL=$(APP_URL) npx ts-node verify/scripts/check-dom.ts

## adr: Create a new ADR (usage: make adr SLUG=my-decision)
adr:
	@if [ -z "$(SLUG)" ]; then echo "Usage: make adr SLUG=my-decision"; exit 1; fi
	@N=$$(ls adr/*.md 2>/dev/null | grep -v template | grep -v AGENTS | wc -l); \
	 N=$$((N + 1)); \
	 PADDED=$$(printf "%04d" $$N); \
	 cp adr/template.md "adr/$$PADDED-$(SLUG).md"; \
	 echo "Created adr/$$PADDED-$(SLUG).md"

## task: Create a new task from the template (usage: make task NAME=my-task)
task:
	@if [ -z "$(NAME)" ]; then echo "Usage: make task NAME=my-task"; exit 1; fi
	@cp tasks/_template.md "tasks/active/$(NAME).md"; \
	 echo "Created tasks/active/$(NAME).md"
	@echo "Next: ask your AI tool to write tasks/active/$(NAME).scratch.md before coding"

## check-env: Validate .env has all required vars from .env.example
check-env:
	@bash verify/scripts/check-env.sh

## init: List all unreplaced [[PLACEHOLDER]] tokens in the repo (run after cloning)
init:
	@echo "Scanning for unreplaced [[PLACEHOLDER]] tokens..."
	@echo ""
	@git ls-files | xargs grep -rn '\[\[' --include="*.md" --include="*.yml" --include="*.yaml" --include="*.sh" --include="*.ts" --include="*.json" 2>/dev/null | grep -v Binary || echo "✓ No unreplaced placeholders found"
	@echo ""
	@echo "Replace each token, then re-run 'make init' to verify."

## up: Start the local stack (docker compose)
up:
	@docker compose up -d --build

## down: Stop the local stack
down:
	@docker compose down

## eval: Run the full LLM eval suite and gate on evals/eval-baseline.json
eval:
	@if [ -z "$(EVAL_COMMAND)" ]; then echo "Set EVAL_COMMAND (command that writes evals/results/latest.json)"; exit 1; fi
	@mkdir -p evals/results
	@$(EVAL_COMMAND)
	@python3 evals/check_baseline.py

## eval-smoke: Run the small eval subset (CI) and gate on smoke thresholds
eval-smoke:
	@if [ -z "$(EVAL_SMOKE_COMMAND)" ]; then echo "Set EVAL_SMOKE_COMMAND (command that writes evals/results/latest.json)"; exit 1; fi
	@mkdir -p evals/results
	@$(EVAL_SMOKE_COMMAND)
	@python3 evals/check_baseline.py --smoke

## lint: Lint, type-check and format-check backend and frontend
lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd frontend && npm run lint && npm run typecheck

## test: Run backend and frontend unit tests (integration tests need `make up` services; see 06_dev_setup.md)
test:
	cd backend && uv run pytest -m "not integration"
	cd frontend && npm test

## test-integration: Backend tests against a real Postgres (needs `make up`; POSTGRES_PORT if not 5432)
POSTGRES_PORT ?= 5432
test-integration:
	cd backend && TEST_DATABASE_URL=postgresql+psycopg://nexus:nexus@localhost:$(POSTGRES_PORT)/nexus uv run pytest -m integration

## audit: Known-vulnerability scan of the locked dependencies (pip-audit, npm audit; needs network)
audit:
	cd backend && uv export --frozen --no-dev --no-hashes -o /tmp/nexus-audit-requirements.txt >/dev/null
	cd backend && uvx pip-audit -r /tmp/nexus-audit-requirements.txt --no-deps --disable-pip
	cd frontend && npm audit --omit=dev
	cd frontend && npm audit

## loadtest: 20 concurrent streams + 5 req/s against the fake model, thresholds from the PRD (rebuilds the stack in fake mode)
loadtest:
	@bash loadtest/run.sh

## loadtest-restart: restart the API under load and check nothing stays locked (MODE=graceful|kill)
MODE ?= graceful
loadtest-restart:
	@bash loadtest/restart.sh $(MODE)

## npm-lock: Regenerate package-lock.json files with the container's npm (run after changing JS deps)
# The host npm can omit optional platform packages from the lockfile, which makes `npm ci` fail in
# Docker and CI. Generating the lock with the same Node/npm those use keeps all three in agreement.
npm-lock:
	@for dir in frontend .; do \
	  echo "== $$dir"; \
	  rm -f $$dir/package-lock.json; \
	  docker run --rm --user "$$(id -u):$$(id -g)" -e HOME=/tmp -v "$$PWD/$$dir:/app" -w /app node:24-alpine \
	    sh -c 'npm install --package-lock-only --no-audit --no-fund >/dev/null 2>&1 && npm ci --dry-run >/dev/null 2>&1 && echo "lock OK"'; \
	done

## fmt: Auto-format backend and frontend
fmt:
	cd backend && uv run ruff check --fix . && uv run ruff format .
	cd frontend && npm run fmt

## hooks: Install the pre-commit git hooks (ruff, prettier, gitleaks)
hooks:
	cd backend && uv run pre-commit install --config ../.pre-commit-config.yaml

## help: Show this help
help:
	@grep -E '^## ' Makefile | sed 's/## //' | column -t -s ':'
