# ForgeFlow — Developer Guide

Everything you need to run ForgeFlow locally, drive the hero demo, and read the benchmark results.
For *what* is being built see [`IMPLEMENTATION_PLAN.md`](./IMPLEMENTATION_PLAN.md); for *how we work* see
[`CLAUDE.md`](./CLAUDE.md); per-phase specs live in [`plans/`](./plans/).

---

## 1. Prerequisites

| Tool | Why | Notes |
|---|---|---|
| **[uv](https://docs.astral.sh/uv/)** | backend (Python 3.11 is fetched by uv) | required · `uv sync` in `backend/` |
| **Node 20 + pnpm 9** | frontend | required · pnpm is pinned via corepack — `corepack enable` once. **Do not** use a global pnpm 11 (breaks on Node 20's `node:sqlite`). |
| **Docker** via **Colima** | the `make dev` stack + sandbox stages | launches the container stack, and runs build/preview/test/repair/deploy — see §1.1 · `colima start` |

### 1.1 What needs Docker — and what doesn't

The ForgeFlow **app** (backend + frontend) runs fine without Docker — you can serve it host-side with
a standalone MongoDB (see "Pieces individually" in §3). Docker is needed to **(a)** launch the
`make dev` container stack, and **(b)** run the stages that execute generated code inside a
per-project sandbox. When the control plane has no reachable daemon, the sandbox stages report
**"Sandbox runtime unavailable"** and everything else works normally.

| Works **without** Docker | **Requires** Docker (a reachable daemon) |
|---|---|
| The web app, auth, projects, chat | **Build** (codegen writes + boots code in the sandbox) |
| Requirements capture & editing | **Preview** (live dev server in the sandbox) |
| Design stage (Stitch/Figma/`fake`) | **Test + self-healing repair loop** |
| Data browser, Eval dashboard (`/eval`) | **Deploy** & **live validation** |
| Admin (`/admin`), cost panel | The hero demo end-to-end (§4) — it drives those stages |
| `make test`, `make lint`, `make seed` | `make sandbox-build`, `make sandbox-smoke` |

> **Note on `make dev`:** the stack boots even when the `api` **container** can't reach a Docker
> daemon — on startup you'll see one INFO line, `sandbox reconcile skipped — Docker unavailable`,
> which is expected, not an error. To actually enable the sandbox stages, wire Docker into the control
> plane (§3.1). Deploy additionally needs `VERCEL_TOKEN` / `RENDER_API_KEY` (§5).

---

## 2. First-time setup — full ordered sequence

Follow these in order. This takes you from a fresh clone to a booted, seeded app.

```bash
# 1. Start the Docker runtime (required for `make dev`, sandboxes, deploy).
colima start                             # verify: docker info

# 2. Enable the pinned pnpm (once per machine). Do NOT use a global pnpm 11.
corepack enable

# 3. Create your .env from the authoritative template.
cp .env.example .env                     # safe local defaults; every var documented in §5

# 4. Generate the one secret with no safe default and paste it into .env → FERNET_KEY=
#    (credential-vault key, phase-34; rotating it invalidates every stored provider token).
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 5. (Optional but recommended) set these in .env:
#      ANTHROPIC_API_KEY=sk-...           # required for the build/repair legs to actually run
#      DEMO_PASSWORD=<something>           # required only if you want the hero demo seed (§4)
#      SEED_ADMIN_EMAIL / SEED_ADMIN_PASSWORD  # to get an /admin login

# 6. Install dependencies (backend uv sync + frontend pnpm install).
make install

# 7. Boot the stack (api + web + mongo + proxy).
make dev                                 # leave running; watch for "Uvicorn running on :8000"

# 8. In a SECOND terminal, create indexes + seed default platform settings (once).
make seed

# 9. Verify.
curl -s localhost:8000/health            # → {"status":"ok",...}
open http://localhost:5173               # the web app
```

> **⚠️ If you copied `.env` before 2026‑07‑23, re-copy it** (`cp .env.example .env`, then re-add your
> `FERNET_KEY`/keys). Older `.env.example` had trailing `# comments` on the same line as values, which
> docker-compose's `env_file` parser folded into the value and crashed the API on boot with a
> `ValidationError` on `budget_cap_inr_*`. Both the template and the config loader are now hardened
> (see §9), but an already-copied `.env` still carries the old inline comments.

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

(First boot only: run `make seed` once in a second terminal — see §2 step 8.)

Stop / inspect:

```bash
make down       # stop + remove the stack
make logs       # tail all stack logs
```

### 3.1 Enabling the sandbox stages (build/test/deploy)

With plain `make dev`, the `api` **container** has no Docker daemon, so sandbox stages stay
unavailable (see §1.1). To turn them on, give the control plane a reachable Docker daemon — either:

- **Option A — mount the host Docker socket into the `api` service.** Keeps the single `make dev`
  workflow. Add one line to the `api` service in [`infra/docker-compose.yml`](./infra/docker-compose.yml):
  ```yaml
      volumes:
        - ../backend:/app
        - /var/run/docker.sock:/var/run/docker.sock   # Colima: if this path is absent on your
                                                       # host, use ${HOME}/.colima/default/docker.sock
  ```
  Then `make down && make dev`. Trade-off: the container gains full host-Docker (≈root) access —
  acceptable for local dev, **not** for anything shared or production.

- **Option B — run the backend on the host** (where Colima's Docker already works), and keep only
  mongo/web/proxy in compose:
  ```bash
  make down
  cd backend && uv run --env-file ../.env uvicorn app.api.app:app --reload --port 8000
  # host Docker (Colima) is reachable → sandboxes work; mongo stays at localhost:27017
  ```
  `--env-file ../.env` matters: `.env` lives at the repo **root**, and a bare `uv run` from `backend/`
  would look for `backend/.env` and load nothing. Cleaner privilege boundary; costs a second terminal.

Either way, build the sandbox image once first: `make sandbox-build`.

### Pieces individually (no Docker needed for API+web, but you need a MongoDB)

`.env` lives at the repo **root**; from `backend/` pass it explicitly with `--env-file ../.env`
(a bare `uv run` there would look for `backend/.env` and load nothing — see §9).

```bash
# start a throwaway mongo however you like, e.g. via docker: docker run -p 27017:27017 mongo:7
cd backend && uv run --env-file ../.env uvicorn app.api.app:app --reload   # → :8000
cd frontend && corepack enable && pnpm install && pnpm dev                 # → :5173
```

> `make` targets (`seed`, `demo-seed`, …) already pass `--env-file` for you — this note is only for
> running `uv run …` by hand.

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

**Every variable in `.env.example` is also editable at runtime from `/admin`** — models, provider and
base URLs, API keys, budgets, sandbox limits, preview commands, repair thresholds, auth limits,
feature flags. An override saved there wins over `.env` and applies immediately (keys flagged
*restart required* apply on the next boot / next sandbox). A test
(`tests/config/test_registry_complete.py`) fails the build if a new `Settings` field is added without
an admin entry and an `.env.example` fallback, so the two can't drift.

- **Secrets** (`*_API_KEY`, `*_TOKEN`, `SECRET_KEY`, …) can be *set* from the panel but never read
  back: they are Fernet-encrypted into the settings store, decrypted only at call time, and shown as
  `••••••`. The audit log records that they changed, never to what.
- **Three keys are env-only** and shown read-only with the reason: `FERNET_KEY` (it encrypts the
  stored secrets — keeping it beside them would defeat that) and `MONGODB_URI` / `FORGEFLOW_META_DB`
  (read while connecting to Mongo, before the admin layer exists).

Highlights you'll likely touch:

| Variable | For |
|---|---|
| `FERNET_KEY` | credential-vault encryption — generate it (§2), back it up |
| `LLM_PROVIDER` | `anthropic` (default) \| `openai` — see §5.1 |
| `ANTHROPIC_API_KEY` | codegen/repair (when provider=anthropic); blank → those legs can't run |
| `MODEL_CODEGEN` / `MODEL_ROUTING` | model ids for real work / routing (provider-appropriate) |
| `BUDGET_CAP_INR_PER_PROJECT` / `_GLOBAL` | spend caps; blank = uncapped |
| `BUDGET_WARN_RATIO` (0.8) | emit a `budget.warning` at this fraction of a cap before halting |
| `DEMO_PASSWORD` | required to seed the demo (§4) |
| `DESIGN_PROVIDER` | `stitch` (default) \| `figma` \| `fake`; blank creds auto-fall-back |
| `VERCEL_TOKEN` / `RENDER_API_KEY` | deploy targets (FE→Vercel, BE→Render — **never** BE→Vercel) |

### 5.1 Running against an OpenAI-compatible provider (phase-53)

The control-plane agents run against Anthropic by default, or **any OpenAI-compatible Chat
Completions endpoint** (OpenAI, Azure OpenAI, Together, Groq, OpenRouter, vLLM, Ollama, …). Switch
with config only — no code changes:

```bash
# 1. Install the optional SDK once:
cd backend && uv sync --extra openai        # (or: pip install openai)

# 2. In .env:
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...                        # or any key the endpoint wants ("ollama" for local)
OPENAI_BASE_URL=                             # blank = api.openai.com; e.g. http://localhost:11434/v1
MODEL_CODEGEN=gpt-4o                         # set to the target provider's model ids
MODEL_ROUTING=gpt-4o-mini
MODEL_PRICING_JSON={"gpt-4o":{"input":210,"output":840}}   # optional; unlisted models cost ₹0
```

Anthropic remains the default and is unchanged if you leave `LLM_PROVIDER` unset. An unknown provider
or a missing `openai` package fails with a clear error rather than silently misbehaving.

The same switch is available without touching `.env`: **`/admin` → Models & provider** holds
`LLM_PROVIDER`, both model ids, `OPENAI_BASE_URL` and the API keys, and the change takes effect on
the next agent call — which is the fastest way to demo a provider swap.

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
first-pass → post-repair dumbbell, distributions, outcomes.

> **⚠️ The dashboard only sees results on the API's own filesystem.** The `/eval` API reads
> `eval/results/*.json` relative to *where the API process runs*. `eval/` lives at the repo root and
> is **not** mounted into the `api` container (only `backend/` is), so:
>
> | You run the API via… | You run the harness on… | Dashboard sees the results? |
> |---|---|---|
> | `make dev` (containerized API) | your **host** terminal | **No** — the container reads its own empty `/eval/results` |
> | **host** (`uv run … uvicorn`) | your **host** terminal | **Yes** — same `eval/results/` |
>
> So if you `make dev` and run `uv run python -m app.eval.runner …` in your terminal, the dashboard
> stays empty. Two fixes:
> - **Recommended — run the backend on the host for eval work** (the harness needs host Docker anyway,
>   §3.1 option B): `cd backend && uv run --env-file ../.env uvicorn app.api.app:app --reload`. Now the
>   harness and the API share `eval/results/`.
> - **Or** mount the results into the container: add `- ../eval:/eval` to the `api` service's `volumes`
>   in [`infra/docker-compose.yml`](./infra/docker-compose.yml), then `make down && make dev`.
>
> (The CSV/MD/API endpoints in option 3 below read the same files, so the same rule applies to them.)

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
| API boot crash: `ValidationError … budget_cap_inr_per_project … unable to parse string as a number` (input like `'# blank = uncapped'` or `''`) | Your `.env` predates the 2026‑07‑23 fix — re-copy it: `cp .env.example .env`, then re-add `FERNET_KEY`/keys. Root cause + fix in §9 note above. |
| Startup log `sandbox reconcile skipped — Docker unavailable`, or a stage errors `Sandbox runtime unavailable` | Expected under plain `make dev` — the `api` container has no Docker daemon. The app is fine; to enable build/test/deploy, wire Docker in via §3.1. Non-sandbox features (§1.1) work regardless. |
| `make dev` can't reach Docker at all (`colima` not running) | `colima start` (Docker via Colima is the host runtime) |
| pnpm errors like `ERR_UNKNOWN_BUILTIN_MODULE: node:sqlite` | you're on a global pnpm 11 — use `corepack enable` (pins pnpm 9 for Node 20) |
| `demo-seed` says `refusing to seed` / `demo-status` says `password UNSET` even though it's set in `.env` | `.env` must be at the repo **root** (not `backend/.env`), and host scripts read it via `--env-file` — update to the current `Makefile` (it passes `--env-file` for you) and `make demo-seed` again. Running the script by hand? add `uv run --env-file ../.env …`. |
| Demo won't seed (`refusing to seed`), `.env` genuinely blank | set `DEMO_PASSWORD=…` in the repo-root `.env` |
| `'Hero Demo' already present` | `make demo-reset` first, then `make demo-seed` |
| `/eval` shows "no data" | run the harness (§6); the API reads `eval/results/*.json` |
| Build/test can't start a sandbox | `make sandbox-build`; if stale, `make sandbox-reap-all` then retry |
| Design generate errors | it auto-falls-back (Stitch → Figma → `fake`); or set `DESIGN_PROVIDER=fake` |
| Codegen/repair 401 or no-op | set `ANTHROPIC_API_KEY` (and check budget headroom on a project's cost panel) |
