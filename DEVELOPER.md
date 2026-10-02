# ForgeFlow — Developer Guide

What is being built: [`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md) · every setting: [`.env.example`](./.env.example)

## Prerequisites

- Docker running (Docker Desktop, or `colima start`)
- `uv`, Node 20, then `corepack enable` (pins pnpm 9)
- Host mode only: `brew install mongodb-community`

## One-time setup

```bash
cp .env.example .env     # fill in the keys below
make install             # backend + frontend deps
make sandbox-build       # image every generated app runs in (~1.8 GB; rebuild after sandbox/ changes)
make seed                # indexes + default settings (MongoDB must be running)
```

## Run

| | Commands |
|---|---|
| **Host** (fast reload) | `brew services start mongodb-community` · `make host-deps` · then `cd backend && uv run --env-file ../.env uvicorn app.api.app:app --reload` and `cd frontend && pnpm dev` |
| **All in Docker** | `make dev` (stop: `make down`) |

App http://localhost:5173 · API http://localhost:8000/health

## `.env` essentials

| Key | For |
|---|---|
| `FERNET_KEY` | always — `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `LLM_PROVIDER`, `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY`), `OPENAI_BASE_URL`, `MODEL_CODEGEN`, `MODEL_ROUTING`, `MODEL_PRICING_JSON` | build, tests, repair, benchmarks |
| `MONGODB_URI=mongodb://localhost:27017` | control plane (host mode) |
| `APP_DB_SANDBOX_URI=mongodb://forgeflow-appdb:27017` · `APP_DB_CLUSTER_URI` (`localhost:27018` locally, Atlas to deploy) | generated apps |
| `STITCH_API_KEY` | design (falls back to a fake provider) |
| `VERCEL_TOKEN` | deploy |

Settings saved in `/admin` override `.env` (except `MONGODB_URI`, `FORGEFLOW_META_DB`, `FERNET_KEY`).

## Benchmarks — one command

```bash
make eval                               # everything → eval/results/REPORT.md
make eval ARGS="--apps none --limit 5"  # 5-minute smoke test (~₹1)
```

It runs HumanEval, MBPP, HumanEval-TS and BFCL (~1.5 h), then builds the 10 benchmark apps
(build → tests → repair), 3 at a time (`ARGS="--jobs 4"` for more; ~3 h). Spend is capped at ₹150
for the industry suites and ₹200 per app (`--bench-budget-inr`, `--app-budget-inr`).
`python -m app.eval --help` lists every option.

**Before running, make sure:**

1. Docker and MongoDB are running, and `make sandbox-build` has been run since the last `sandbox/` change.
2. `.env` has the model settings. The eval reads `.env` only; `/admin` settings are ignored. For OpenRouter:
   ```bash
   LLM_PROVIDER=openai
   OPENAI_API_KEY=sk-or-...
   OPENAI_BASE_URL=https://openrouter.ai/api/v1
   OPENROUTER_PROVIDER_ORDER=z-ai        # pin one upstream so every run measures the same deployment
   MODEL_CODEGEN=z-ai/glm-5.3-flash
   MODEL_ROUTING=z-ai/glm-5.3-flash
   MODEL_PRICING_JSON='{"z-ai/glm-5.3-flash": {"input": 13.2, "output": 44.0}}'   # ₹ per 1M tokens
   ```
3. Agent bounds are at their `.env.example` defaults (e.g. `ANTHROPIC_MAX_TOOL_TURNS=250`); a low value
   starves test generation and repair, and the app scores mean nothing.
4. Internet is available on the first run, to download the pinned, checksum-verified datasets.

## Everyday commands

`make test` · `make lint` · `make fmt` · `make help` (full list)

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Sandbox runtime unavailable`, or tests show `test-db-unavailable` | `make sandbox-build` |
| Generated app: `buffering timed out` / `EAI_AGAIN forgeflow-appdb` | `make host-deps` |
| Preview URL refuses to connect / `502` | `make proxy` / start the preview again |
| Docker unavailable (Colima) | `export DOCKER_HOST=unix://$HOME/.colima/default/docker.sock` |
| Deploy refuses: *not reachable from the internet* | set `APP_DB_CLUSTER_URI` to your Atlas URI |
| pnpm `node:sqlite` error | `corepack enable` |
| Settings seem ignored | `.env` must be at the repo root, not `backend/.env` |
