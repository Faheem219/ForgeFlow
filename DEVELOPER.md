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
APP_DB_SANDBOX_URI=mongodb://forgeflow-appdb:27017     # the same DB, as a SANDBOX sees it
```

> Both app-DB lines are required. Generated apps cannot use brew mongo: sandboxes sit on an `internal` Docker network with no route to your host, so `localhost` there is the sandbox itself. `make host-deps` starts that DB as a container.
>
> **Once you deploy, these stop being the same database** — and that is intended. `APP_DB_CLUSTER_URI` becomes an Atlas URI (see [Deploy](#deploy)), because that is what a Vercel function can reach; `APP_DB_SANDBOX_URI` stays on the local `appdb`, because a sandbox has no internet. So the **preview** app and the **deployed** app read different data. Pointing the sandbox at Atlas does not fix it: the sandbox cannot reach Atlas either, and the generated app would just time out.

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

Both carry `restart: always` rather than `unless-stopped`, because `unless-stopped` explicitly does
**not** restart a container that was stopped when the daemon went down — which is every docker
restart. Starting a preview also restarts whichever of the two it finds stopped
(`PREVIEW_AUTOSTART_DEPS=false` opts out), so the usual cause of "it worked yesterday" is handled
before the dev servers launch. Neither helps if the containers were never created: run
`make host-deps` after a `make down`.

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
- **`APP_DB_CLUSTER_URI`** — must be an **Atlas** URI, not `localhost:27018`, since the deployed backend has to reach it. A BYO `mongodb+srv://` URI in the credential vault (Settings → Provider credentials) works too. Allow Vercel's egress on the cluster (`0.0.0.0/0` on a shared tier). Since phase-62 a deploy **refuses to start** against a localhost or bare-docker-service URI rather than shipping a function that boots and then times out on every query.

**Bring your own accounts.** Selecting *Bring your own* in the Deploy stage now lists the credentials that mode needs (Vercel token; MongoDB URI is optional) as Stored or Missing, with Deploy disabled until the required ones exist. Add them in Settings → Provider credentials.

**Deleting a deployment.** The Deploy stage has a **Delete** action: it destroys both Vercel deployments, returns the stage to *Not started* and marks Validate stale. It never touches the database — dropping data stays an explicit choice when deleting the whole project. Deleting a project now tears its deployments down too.

Everything up to and including **test + self-healing repair** runs entirely offline once the sandbox image is built.

**Want a persistent Node process instead of a function?** `DEPLOY_BE_PROVIDER=render` restores the Render path — but Render builds by *cloning* `DEPLOY_REPO_URL`, and nothing pushes the workspace to a git remote, so it only works if you point it at a repo that already holds the app. That limitation is exactly why the default moved to Vercel.

### Choosing the LLM provider

ForgeFlow targets **OpenAI-compatible Chat Completions as its primary interface** (the standard most
providers implement); Anthropic is fully supported behind the same seam and is still the default.
Model ids are configuration, not identity — set them to whatever your endpoint serves.

```bash
cd backend && uv sync --extra openai     # only needed for LLM_PROVIDER=openai
# .env:
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_BASE_URL=            # blank = api.openai.com · http://localhost:11434/v1 for Ollama
MODEL_CODEGEN=gpt-4o        # real code/test/repair work
MODEL_ROUTING=gpt-4o-mini   # summaries + routing
MODEL_CLASSIFY=             # blank = use MODEL_ROUTING; set it to go cheaper still
```

`MODEL_CLASSIFY` covers the highest-volume cheap call — deciding whether a change request needs a
full phased build or a single incremental pass — so it is worth pointing at the cheapest model your
endpoint has.

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
| Generated app: `getaddrinfo EAI_AGAIN forgeflow-appdb` | the `appdb` container is gone, so the name has no DNS record. Preview start restarts it if it merely stopped; if it was removed, `make host-deps` |
| Repair stops at 0 attempts: *"sandbox environment problem"*, tests show `[forgeflow:test-db-unavailable]` or `could not find an valid binary path` | the sandbox image predates the baked test `mongod` (phase-65) — `make sandbox-build`; sandboxes are recreated from the new image on next use, code intact |
| Deploy refuses: *"not reachable from the internet"* | `APP_DB_CLUSTER_URI` is a localhost/docker address; set it to your Atlas URI (`APP_DB_SANDBOX_URI` stays local) |
| Vercel dashboard shows no environment variables | pre-phase-62 deployment — redeploy; env is now mirrored to the Vercel *project*, not just the deployment |
| Preview URL refuses to connect | proxy not running — preview start restarts a *stopped* one; if it was removed, `make proxy` |
| Preview URL returns `502` | the proxy is up but the sandbox behind it is not — it was idle-reaped after `SANDBOX_IDLE_TIMEOUT_S`; start the preview again |
| `Sandbox runtime unavailable` | `make sandbox-build`; if stale, `make sandbox-reap-all` then retry |
| API boot: `ValidationError … budget_cap_inr…` | stale `.env` with inline comments — `cp .env.example .env`, re-add your keys |
| pnpm `ERR_UNKNOWN_BUILTIN_MODULE: node:sqlite` | global pnpm 11 — `corepack enable` |
| `demo-seed` says password UNSET | `.env` must be at the repo **root**, not `backend/.env` |
| Compose: `network forgeflow-preview … incorrect label` | `docker network rm forgeflow-preview && make dev` |
