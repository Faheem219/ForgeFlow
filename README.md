# ForgeFlow

AI-native, human-in-the-loop **PWA** that carries a web-app idea — from screenshots or a
structured spec — through **requirements → design → build → test + self-healing repair →
deploy → live validation**, letting you refine, revisit, or skip **any** stage.

- **What to build:** [`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md) (source of truth).
- **How we work:** [`CLAUDE.md`](./CLAUDE.md) (operating manual).
- **Per-phase specs:** [`plans/`](./plans/) — start at [`plans/README.md`](./plans/README.md).

> Status: **Phase 01** (repo, tooling & local dev) — the monorepo skeleton, quality tooling,
> and one-command local dev.

## Repository layout

```
backend/     FastAPI control plane (Python 3.11, uv)
frontend/    React + Vite + TS + Tailwind PWA (pnpm)
templates/   app-skeleton/ — fixed-stack generated-app template (filled in phase 22)
sandbox/     per-project sandbox image (built in phase 10)
infra/       docker-compose.yml + Caddy reverse proxy
eval/        benchmark specs (phase 43)
plans/       per-phase execution specs
```

## Prerequisites

- **Docker** via **Colima** (`colima start`) — for `make dev`.
- **[uv](https://docs.astral.sh/uv/)** (Python 3.11 is fetched/managed by uv).
- **Node 20** + **pnpm 9** (pinned via `corepack`; run `corepack enable` once).

## Quickstart

```bash
cp .env.example .env            # fill in values as needed (safe defaults for local)
make dev                        # boots api + web + mongo + proxy (needs Colima/Docker)
# API:  http://localhost:8000/health
# Web:  http://localhost:5173   (also proxied at http://localhost via Caddy)
```

Running the API and web app **on the host** instead (`uvicorn` + `pnpm dev`)? The per-project
preview URLs (`http://<project>.preview.localhost`) are served by the Caddy proxy, so start it or
they will refuse to connect:

```bash
make proxy                      # just the reverse proxy on :80 (api/web stay on the host)
```

Run the checks locally (no Docker required):

```bash
make test                       # backend pytest + frontend vitest
make lint                       # ruff + black --check + mypy + eslint + prettier --check
make fmt                        # auto-format both apps
```

### Backend only

```bash
cd backend
uv sync                         # create env + install deps (Python 3.11 via uv)
uv run uvicorn app.api.app:app --reload
uv run pytest
```

### Frontend only

```bash
cd frontend
corepack enable                 # once; pins pnpm 9 (Node 20 compatible)
pnpm install
pnpm dev
pnpm test
```

## Configuration

All settings resolve through a **layered config** with precedence
**admin panel (DB) > env > code default**. Phase 01 ships the *env + default* layers and the
resolver seam; the DB/admin layer and dashboard arrive in phases 51–52. Every variable is
documented in [`.env.example`](./.env.example). Never commit `.env`.

## License

Proprietary / coursework project (PBL). Not for redistribution.
