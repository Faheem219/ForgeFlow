# CLAUDE.md — ForgeFlow Operating Manual

You are building **ForgeFlow**: an AI-native, human-in-the-loop **PWA** that takes a web-app idea (from screenshots or a structured spec) through **requirements → design → build → test + self-healing repair → deploy → live validation**, letting the user refine, revisit, or skip **any** stage.

**Read `IMPLEMENTATION_PLAN.md` fully before doing anything.** It is the source of truth (context, decisions, architecture, domain model, and the phase roadmap). This file is *how you work*; the plan is *what you build*; `plans/phase-XX-*.md` are the detailed specs.

---

## Golden rules (non-negotiable)

1. **Follow the locked decisions (Section 1 of the plan).** Do not silently change the stack, architecture, or approach. If a task seems to require deviating, **stop and ask.**
2. **Fixed generated-app stack:** every app ForgeFlow *generates* is **React (Vite+TS) + Node/Express (TS) + MongoDB (Mongoose)**, tested with **Vitest/Jest + Playwright**. Never introduce another stack for generated apps.
3. **Untrusted code runs only in sandboxes.** Never `exec` generated code in the control plane. Sandboxes have no host network, dropped caps, and CPU/mem/pid limits.
4. **Never hardcode or log secrets.** All config from env; provider tokens encrypted at rest; decrypted only at call time; never returned to the client.
5. **The repair loop is always bounded.** Diff-aware context, max iterations, no-progress detection, human escalation. Never build an unbounded fix loop.
6. **Non-linearity is an invariant.** No stage is mandatory. Downstream stages must tolerate missing/stale upstream artifacts. Only hard prereqs: deploy⇐build, validate⇐deploy.
7. **Cost discipline.** Use `MODEL_ROUTING` (Haiku) for classification/summaries/routing; `MODEL_CODEGEN` (Sonnet) only for real code/test/repair work. Record token usage per project; respect budget caps.
8. **Ship phases independently.** Each phase must build, pass its tests, and leave the app runnable. No half-finished phases.

---

## Tech stack

- **Control plane:** FastAPI (Python 3.11, `uv`), Pydantic v2, Motor + Beanie (MongoDB), `anthropic` SDK, `docker` SDK, WebSockets, `httpx`, `cryptography`, `pytest`.
- **Frontend:** React 18 + Vite + TS + Tailwind (semantic tokens, light+dark, indigo/violet), Zustand, TanStack Query, `@monaco-editor/react`, `xterm.js`, `@xyflow/react` (React Flow), `vite-plugin-pwa`; UI-revamp additions (authorized): `framer-motion`, `react-resizable-panels`, `lucide-react`, `clsx`+`tailwind-merge`, `@fontsource` Inter/JetBrains Mono.
- **Generated apps (fixed):** React+Vite+TS / Express+TS / Mongoose / Zod / Vitest / Jest+supertest / Playwright.
- **Sandbox image:** Node 20 + pnpm + Playwright + git, non-root.
- **Deploy:** Vercel for **both** the SPA frontend and the Node backend (a function built from an inline source upload — no git host in the path), Atlas for the DB. Render stays available behind `DEPLOY_BE_PROVIDER=render`. *(D11 amended 2026-08-23, phase-58 — the earlier rule "BE is never deployed to Vercel" no longer applies.)*
- **Models:** `MODEL_CODEGEN=claude-sonnet-5`, `MODEL_ROUTING=claude-haiku-4-5-20251001` (env-configurable).

## Repository structure

