# Phase 01 — Repo, tooling & local dev

## Title & ID

- **ID:** `phase-01`
- **Title:** Repo, tooling & local dev
- **Epic:** Epic 0 — Foundations & Scaffolding
- **Milestone:** M1

## Goal

Stand up the monorepo skeleton (backend/frontend/templates/sandbox/infra), wire quality tooling, and make `make dev` boot API + web + Mongo + proxy on Colima/Docker in one command.

## Context

Everything else builds on this. It establishes the repo structure from `IMPLEMENTATION_PLAN.md` §5, the FastAPI control plane (D3), the React PWA frontend (D3), and the **layered configuration foundation** that later phases resolve settings through. Per the user directive, config precedence is **admin panel > env > default**; this phase builds the *env + default* layers and the resolver seam so the DB (admin) layer can slot in at `phase-51` without touching consumers.

## Depends on

- **Phase IDs:** — (root phase)
- **External:** Colima + Docker installed and running; `uv` (Python), `pnpm` (Node 20) available; a MongoDB URI (local container is fine for now).

### Prerequisite check

- `docker info` succeeds (Colima up).
- `uv --version`, `pnpm --version`, `node --version` (≥20) all succeed.
- Repo root contains `CLAUDE.md`, `IMPLEMENTATION_PLAN.md`.

## Detailed tasks

1. **Repo layout.** Create the `IMPLEMENTATION_PLAN.md` §5 tree: `backend/`, `frontend/`, `templates/app-skeleton/` (empty placeholder + README), `sandbox/`, `infra/`, `eval/specs/` (placeholder), keeping `plans/` as-is.
2. **Backend project (`backend/`).**
   - `pyproject.toml` (managed by `uv`): deps `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`, `motor`, `beanie`, `anthropic`, `docker`, `httpx`, `cryptography`, `python-jose[cryptography]`, `passlib[bcrypt]`, `websockets`; dev deps `pytest`, `pytest-asyncio`, `ruff`, `black`, `mypy`, `types-*`.
   - `app/core/config.py` — **layered `Settings` resolver.** Define `Settings(BaseSettings)` (Pydantic v2) reading env + defaults. Wrap access in a `ConfigResolver` with an ordered provider chain `[EnvProvider, DefaultProvider]` exposing `get(key, cast)`. Document that `phase-51` prepends a `DbSettingProvider` (admin overrides). All later code MUST read config via this resolver, never `os.environ` directly.
   - `app/core/logging.py` — structured JSON logging setup.
   - `app/core/errors.py` — error taxonomy base classes `UserError`, `SystemError`, `ProviderError` + FastAPI exception handlers → consistent error envelope `{error: {type, message, detail?}}`.
   - `app/api/app.py` — FastAPI app factory `create_app()`; mounts `/health` returning `{status:"ok", env, version}`; CORS from config; registers exception handlers.
   - `backend/tests/test_health.py` — asserts `/health` 200.
3. **Frontend project (`frontend/`).**
   - Vite + React 18 + TS (`strict`) + Tailwind (dark theme, indigo/violet tokens in `tailwind.config.ts` + `src/app/theme/`), ESLint + Prettier.
   - `src/main.tsx`, `src/app/App.tsx` rendering a minimal shell + a "backend health" indicator hitting `/health`.
   - `src/lib/apiClient.ts` — typed fetch wrapper reading `VITE_API_BASE_URL`.
4. **Infra (`infra/docker-compose.yml`).** Services: `api` (backend, hot reload), `web` (frontend dev server), `mongo` (or Atlas via env), `proxy` (Caddy) reverse-proxying `web`, `api`, and reserving a wildcard for future sandbox previews. `infra/caddy/Caddyfile`.
5. **Task runner (`Makefile`).** `make dev` (compose up), `make down`, `make test` (backend `uv run pytest` + frontend `pnpm test`), `make lint` (ruff+black+mypy + eslint), `make fmt`.
6. **Quality gates.** `.pre-commit-config.yaml` (ruff, black, mypy, eslint, prettier); root `README.md` (quickstart); GitHub Actions `.github/workflows/ci.yml` running lint + type + tests for both apps.
7. **Config surface.** Create authoritative `.env.example` seeded with `IMPLEMENTATION_PLAN.md` §13 vars (values blank/placeholder). Ensure `.gitignore` excludes `.env`.

## Out of scope for this phase

- DB models & repositories (`phase-02`), auth (`phase-03`), realtime (`phase-05`), PWA service worker/offline (`phase-04`), the DB-backed admin config layer & admin UI (`phase-51`/`phase-52`). Only the resolver *seam* is built here.

## Acceptance criteria

- `make dev` boots `api`, `web`, `mongo`, `proxy`; `GET /health` returns 200 `{status:"ok"}`; web shell loads and shows backend health green.
- `make lint` and `make test` pass locally and in CI.
- `app/core/config.py` exposes a resolver with an ordered provider chain; no module reads `os.environ` directly (grep check in CI).
- `.env.example` contains every §13 variable; `.env` is git-ignored.

## Tests to write

- `backend/tests/test_health.py` — health 200 + envelope shape.
- `backend/tests/test_config_resolver.py` — env overrides default; unknown key raises `SystemError`; provider order respected (stub a fake high-priority provider to prove precedence seam).
- `backend/tests/test_error_envelope.py` — raising `UserError` yields the standard envelope + correct HTTP status.
- `frontend/src/lib/apiClient.test.ts` (Vitest) — base URL wiring + error mapping.

## Manual verification steps

1. `make dev` → open the proxied web URL; health indicator green.
2. `curl http://localhost:8000/health` → `{"status":"ok",...}`.
3. `make lint && make test` → all green.
4. Temporarily set an env override and confirm the resolver returns it over the default (unit test already covers this).

## Rollback / cleanup notes

- `make down` stops/removes containers. Remove the named Mongo volume to wipe local data. Fully reversible (no persistent external resources created).

## Design notes

- **Layered config seam (user directive).** Introduced now so no consumer refactor is needed at `phase-51`; only the provider chain gains a DB layer at the front. Precedence is strictly **admin (DB) > env > default**.
- Caddy chosen over Traefik for a simpler single-file config; either satisfies the plan.

## Status

✅ done — Implemented and verified: `make test` (backend 8 + frontend 3 passing) and `make lint`
(ruff + black + mypy + eslint + prettier + tsc, all clean); backend boots under uvicorn with
`GET /health` → `200 {"status":"ok",...}`; frontend production `build` succeeds; layered config
resolver + `os.environ` guard in place; `.env.example` covers every §13 var; `.env` git-ignored.
**Deferred (environment-limited):** full `make dev` stack boot was not exercised here (Docker
daemon/Colima unavailable in the build env) — the compose file was validated via
`docker compose config` and both service Dockerfiles are in place.
