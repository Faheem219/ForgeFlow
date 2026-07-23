# ForgeFlow — Developer Guide

Everything you need to run ForgeFlow locally, drive the hero demo, and read the benchmark results.
For *what* is being built see [`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md); for *how we work* see
[`CLAUDE.md`](./CLAUDE.md); per-phase specs live in [`plans/`](./plans/).

---

## 1. Prerequisites

| Tool | Why | Notes |
|---|---|---|
| **Docker** via **Colima** | `make dev`, sandboxes, deploy | `colima start` before anything Docker-backed |
| **[uv](https://docs.astral.sh/uv/)** | backend (Python 3.11 is fetched by uv) | `uv sync` in `backend/` |
| **Node 20 + pnpm 9** | frontend | pnpm is pinned via corepack — `corepack enable` once. **Do not** use a global pnpm 11 (breaks on Node 20's `node:sqlite`). |

> The whole quality suite (`make test`, `make lint`) runs **without Docker** — only the live stack,
> sandboxes, and deploy need Colima.

---

## 2. First-time setup

```bash
cp .env.example .env          # safe defaults for local; fill in what you need (§5)
make install                  # backend (uv sync) + frontend (pnpm install)
```

Generate the secrets that have no safe default:

```bash
# Credential-vault key (phase-34). Losing/rotating this invalidates every stored provider token.
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# → paste into FERNET_KEY in .env
```

---

## 3. Running the app

### Full stack (recommended)

```bash
make dev        # boots api + web + mongo + proxy via infra/docker-compose.yml (needs Colima)
```

| Surface | URL |
|---|---|
| Web (Vite) | http://localhost:5173 |
| API | http://localhost:8000 · health: http://localhost:8000/health |
| Proxy (Caddy — straddles host + sandbox networks) | http://localhost (:80) |

Then, once (creates indexes + seeds default platform settings):

```bash
make seed
```

Stop / inspect:

```bash
make down       # stop + remove the stack
make logs       # tail all stack logs
```

### Pieces individually (no Docker needed for API+web, but you need a MongoDB)

```bash
# start a throwaway mongo however you like, e.g. via docker: docker run -p 27017:27017 mongo:7
cd backend && uv run uvicorn app.api.app:app --reload      # → :8000
cd frontend && corepack enable && pnpm install && pnpm dev  # → :5173
```

### The generated-app sandbox image

Build the image every generated app runs inside (Node 20 + pnpm + Playwright chromium, non-root):

```bash
make sandbox-build      # docker build -t forgeflow-sandbox:latest sandbox/
make sandbox-smoke      # build + smoke-test it
```

### Web routes worth knowing

| Route | What |
|---|---|
| `/` | project dashboard |
| `/projects/:id` | the workspace (design → … → validate, cost panel, data browser) |
| `/eval` | **evaluation dashboard** (benchmark charts) — nav: "Evaluation" |
| `/admin` | admin dashboard (config, platform cost) — **admin users only** |
| `/settings` | user settings + credential vault |

---

## 4. The hero demo scenario

**Scenario:** [`eval/specs/hero-todo.yaml`](./eval/specs/hero-todo.yaml) — a todo app whose
"Clear completed" bulk action first-pass generation tends to get subtly wrong (it clears *everything*).
That failing test is the setup for the money moment: the **bounded, diff-aware repair loop** reads the
diff, patches just that, and re-tests to green. The full stage-by-stage script is
[`docs/demo-runbook.md`](./docs/demo-runbook.md).

### Seed / reset / status

The demo account and password come from config — **`DEMO_PASSWORD` is required** (the script refuses to
ship a known default). `DEMO_EMAIL` defaults to `demo@forgeflow.dev`.

```bash
export DEMO_PASSWORD=…               # or set it in .env

make demo-seed      # create the demo user + a "Hero Demo" project with hero-todo requirements captured
make demo-status    # show what's currently seeded
make demo-reset     # delete the demo user's projects (cascades stage state, convo, artifacts,
                    #   best-effort reaps each sandbox + app DB); the account is kept
```

`seed` marks requirements **complete**, so a run starts at the interesting part (design → build → the
repair moment → deploy → validate) instead of re-typing a spec. The seed→demo→reset cycle is repeatable.

Then log in as the demo user, open the **Hero Demo** project, and drive:
`design → build → (tests go red) → repair → deploy → validate`.

### Rehearsal smoke (no live infra)

```bash
make demo-rehearse      # cd backend && uv run pytest tests/demo -q
```

This runs the whole hero path headlessly through the **real** conductor/handlers with the model,
sandbox, and live-deploy legs injected as fakes, and asserts every milestone (skeleton instantiated →
genuine first-pass failure → repair fixes it → healthy deploy → live validation delivered). Green means
the hero path's *logic* is intact; the runbook's pre-demo checklist covers the live infrastructure the
test deliberately mocks.

---

## 5. Configuration

Every setting resolves through a **layered config** — precedence **admin panel (DB) > env > code
default**. [`.env.example`](./.env.example) is authoritative and documents every variable; copy it to
`.env` (never commit `.env`). Blank values fall back to the code default.

Highlights you'll likely touch:

| Variable | For |
|---|---|
| `FERNET_KEY` | credential-vault encryption — generate it (§2), back it up |
| `ANTHROPIC_API_KEY` | codegen/repair; blank → those legs can't run |
| `MODEL_CODEGEN` / `MODEL_ROUTING` | Sonnet for real work, Haiku for routing/summaries |
| `BUDGET_CAP_INR_PER_PROJECT` / `_GLOBAL` | spend caps; blank = uncapped |
| `BUDGET_WARN_RATIO` (0.8) | emit a `budget.warning` at this fraction of a cap before halting |
| `DEMO_PASSWORD` | required to seed the demo (§4) |
| `DESIGN_PROVIDER` | `stitch` (default) \| `figma` \| `fake`; blank creds auto-fall-back |
| `VERCEL_TOKEN` / `RENDER_API_KEY` | deploy targets (FE→Vercel, BE→Render — **never** BE→Vercel) |

---

## 6. Benchmark / evaluation harness

This is the project's measured contribution (D13): run the benchmark corpus through the **real**
pipeline headlessly and compare **first-pass vs post-repair** pass rates.

### Run it

```bash
cd backend
uv run python -m app.eval.runner --spec all                       # CI-safe legs (build/test/repair)
uv run python -m app.eval.runner --spec todo-list                 # one spec (or an id prefix)
uv run python -m app.eval.runner --spec todo-list --with-deploy   # also deploy+validate (BILLABLE)
uv run python -m app.eval.runner --spec all --out /path/report.json
```

Specs live in [`eval/specs/*.yaml`](./eval/specs/) (10 seeds incl. `hero-todo`). Deploy/validate are
**off by default** because they create billable provider resources. Each spec gets its own project,
sandbox, and DB, torn down in a `finally`.

### Where results are stored

Each run writes **`eval/results/eval-<timestamp>.json`** — every spec's record (first-pass vs
post-repair pass rate, repair iterations, regressions, tokens/₹, wall clock) plus corpus aggregates.

> These `.json` files are **gitignored generated output** — measurements of a particular run on
> particular hardware with a particular model, not source. See [`eval/results/README.md`](./eval/results/README.md).

### How to view them

**1 — CLI:** the runner prints a per-spec table + headline summary as it finishes.

**2 — Web dashboard:** open **`/eval`** (nav: "Evaluation"). It charts the newest report by default —
first-pass → post-repair dumbbell, distributions, outcomes. *(The API reads files from
`eval/results/`, so run the harness on the same host as the API — with `make dev`, run it from a shell
into the api container or point the API at the same directory.)*

**3 — HTTP API** (any authenticated user; `?source=<filename>` to pick a run, default newest):

| Endpoint | Returns |
|---|---|
| `GET /eval/summary` | aggregates + per-spec records (what the dashboard charts) |
| `GET /eval/runs` | every report on disk, newest first (metadata) |
| `GET /eval/report.csv` | stable, append-only CSV schema (spreadsheet / paper appendix) |
| `GET /eval/report.md` | markdown report (headline table + per-spec) |

The API is **read-only** — it never starts a run, so an HTTP request can't kick off a billable corpus.
Running is always the phase-44 CLI above.

---

## 7. Testing & quality

```bash
make test           # backend pytest + frontend vitest
make test-backend   # cd backend && uv run pytest -q
make test-frontend  # cd frontend && pnpm test -- --run
make lint           # ruff + black --check + mypy  |  eslint + prettier --check + tsc
make fmt            # auto-format both apps
make check-env      # assert no direct os.environ reads outside the config layer
```

Generated-app skeleton (the template every build starts from):

```bash
make skeleton-verify   # install + typecheck/build + unit tests (Vitest + Jest)
```

---

## 8. Make target reference

```
make help          # the authoritative, self-documenting list
```

| Target | Does |
|---|---|
| `dev` / `down` / `logs` | run / stop / tail the local stack |
| `install` | backend + frontend deps |
| `seed` | indexes + default platform settings (run once after first `dev`) |
| `demo-seed` / `demo-reset` / `demo-status` / `demo-rehearse` | hero-demo lifecycle (§4) |
| `test` / `lint` / `fmt` / `check-env` | quality (§7) |
| `sandbox-build` / `sandbox-smoke` | the generated-app sandbox image |
| `sandbox-reap-all` | **DANGER** — remove ALL forgeflow sandboxes + volumes (destroys workspace code) |
| `skeleton-verify` | full check of the generated-app template |

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `make dev` can't reach Docker | `colima start` (Docker via Colima is the runtime) |
| pnpm errors like `ERR_UNKNOWN_BUILTIN_MODULE: node:sqlite` | you're on a global pnpm 11 — use `corepack enable` (pins pnpm 9 for Node 20) |
| Demo won't seed (`refusing to seed`) | set `DEMO_PASSWORD` in the environment / `.env` |
| `'Hero Demo' already present` | `make demo-reset` first, then `make demo-seed` |
| `/eval` shows "no data" | run the harness (§6); the API reads `eval/results/*.json` |
| Build/test can't start a sandbox | `make sandbox-build`; if stale, `make sandbox-reap-all` then retry |
| Design generate errors | it auto-falls-back (Stitch → Figma → `fake`); or set `DESIGN_PROVIDER=fake` |
| Codegen/repair 401 or no-op | set `ANTHROPIC_API_KEY` (and check budget headroom on a project's cost panel) |
