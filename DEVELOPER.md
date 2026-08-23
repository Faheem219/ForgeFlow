# ForgeFlow — Developer Guide

How to run ForgeFlow locally with **every stage functional**. What is being built: [`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md). Every config variable: [`.env.example`](./.env.example).

---

## 0. One-time setup

```bash
colima start                      # or Docker Desktop — a daemon must be reachable
corepack enable                   # pins pnpm 9; a global pnpm 11 breaks on Node 20
cp .env.example .env              # never commit .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
                                  # paste into .env → FERNET_KEY=
make install                      # backend (uv sync) + frontend (pnpm install)
make sandbox-build                # the image every generated app runs in (~1.8 GB, once)
```

Then fill in `.env` — see [§3](#3-variables). Minimum for anything to work: `FERNET_KEY` + `ANTHROPIC_API_KEY`.

---

## 1. Section A — host mode (2 terminals + brew MongoDB)

Backend and frontend run on your machine; `mongodb-community` is the control-plane DB.

**`.env` must contain:**

```bash
MONGODB_URI=mongodb://localhost:27017                  # brew mongo = control plane
APP_DB_CLUSTER_URI=mongodb://localhost:27018           # generated-app DB, as the HOST sees it
APP_DB_SANDBOX_URI=mongodb://forgeflow-appdb:27017     # the SAME DB, as a SANDBOX sees it
```

> Both app-DB lines are required. Generated apps cannot use brew mongo: sandboxes sit on an `internal` Docker network with no route to your host, so `localhost` there is the sandbox itself. `make host-deps` starts that DB as a container.

**Run:**

```bash
brew services start mongodb-community     # control-plane DB
make host-deps                            # generated-app DB (:27018) + Caddy proxy (:80)

# terminal 1
cd backend && uv run --env-file ../.env uvicorn app.api.app:app --reload

# terminal 2
cd frontend && pnpm dev
```

First run only, in a third terminal: `make seed` (indexes + default platform settings).

App at **http://localhost:5173** · API at **http://localhost:8000/health**.

Why the two containers are not optional:

| Container | Without it |
|---|---|
| `appdb` (:27018) | generated apps boot, then every query dies with mongoose `buffering timed out after 10000ms` |
| `proxy` (:80) | preview URLs (`*.preview.localhost`) refuse to connect — it is the only route into a sandbox |

Sandboxes need no wiring: the backend picks up your daemon via `docker.from_env()`. If `DOCKER_HOST` is unset and `/var/run/docker.sock` is absent (some Colima setups), export `DOCKER_HOST=unix://$HOME/.colima/default/docker.sock`.

---

## 2. Section B — everything in Docker Compose

Both MongoDBs are compose services, so nothing to start by hand.

```bash
make dev        # api + web + control-plane mongo + generated-app mongo + proxy
make seed       # first run only, in a second terminal
```

App at **http://localhost:5173** · API at **http://localhost:8000/health** · proxy at **http://localhost**.

`make down` stops it, `make logs` tails it.

The compose file already sets `MONGODB_URI` and `APP_DB_CLUSTER_URI` to the right service names, overriding your `.env` — so the same `.env` works for both sections. It also mounts `/var/run/docker.sock` into the `api` service, which is what makes build/test/deploy work from inside a container. That grants the container root-equivalent host Docker access: fine for local dev, never for anything shared.

Colima without `/var/run/docker.sock`: `DOCKER_SOCK=$HOME/.colima/default/docker.sock make dev`.

---

## 3. Variables

Precedence: **`/admin` panel (DB) > `.env` > code default.** Everything is settable in either place, so `.env` alone is enough. Three keys are **env-only** (read before the admin layer exists): `MONGODB_URI`, `FORGEFLOW_META_DB`, `FERNET_KEY`.

Secrets set via `/admin` are encrypted and never readable again. To get an admin login: set `SEED_ADMIN_EMAIL` + `SEED_ADMIN_PASSWORD` in `.env`, then `make seed`.

### What each feature needs

| Feature | Required | Blank →|
|---|---|---|
| Auth, projects, chat, requirements | a reachable `MONGODB_URI` | won't boot |
| Credential vault + `/admin`-stored secrets | `FERNET_KEY` | `FERNET_KEY is not configured` |
| **Codegen, build, repair** | `ANTHROPIC_API_KEY` | stages 401 / no-op |
| **Design** | `STITCH_API_KEY` | falls back down `DESIGN_FALLBACK_CHAIN` → `fake` |
| **Build / preview / test / repair** | Docker + `make sandbox-build` | `Sandbox runtime unavailable` |
| **Deploy — frontend + backend** | `VERCEL_TOKEN` | `platform has no vercel credential` |
| **Deploy — database** | `APP_DB_CLUSTER_URI` = an Atlas URI | deployed app can't reach `localhost:27018` |
| **Live validation** | a successful deploy | no URL to validate against |
| `/eval` dashboard | run the harness → `eval/results/*.json` | "no data" |
| Hero demo seed | `DEMO_PASSWORD` | `make demo-seed` refuses |

### Deploy

Two things are needed, and both are just config:

- **`VERCEL_TOKEN`** — deploys the frontend *and* the backend (phase-58). Both ship as inline file uploads: the sandbox runs `pnpm build` and the `dist` files go up for the SPA; the backend's source plus its `vercel.json` go up and Vercel builds it into a function. No git host, no repo, no second credential.
- **`APP_DB_CLUSTER_URI`** — must be an **Atlas** URI, not `localhost:27018`, since the deployed backend has to reach it. A BYO `mongodb+srv://` URI in the credential vault (Settings → Provider credentials) works too. Allow Vercel's egress on the cluster (`0.0.0.0/0` on a shared tier).

Everything up to and including **test + self-healing repair** runs entirely offline once the sandbox image is built.

**Want a persistent Node process instead of a function?** `DEPLOY_BE_PROVIDER=render` restores the Render path — but Render builds by *cloning* `DEPLOY_REPO_URL`, and nothing pushes the workspace to a git remote, so it only works if you point it at a repo that already holds the app. That limitation is exactly why the default moved to Vercel.

### Using an OpenAI-compatible provider instead

```bash
cd backend && uv sync --extra openai
# .env:
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_BASE_URL=            # blank = api.openai.com · http://localhost:11434/v1 for Ollama
MODEL_CODEGEN=gpt-4o
MODEL_ROUTING=gpt-4o-mini
```

Getting a Stitch key: [stitch.withgoogle.com](https://stitch.withgoogle.com) → profile → Stitch settings → API keys. Quota is ~350 generations/month, and the API is text-prompt only — screenshot intake falls back to `fake`.

---

## 4. Commands

```bash
make help              # authoritative list
make test              # pytest + vitest
make lint / make fmt   # ruff+black+mypy+eslint+prettier+tsc
make demo-seed         # hero demo project (needs DEMO_PASSWORD)
make sandbox-reap-all  # DANGER: deletes ALL sandboxes + workspace code
```

Routes: `/` dashboard · `/projects/:id` workspace · `/eval` benchmarks · `/admin` config (admins only) · `/settings` credential vault.

---

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| `sandbox reconcile skipped — Docker unavailable` | daemon not reachable — start Colima/Docker Desktop; in compose mode check the socket mount ([§2](#2-section-b--everything-in-docker-compose)) |
| Generated app: `buffering timed out after 10000ms` | `APP_DB_SANDBOX_URI` unset or `appdb` not running — `make host-deps` |
| Preview URL refuses to connect | proxy not running — `make proxy` |
| `Sandbox runtime unavailable` | `make sandbox-build`; if stale, `make sandbox-reap-all` then retry |
| API boot: `ValidationError … budget_cap_inr…` | stale `.env` with inline comments — `cp .env.example .env`, re-add your keys |
| pnpm `ERR_UNKNOWN_BUILTIN_MODULE: node:sqlite` | global pnpm 11 — `corepack enable` |
| `demo-seed` says password UNSET | `.env` must be at the repo **root**, not `backend/.env` |
| Compose: `network forgeflow-preview … incorrect label` | `docker network rm forgeflow-preview && make dev` |
