# ForgeFlow — task runner. Recipes use TABS (Make requirement).
.DEFAULT_GOAL := help
COMPOSE := docker compose -f infra/docker-compose.yml
PNPM := corepack pnpm
SANDBOX_IMAGE ?= forgeflow-sandbox:latest

.PHONY: help dev down logs test test-backend test-frontend lint lint-backend lint-frontend fmt fmt-backend fmt-frontend backend-install frontend-install install check-env seed sandbox-build sandbox-smoke

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

dev: ## Boot api + web + mongo + proxy (needs Colima/Docker running)
	$(COMPOSE) up --build

down: ## Stop and remove the local stack
	$(COMPOSE) down

logs: ## Tail stack logs
	$(COMPOSE) logs -f

install: backend-install frontend-install ## Install all deps

backend-install: ## Sync backend deps (uv)
	cd backend && uv sync

frontend-install: ## Install frontend deps (pnpm 9 via corepack)
	cd frontend && corepack enable && $(PNPM) install

test: test-backend test-frontend ## Run all tests

test-backend: ## Backend unit tests
	cd backend && uv run pytest -q

test-frontend: ## Frontend unit tests
	cd frontend && $(PNPM) test -- --run

lint: lint-backend lint-frontend ## Lint + type-check both apps

lint-backend: ## ruff + black --check + mypy
	cd backend && uv run ruff check . && uv run black --check . && uv run mypy .

lint-frontend: ## eslint + prettier --check + tsc
	cd frontend && $(PNPM) run lint && $(PNPM) run format:check && $(PNPM) run typecheck

fmt: fmt-backend fmt-frontend ## Auto-format both apps

fmt-backend: ## ruff --fix + black
	cd backend && uv run ruff check --fix . && uv run black .

fmt-frontend: ## prettier --write
	cd frontend && $(PNPM) run format

check-env: ## Verify no direct os.environ reads outside the config layer
	@bash infra/scripts/check_no_os_environ.sh

seed: ## Ensure indexes + seed default platform settings (needs MongoDB)
	cd backend && uv run python -m scripts.seed

sandbox-build: ## Build the sandbox image (Node 20 + pnpm + Playwright chromium, non-root)
	docker build -t $(SANDBOX_IMAGE) sandbox/

sandbox-smoke: ## Build + smoke-test the sandbox image (Node app + Playwright chromium)
	SANDBOX_IMAGE=$(SANDBOX_IMAGE) bash sandbox/smoke/run_smoke.sh
