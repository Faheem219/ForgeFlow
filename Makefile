# ForgeFlow — task runner. Recipes use TABS (Make requirement).
.DEFAULT_GOAL := help
COMPOSE := docker compose -f infra/docker-compose.yml
PNPM := corepack pnpm
SANDBOX_IMAGE ?= forgeflow-sandbox:latest
PREVIEW_NETWORK ?= forgeflow-preview

# Host-run scripts read config from the repo-root .env (docker-compose loads it into the container
# itself; a bare `uv run` from backend/ would look for backend/.env and find nothing). We pass the
# root file explicitly via uv's --env-file, as an absolute path so it survives the `cd backend`.
# Only passed when the file exists, so a fresh clone without a .env still runs on code defaults.
DOTENV := $(wildcard $(CURDIR)/.env)
ENV_FILE_ARG := $(if $(DOTENV),--env-file $(DOTENV),)

.PHONY: help dev preview-net proxy host-deps down logs test test-backend test-frontend lint lint-backend lint-frontend fmt fmt-backend fmt-frontend backend-install frontend-install install check-env seed demo-seed demo-reset demo-status demo-rehearse sandbox-build sandbox-smoke sandbox-reap-all skeleton-install skeleton-build skeleton-test skeleton-verify devops-lint ansible-check ansible-deploy ansible-ping k8s-validate k8s-apply k8s-rollout k8s-rollback k8s-demo monitoring-up monitoring-down prod-build

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

dev: preview-net ## Boot api + web + mongo + proxy (needs Colima/Docker running)
	$(COMPOSE) up --build

preview-net: ## Create the preview network if absent (the sandbox manager owns it, not compose)
	@docker network inspect $(PREVIEW_NETWORK) >/dev/null 2>&1 \
		|| docker network create --driver bridge --internal \
			--label forgeflow.managed=true --label forgeflow.role=preview $(PREVIEW_NETWORK)

proxy: preview-net ## Run ONLY the reverse proxy — preview URLs need it even when api/web run on the host
	$(COMPOSE) up -d --no-deps proxy

host-deps: preview-net ## Host mode: start ONLY what a host-run backend needs (generated-app DB + proxy)
	$(COMPOSE) up -d --no-deps appdb proxy

down: ## Stop and remove the local stack
	$(COMPOSE) down

logs: ## Tail stack logs
	$(COMPOSE) logs -f

install: backend-install frontend-install ## Install all deps

backend-install: ## Sync backend deps (uv)
	cd backend && uv sync --extra openai

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
	cd backend && uv run $(ENV_FILE_ARG) python -m scripts.seed

demo-seed: ## Seed the hero-demo account + project (needs MongoDB + DEMO_PASSWORD)
	cd backend && uv run $(ENV_FILE_ARG) python -m scripts.demo seed

demo-reset: ## Return the demo to a clean pre-demo state (deletes demo projects)
	cd backend && uv run $(ENV_FILE_ARG) python -m scripts.demo reset

demo-status: ## Show what hero-demo state is currently seeded
	cd backend && uv run $(ENV_FILE_ARG) python -m scripts.demo status

demo-rehearse: ## Run the headless hero-path rehearsal smoke test (no live infra)
	cd backend && uv run pytest tests/demo -q

sandbox-build: ## Build the sandbox image (Node 20 + pnpm + Playwright chromium + test mongod, non-root)
	docker build -t $(SANDBOX_IMAGE) sandbox/

sandbox-smoke: ## Build + smoke-test the sandbox image (Node app + Playwright chromium + offline MongoDB)
	SANDBOX_IMAGE=$(SANDBOX_IMAGE) bash sandbox/smoke/run_smoke.sh

sandbox-reap-all: ## DANGER: remove ALL forgeflow sandbox containers + volumes (deletes workspace code)
	@echo "This removes ALL forgeflow-sb-* containers, forgeflow-vol-*/forgeflow-home-* volumes"
	@echo "and per-project forgeflow-sbnet-* networks."
	@echo "Workspace code in those volumes is PERMANENTLY LOST."
	@read -p "Type 'yes' to continue: " ans; [ "$$ans" = "yes" ] || { echo "aborted"; exit 1; }
	-docker ps -aq --filter "label=forgeflow.managed=true" | xargs -r docker rm -f
	-docker volume ls -q --filter "label=forgeflow.managed=true" | xargs -r docker volume rm
	@# Per-project networks go too: docker's default address pools are finite, so orphans
	@# eventually make new sandboxes unschedulable. The shared preview/egress ones are kept.
	-docker network ls -q --filter "label=forgeflow.role=sandbox" | xargs -r docker network rm