See Section 5 of the plan. Backend under `backend/app/**` (core, db, auth, projects, orchestrator, agents, sandbox, design, testing, deploy, data_browser, realtime, eval, api). Frontend under `frontend/src/**` (app, lib, components, features/*). Also `templates/app-skeleton/`, `sandbox/`, `infra/`, `eval/`, `plans/`.

## Environment & commands

- **Local runtime:** Docker via **Colima**. Bring up with `make dev` (API + web + Mongo + proxy via `infra/docker-compose.yml`).
- **Backend:** `uv sync`; run `uv run uvicorn app.api.app:app --reload`; tests `uv run pytest`; quality `uv run ruff check . && uv run black --check . && uv run mypy .`.
- **Frontend:** `pnpm i`; `pnpm dev`; tests `pnpm test`; `pnpm lint`.
- **Sandbox image:** `docker build -t forgeflow-sandbox:latest sandbox/`.
- Never commit `.env`; keep `.env.example` authoritative (plan Section 13).

## Coding conventions

- **Python:** full type hints, Pydantic models at boundaries, async I/O, small focused modules, `ruff`+`black`+`mypy` clean. No bare `except`; use the error taxonomy (`UserError`/`SystemError`/`ProviderError`).
- **TypeScript:** `strict` on, no `any` (justify if unavoidable), functional components + hooks, colocate feature code under `features/*`, ESLint+Prettier clean.
- **APIs:** typed request/response schemas; version nothing prematurely; consistent error envelope.
- **Realtime:** typed events (`event`, `project_id`, `stage`, `payload`, `seq`); long tasks emit incremental, resumable events.
- **Data:** never overwrite artifacts — version them. Respect per-project DB isolation.

## Testing conventions

- **Control plane:** `pytest` (+`pytest-asyncio`); mock external providers (Anthropic, Stitch, Vercel, Render); use a disposable test Mongo DB.
- **Frontend:** Vitest + Testing Library for units; keep components testable.
- **Generated apps:** Vitest/Jest for units, Playwright for E2E; E2E runs against the sandbox preview (dev) and the live URL (validation).
- A phase is **not done** until its acceptance criteria (in `plans/phase-XX`) are met with passing tests.

## Git conventions

- Conventional Commits (`feat:`, `fix:`, `chore:`, `test:`, `docs:`, `refactor:`).
- One coherent phase per branch/PR where practical; small, reviewable commits.
- The generated-app **workspace** is git-backed inside the sandbox (needed for diff-aware repair).

## Phase workflow (how you operate)

1. On kickoff (see the startup prompt), **generate `plans/`** — one detailed spec per phase in the roadmap — then stop.
2. When the user says **"execute phase N"**:
   - Read `plans/phase-NN-*.md` and its dependencies; confirm prereqs are done.
   - Implement it end-to-end (code + tests) following these conventions and the plan.
   - Run lint/type/tests; ensure the app still boots.
   - Update `plans/README.md` status (⬜ todo → 🔄 in progress → ✅ done) and note anything deferred.
   - Commit with a conventional message referencing the phase.
3. If a phase's acceptance criteria are ambiguous or conflict with the plan, **ask before coding.**
4. Never expand scope beyond the phase. Never start the next phase unprompted.

## Domain glossary

- **Project** — a user's app-in-progress; owns stage states, conversation, artifacts, a sandbox, and a per-project DB.
- **Stage** — one of requirements/design/build/test/deploy/validate; each has an independent `StageState` (enables non-linear jumps/skips). That is the **product** order (D15, 2026-08-06) — it drives the nav, the default entry stage and the staleness cascade (`STAGE_ORDER`), and gates nothing. The `Stage` enum's own declaration order is unrelated and still lists `design` first; nothing depends on it.
- **Conductor** — the orchestrator that routes user intents to stage handlers and owns transition legality.
- **Artifact** — a versioned output (design, code change, requirement, test, test result, repair attempt, deployment, infra plan).
- **Sandbox** — the per-project Docker container where generated code lives, runs, and is tested.
- **DesignProvider** — pluggable design backend (Stitch default, Figma alt).
- **RepairAttempt / Loop** — one diff-aware patch iteration / the bounded controller around it.
- **DeploymentPlan** — the infra analyzer's classification + targets for a project.
- **Run** — a costed, traced unit of agent work (used by observability + the eval harness).

## Do / Don't

- **Do** keep the demo hero-path (screenshots → deployed URL) working as features land.
- **Do** fail soft on provider errors (esp. Stitch quota) with clear user messaging + fallback.
- **Do** measure the repair loop (iterations, convergence, cost) — it's the contribution.
- **Don't** add extra generated-app stacks, multiplayer, payments, or non-web targets.
- **Don't** bypass the sandbox, leak secrets, or build unbounded loops.
- **Don't** mark a phase done without meeting its acceptance criteria.