skeleton-install: ## Install generated-app skeleton deps (pnpm 9 via corepack)
	cd templates/app-skeleton && corepack enable && $(PNPM) install --no-frozen-lockfile

skeleton-build: ## Typecheck + build the generated-app skeleton (FE + BE)
	cd templates/app-skeleton && $(PNPM) run typecheck && $(PNPM) run build

skeleton-test: ## Run the generated-app skeleton unit suites (Vitest + Jest)
	cd templates/app-skeleton && $(PNPM) run test:unit

skeleton-verify: skeleton-install skeleton-build skeleton-test ## Full skeleton check (install + build + unit tests)

# ==================================================================== DevOps (infra/)
# Provisioning, orchestration and monitoring. See docs/devops/README.md for the full runbook.
#
# Ansible needs a POSIX control node: macOS (`brew install ansible`) or Linux/WSL
# (`pipx install ansible`). There is no native Windows control node.
ANSIBLE_DIR := infra/ansible
K8S_DIR := infra/k8s
MONITORING_DIR := infra/monitoring

devops-lint: ## Lint every devops artifact (workflows, k8s manifests, ansible, prometheus)
	@echo "--- actionlint (GitHub workflows) ---"
	docker run --rm -v "$(CURDIR)":/repo -w /repo rhysd/actionlint:latest -color
	@echo "--- kubeconform (k8s manifests) ---"
	docker run --rm -v "$(CURDIR)/$(K8S_DIR)":/work ghcr.io/yannh/kubeconform:latest 		-summary -strict -kubernetes-version 1.31.0 /work/base
	@echo "--- promtool (prometheus config + alert rules) ---"
	docker run --rm -v "$(CURDIR)/$(MONITORING_DIR)/prometheus":/etc/prometheus 		--entrypoint promtool prom/prometheus:v2.54.1 check config /etc/prometheus/prometheus.yml
	@echo "--- ansible-lint ---"
	cd $(ANSIBLE_DIR) && ansible-lint site.yml roles/

ansible-ping: ## Check SSH connectivity to the VM
	cd $(ANSIBLE_DIR) && ansible forgeflow -m ping

ansible-check: ## Dry run: show exactly what provisioning WOULD change (safe, changes nothing)
	cd $(ANSIBLE_DIR) && ansible-playbook site.yml --check --diff

ansible-deploy: ## Provision the Oracle VM (idempotent — a second run should be changed=0)
	cd $(ANSIBLE_DIR) && ansible-playbook site.yml

k8s-validate: ## Schema-validate the Kubernetes manifests
	docker run --rm -v "$(CURDIR)/$(K8S_DIR)":/work ghcr.io/yannh/kubeconform:latest 		-summary -verbose -strict -kubernetes-version 1.31.0 /work/base

k8s-apply: ## Apply the manifests to the current kubectl context
	kubectl apply -f $(K8S_DIR)/base/

k8s-rollout: ## Roll to a new image: make k8s-rollout IMAGE=ghcr.io/<owner>/forgeflow-api:<tag>
	@test -n "$(IMAGE)" || { echo "usage: make k8s-rollout IMAGE=<image>"; exit 2; }
	bash $(K8S_DIR)/scripts/rolling-update.sh "$(IMAGE)" "make k8s-rollout"

k8s-rollback: ## Roll back the API deployment (REVISION=n optional; blank = previous)
	bash $(K8S_DIR)/scripts/rollback.sh $(REVISION)

k8s-demo: ## Zero-downtime + auto-rollback demo: make k8s-demo V1=<image> V2=<image>
	@test -n "$(V1)" -a -n "$(V2)" || { echo "usage: make k8s-demo V1=<image> V2=<image>"; exit 2; }
	bash $(K8S_DIR)/scripts/demo-rolling-update.sh "$(V1)" "$(V2)"

monitoring-up: ## Start Prometheus + Grafana + exporters (needs GRAFANA_ADMIN_PASSWORD)
	@test -n "$$GRAFANA_ADMIN_PASSWORD" || { echo "export GRAFANA_ADMIN_PASSWORD first"; exit 2; }
	docker compose -f $(MONITORING_DIR)/docker-compose.monitoring.yml up -d
	@echo "Grafana  -> http://localhost:3000"
	@echo "Prometheus -> http://localhost:9090"

monitoring-down: ## Stop the monitoring stack
	docker compose -f $(MONITORING_DIR)/docker-compose.monitoring.yml down

prod-build: ## Build the production backend image locally (native arch)
	docker build -f backend/Dockerfile.prod -t forgeflow-api:local .
