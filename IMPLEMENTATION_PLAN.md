# ForgeFlow — Implementation Plan

> **ForgeFlow** is an AI-native, human-in-the-loop Progressive Web App that carries a web-app idea — from screenshots or a structured spec — through automated UI design, full-stack code generation, requirement-driven testing with a **bounded self-healing repair loop**, and one-click cloud deployment, while letting the user refine, revisit, or skip **any** stage at will.

This document is the **single source of truth** for the build. `CLAUDE.md` is the operating manual; the files under `plans/` are the per-phase execution specs generated from Section 10 of this document.

---

## 1. Context & Locked Decisions

These were decided deliberately. Do **not** silently change them; if a phase forces a reconsideration, stop and flag it.

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Positioning:** the full pipeline is the demo, but the *defensible contribution* is the **bounded, requirements-derived self-healing test loop** inside a **fully non-linear, human-in-the-loop** orchestration. Secondary differentiator: **infra-aware dual-mode deployment** with an n8n-style live topology. | The app-builder space (Lovable/Bolt/Replit/v0) is saturated on "prompt → app → deploy" but weak on reliable verification, live-site validation, and beginner-safe infra. We compete where they are weak, not on codegen polish. |
| D2 | **Generated-app stack is FIXED:** React (Vite + TS) frontend, Node/Express (TS) backend, MongoDB (Mongoose). | One well-trodden stack ⇒ higher first-pass generation success ⇒ fewer repair loops ⇒ a tractable, testable target in the project timeline. |
| D3 | **Platform control-plane stack:** **FastAPI (Python 3.11)** backend + **React (Vite + TS + Tailwind)** PWA frontend. | Aligns with existing FastAPI expertise; Python is strong for the agent loop, Docker orchestration, and async I/O. Cleanly separate from the Node generated apps. |
| D4 | **Codegen engine:** a custom **agentic tool-use loop on Claude** via the Anthropic API. Workhorse = `claude-sonnet-5`; cheap routing/classification/summaries = `claude-haiku-4-5-20251001`. All model IDs env-configurable. | Budget-appropriate (₹6k for dev + demo). Sonnet for code, Haiku for the many small calls keeps cost down. |
| D5 | **Execution + IDE:** isolated **per-project Docker sandbox**; in-browser **Monaco** IDE with live code streaming, an **xterm.js** terminal, and a proxied **live preview**. Local dev via **Colima + Docker**. | Full control, supports Playwright, supports Python-free Node runtime for generated apps, cheap (just a VM). |
| D6 | **Requirements → tests:** a **structured, fill-in-the-blanks requirements guide** per feature compiles into **unit + Playwright E2E** tests. | Structured input ⇒ deterministic, evaluable test generation; easy for a technical-ish user. |
| D7 | **Repair loop:** **diff-aware** (feed only failing test output + minimal code-diff context), **bounded retries**, **no-progress detection**, **human escalation** on cap. | This is the crown-jewel contribution and the fix for the "infinite bug-loop / credit burn" failure mode of incumbents. |
| D8 | **Database:** one platform **MongoDB Atlas** cluster. Control-plane metadata in `forgeflow_meta`; **one database per user-project** for generated-app data. Users may supply their own URI. A **project-scoped CRUD data browser** ships in-app. | Single DB technology, cheap (M0 free tier), per-project isolation, zero DB setup for the user. |
| D9 | **Design stage is PLUGGABLE:** a `DesignProvider` interface; **Stitch MCP** default (image→UI + text→UI + fetch HTML/CSS), **Figma MCP** alternative. | Avoids lock-in and hedges Stitch's quota/auth constraints (see D10). |
| D10 | **Stitch constraints acknowledged:** requires a GCP project with billing + OAuth/service-account auth; ~350 generations/month free; access tokens are short-lived. The **platform** holds credentials centrally; provider layer must handle token refresh, quota exhaustion, and graceful fallback. | Multi-user quota is a real risk; the adapter must fail soft (queue / fallback provider / clear user message). |
| D11 | **Deployment is DUAL-MODE:** **seamless** on platform-owned accounts (Vercel FE + Render BE + shared Atlas) **or** **bring-your-own** tokens. FE→Vercel (SPA), BE→Render (persistent Node service). A **React Flow** topology shows FE/BE/DB nodes with live status/logs. | Vercel alone can't host a persistent Node backend + Mongo; Render covers the BE. Beginner-safe by default, power-user friendly when needed. |
| D12 | **Non-linear orchestration invariant:** **no stage is mandatory.** Users can enter at any stage, refine any output conversationally, jump back, or skip forward. | Core product promise; every stage handler and UI must honor it. |
| D13 | **Evaluation harness** is a first-class deliverable: benchmark of app specs measuring first-pass vs post-repair pass rate, repair-iteration count, and screenshot→URL time. | Turns the project into publishable/pitchable evidence. |
| D14 | **The platform itself is a PWA** (installable, offline app shell). It builds regular web apps (not PWAs, not mobile/native). | Per scope. |
| D15 | **Stage order is requirements-first** (`requirements → design → build → test → deploy → validate`). Supersedes the design-first ordering used up to 2026-08-06. | You should not design a UI before you know what the app has to do. See the decision note below. |

### Decision log

**2026-08-06 — stage roadmap reordered from design-first to requirements-first (D15).**
Per product decision, requirements now precedes design *everywhere*: the stage navigator, the default
entry stage of a new project, and the staleness-cascade order (refining requirements now marks design
and everything after it `stale`; refining design no longer touches requirements). Design additionally
consumes the finalized requirements as extra context in the design-provider prompt when a
`RequirementSpec` exists, and remains fully functional — byte-for-byte unchanged — without one. A
whole-spec AI draft (freeform description → proposed feature list, `MODEL_ROUTING`/Haiku, never
auto-saved) was added alongside the existing manual form and per-feature suggest.
**The non-linearity invariant (D12) is unchanged — no new hard prerequisites.** `deploy ⇐ build` and
`validate ⇐ deploy` remain the only mandatory ordering; requirements ⇒ design is a soft nudge in the
UI, never a gate. Phase files 16–19 (design) and 25–26 (requirements) are **not** renumbered — phase
numbering records build order, not product order (see `plans/README.md`).

---

## 2. Product Scope

**In scope (v1):**
- Auth, projects dashboard, per-project workspace.
- Six pipeline stages (each optional): Requirements → Design → Build → Test+Repair → Deploy → Live Validation.
- Web IDE (Monaco + terminal + live preview) over per-project Docker sandboxes.
- Pluggable design (Stitch/Figma) from screenshots or text.
- Agentic generation of the fixed stack; conversational change requests.
- Structured requirements → generated unit + E2E tests; bounded self-healing repair.
- Infra analysis + dual-mode deploy + visual topology.
- Post-deploy validation with repair path.
- Per-project database with CRUD data browser.
- Evaluation harness + results view.

**Out of scope (v1):**
- Non-web targets (mobile/native/desktop), multiple generated-app stacks, payments/billing in *generated* apps, team collaboration/multiplayer, enterprise SSO/compliance, marketplace of templates. (Design for extensibility but do not build.)

---

## 3. System Architecture

Three planes plus external providers. (See `forgeflow_user_flow.drawio` for the end-to-end user flow.)

1. **Control plane — FastAPI (`backend/`)**
   - Auth, projects, orchestrator (the "conductor"), stage handlers, agents, realtime hub, deploy, data-browser, eval.
   - State + metadata in MongoDB `forgeflow_meta`.
2. **Execution plane — per-project Docker sandboxes (`sandbox/`)**
   - One container per active project workspace. Hosts the generated app's source, runs dev servers, installs deps, runs tests (incl. Playwright). Reached by the control plane via a sandbox manager (docker SDK) and by the user via WS-bridged terminal + a reverse-proxied preview.
3. **Presentation plane — React PWA (`frontend/`)**
   - Dashboard, workspace shell, stage panels, Monaco IDE, terminal, React Flow deployment topology, data browser.
4. **External providers**
   - **Anthropic API** (codegen/test-gen/repair agents).
   - **Design MCPs** (Stitch default, Figma alt).
   - **Deploy providers** (Vercel, Render) — platform-owned or BYO creds.
   - **MongoDB Atlas** (control-plane + per-project app DBs).

**Realtime:** a WebSocket hub streams events (agent tokens, file writes, terminal output, test results, deploy status) from control plane → frontend. Everything long-running is event-driven and resumable.

---

## 4. Tech Stack

**Platform frontend:** React 18 + Vite + TypeScript + Tailwind CSS (semantic token theming, light + dark, indigo/violet accents), React Router, Zustand (state), TanStack Query (server state), `@monaco-editor/react` (IDE), `xterm.js` (terminal), `@xyflow/react` (React Flow topology), `vite-plugin-pwa`/Workbox (PWA), native WebSocket client. UI-revamp additions (authorized 2026-08-04): `framer-motion` (micro-interactions), `react-resizable-panels` (draggable splitters), `lucide-react` (icons), `clsx`+`tailwind-merge` (class composition), `@fontsource-variable/inter` + `@fontsource/jetbrains-mono` (self-hosted fonts).

**Platform backend:** FastAPI + Uvicorn, Python 3.11, `uv` (env/deps), Pydantic v2 (schemas/settings), Motor + Beanie (MongoDB ODM), `anthropic` Python SDK (agents; optionally the Claude Agent SDK), `docker` SDK (sandbox), `websockets`/FastAPI WS, `httpx` (providers), `cryptography` (secret encryption), `python-jose`/`passlib[bcrypt]` (auth), `pytest`+`pytest-asyncio` (tests), `ruff`+`black`+`mypy` (quality).

**Generated-app stack (fixed):** React 18 + Vite + TS (FE); Node + Express + TS (BE); MongoDB + Mongoose; Zod (validation); Vitest + Testing Library (FE unit); Jest + supertest (BE unit); **Playwright** (E2E). Shipped as an opinionated `templates/app-skeleton/`.

**Sandbox image:** Node 20 + pnpm + Playwright browsers + git + curl, non-root user, no host network, CPU/memory/pids caps.

**Deploy:** Vercel (SPA FE), Render (Node web service BE); reverse proxy (Caddy or Traefik) for preview subdomains in prod.

**Infra/local:** Docker via Colima; `infra/docker-compose.yml` for backend + Mongo (or Atlas) + proxy.

---

## 5. Repository Structure

```
forgeflow/
  backend/                       # FastAPI control plane (uv, pyproject.toml)
    app/
      core/          config.py, security.py, logging.py, events.py, errors.py
      db/            mongo.py (client), models/ (Beanie docs), repos/
      auth/          router.py, service.py, deps.py
      projects/      models.py, service.py, state_machine.py, router.py
      orchestrator/  conductor.py, stages/ (design.py, build.py, requirements.py, test.py, repair.py, deploy.py, validate.py)
      agents/        anthropic_client.py, models.py (routing), tools/ (fs, exec, tests, design), codegen.py, testgen.py, repair.py, prompts/
      sandbox/       manager.py, fs.py, exec.py, preview.py, image/
      design/        base.py (DesignProvider), stitch.py, figma.py, registry.py
      testing/       runner.py, parsers.py (junit/json), models.py
      deploy/        analyzer.py, providers/ (vercel.py, render.py), db_provision.py, orchestrate.py, secrets.py
      data_browser/  router.py, service.py
      realtime/      hub.py, router.py (WS), schemas.py
      eval/          runner.py, specs_loader.py, metrics.py, report.py
      api/           app.py (FastAPI factory), routers wiring
    tests/
  frontend/                      # React + Vite + TS PWA (pnpm)
    src/
      app/           router.tsx, layout/, theme/
      lib/           apiClient.ts, wsClient.ts, stores/, types/
      components/    ui/ (buttons, panels, modals, toasts)
      features/
        auth/  dashboard/  workspace/  design/  ide/  build/
        requirements/  testing/  deploy/  data/  eval/
    public/  (manifest, icons, offline shell)
  templates/
    app-skeleton/                # fixed-stack generated-app skeleton (FE + BE + tests)
  sandbox/
    Dockerfile  entrypoint.sh
  infra/
    docker-compose.yml  caddy/ (or traefik/)
  eval/
    specs/                       # benchmark app specs
  plans/                         # per-phase execution specs (generated)
    README.md  PHASE_TEMPLATE.md  phase-XX-*.md
  CLAUDE.md
  IMPLEMENTATION_PLAN.md
  .env.example
  README.md
```

---

## 6. Core Domain Model (MongoDB `forgeflow_meta`)

- **User** — id, email, hashed_password, created_at, settings.
- **Credential** — id, user_id, kind (`vercel|render|mongo_uri|stitch|figma`), encrypted_secret, scope (`platform|byo`), created_at. (Encrypted at rest.)
- **Project** — id, user_id, name, current_stage, status, stack (`fixed-mern-ts`), sandbox_id?, app_db_name, created_at, updated_at.
- **StageState** — project_id, stage, status (`empty|in_progress|awaiting_user|complete|skipped`), artifacts[], updated_at. (One per stage; enables non-linear jumps.)
- **Message** — project_id, stage?, role (`user|assistant|system|tool`), content, artifacts[], token_usage, created_at. (Conversation history, per project & stage.)
- **Artifact** — id, project_id, stage, type (`design|code_change|requirement|test|test_result|repair_attempt|deployment|infra_plan`), version, ref (blob/db pointer), meta, created_at. (Versioned; supports "revisit/refine".)
- **RequirementSpec** — project_id, features[] where each feature = { name, description, inputs[], expected_behaviors[], acceptance_criteria[] } (the fill-in-the-blanks schema).
- **TestSuite** — project_id, kind (`unit|e2e`), files[], generated_from (requirement refs), version.
- **TestRun** — project_id, suite refs, results (pass/fail per test), failures[], stdout_ref, created_at.
- **RepairAttempt** — project_id, run_id, iteration, target_files[], diff_ref, resulting_run_id, outcome (`fixed|no_progress|regressed`), created_at.
- **Deployment** — project_id, mode (`seamless|byo`), fe_target, be_target, db_target, urls{}, status, topology_snapshot, logs_ref, created_at.
- **Run/Trace** — project_id, kind, cost (tokens/₹), started_at, finished_at, events_ref. (Observability + eval.)

---

## 7. Cross-Cutting Conventions

- **Config:** all config via Pydantic Settings from env; nothing hardcoded. `.env.example` enumerates every var (Section 13).
- **Secrets:** provider tokens encrypted at rest (Fernet/`cryptography`); decrypted only in-process at call time; never logged, never returned to the client.
- **Realtime events:** typed event schema (`event`, `project_id`, `stage`, `payload`, `seq`). Frontend subscribes per project. Long tasks emit incremental events and are resumable from `seq`.
- **Errors:** a single error taxonomy (`UserError` vs `SystemError` vs `ProviderError`); user-facing errors are actionable; provider failures degrade gracefully.
- **Cost control:** every agent call records token usage against the project; per-project and global budget caps (env) halt runs with a clear message.
- **Idempotency:** stage handlers are re-runnable; artifacts are versioned rather than overwritten.
- **Security posture:** generated (untrusted) code runs **only** inside sandboxes; sandboxes have no host network, dropped caps, and resource limits; the control plane never `exec`s generated code directly.

---

## 8. The Non-Linear Orchestration Model

- **Stages:** `requirements → design → build → test → deploy → validate`, plus an implicit `idle/dashboard`. This order drives presentation and the staleness cascade only — it is **not** a required sequence (see D15).
- **State machine (`projects/state_machine.py`):** each project holds independent `StageState` per stage. Transitions are **not** forced to be sequential:
  - **Enter anywhere:** user may start at `build` (own designs) or even later.
  - **Skip:** any stage may be marked `skipped`; downstream stages must tolerate missing upstream artifacts (e.g., test-gen warns if no requirements exist and offers to capture them or infer minimal ones).
  - **Jump back / refine:** re-entering a completed stage opens a new conversation round; new artifacts are new versions; downstream stages are marked `stale` (not deleted) and can be re-run.
  - **Guardrails:** deploy requires a build; live-validation requires a deployment. These are the only hard prerequisites — everything else is optional.
- **Conductor (`orchestrator/conductor.py`):** receives a user intent (message + target stage), routes to the stage handler, persists messages/artifacts, emits events. The conductor — not the UI — owns transition legality.
- **UI contract:** the workspace shows a stage navigator where any stage is clickable; each stage panel exposes "refine (chat)", "skip", and "proceed" affordances.

---

## 9. The Self-Healing Repair Loop (crown-jewel spec)

Implemented across Epic 6; referenced by Build (Epic 4), Test (Epic 5), and Live Validation (Epic 8).

**Inputs:** a `TestRun` with ≥1 failure; the current workspace; the requirement/test that failed.

**Algorithm:**
1. **Analyze failure** (`agents/repair.py` + `testing/parsers.py`): parse structured results → for each failing test capture (test name, assertion, stack, referenced source files). Build a **minimal repair context**: only the failing test source, the specific files it exercises, and a **diff of what changed since the last passing run** (git-backed workspace).
2. **Targeted patch:** call the repair agent with *only* that minimal context (not the whole repo) + a strict instruction to fix the failing behavior without breaking passing tests. Apply patch to the sandbox workspace; commit.
3. **Re-run** the affected suite (fast path) then the full suite.
4. **Loop controller (`orchestrator/stages/repair.py`):**
   - `max_iterations` (env, default 5).
   - **No-progress detection:** stop if the failing-test set does not shrink for `stall_threshold` (default 2) consecutive iterations, or if a patch introduces regressions twice.
   - **Convergence metrics:** track `failing_count` per iteration, `regressions_introduced`, `tokens_spent`.
   - **Human escalation:** on cap/stall, surface the failing tests, the diffs tried, and a concise "here's where I'm stuck" summary; ask the user for guidance; resume with their input (which re-enters Build).
5. **Record:** each iteration is a `RepairAttempt`; the whole loop is a `Run` with cost + outcome — this is what the **evaluation harness** measures.

**Invariants:** never feed the whole repo when a diff suffices; never loop unbounded; always leave an auditable trail; always prefer escalation over silent failure.

---

## 10. Phase Roadmap

Phases are grouped into epics and are designed to be **independently executable and shippable**. Each phase below is the seed for a detailed `plans/phase-XX-*.md`. Numbering is stable; the agent expands each into file-level steps.

> Format per phase — **Goal**, **Depends on**, **Tasks**, **Deliverables**, **Acceptance**.

### Epic 0 — Foundations & Scaffolding

**Phase 01 — Repo, tooling & local dev**
- **Goal:** Monorepo skeleton with backend/frontend/templates/sandbox/infra, quality tooling, and one-command local startup on Colima/Docker.
- **Depends on:** —
- **Tasks:** init repo; `backend/` (uv, pyproject, ruff/black/mypy, FastAPI hello route); `frontend/` (Vite+TS+Tailwind, base theme, lint/prettier); `infra/docker-compose.yml` (backend + Mongo + Caddy); Makefile/taskfile (`make dev`, `make test`, `make lint`); `.env.example`; pre-commit hooks; README.
- **Deliverables:** running skeleton, `make dev` boots API + web + Mongo.
- **Acceptance:** health endpoint returns 200; web shell loads; lint/format/type checks pass in CI.

**Phase 02 — Control-plane data layer**
- **Goal:** MongoDB connection + Beanie models + repositories for the Section 6 domain model.
- **Depends on:** 01
- **Tasks:** Mongo client (Motor/Beanie), `forgeflow_meta` init, all entity models, repository classes, indexes, seed/migration script.
- **Deliverables:** typed models + repos + tests.
- **Acceptance:** CRUD unit tests pass against a test DB; indexes created.

**Phase 03 — Auth & user management**
- **Goal:** email/password auth, JWT sessions, protected routes.
- **Depends on:** 02
- **Tasks:** register/login/logout, password hashing (bcrypt), JWT issue/verify, FastAPI deps for current user, rate limiting, frontend auth screens + token handling + guarded routes.
- **Deliverables:** working auth end-to-end.
- **Acceptance:** unauth requests 401; login persists session; e2e login works.

**Phase 04 — Frontend app shell & PWA base**
- **Goal:** App layout, routing, theme, and installable PWA baseline.
- **Depends on:** 01
- **Tasks:** router + layout (sidebar/topbar), indigo/violet dark theme + tokens, base UI components (button/panel/modal/toast), `vite-plugin-pwa` manifest + icons + offline shell + service worker.
- **Deliverables:** navigable shell; installable PWA.
- **Acceptance:** Lighthouse PWA installable; routes render; theme consistent.

**Phase 05 — Realtime event hub**
- **Goal:** WebSocket infrastructure for streaming events with resumability.
- **Depends on:** 03
- **Tasks:** backend WS hub (per-project channels, auth on connect, `seq` ordering, replay from `seq`), typed event schema, frontend WS client + reconnect + store integration.
- **Deliverables:** end-to-end event stream demo (ping/echo + a sample progress stream).
- **Acceptance:** events arrive ordered; reconnect resumes without loss.

### Epic 1 — Project & Orchestration Core

**Phase 06 — Project lifecycle & stage state machine**
- **Goal:** Create/list/open projects; implement the non-linear stage state machine (Section 8).
- **Depends on:** 02, 03
- **Tasks:** project CRUD; `StageState` management; transition legality (enter-anywhere, skip, jump-back marks downstream stale, hard prereqs deploy⇐build, validate⇐deploy); events on transitions.
- **Deliverables:** project + stage APIs + state-machine tests.
- **Acceptance:** all transition rules covered by tests incl. skip/jump/stale.

**Phase 07 — Conversation & artifact model**
- **Goal:** Per-project/per-stage messages and versioned artifacts.
- **Depends on:** 06
- **Tasks:** message store, artifact store with versioning + retrieval, blob storage strategy (GridFS or filesystem+ref), token-usage capture.
- **Deliverables:** conversation + artifact APIs + tests.
- **Acceptance:** artifacts versioned, retrievable; history queryable per stage.

**Phase 08 — Conductor (orchestrator)**
- **Goal:** Central router from user intent → stage handler; owns transitions and event emission.
- **Depends on:** 05, 06, 07
- **Tasks:** conductor service; stage-handler interface; intent schema (message + target stage + action `refine|proceed|skip`); wire a no-op handler per stage; cost accounting hook.
- **Deliverables:** conductor + handler contracts + tests with stub handlers.
- **Acceptance:** intents route correctly; illegal transitions rejected; events emitted.

**Phase 09 — Workspace UI shell**
- **Goal:** The project workspace: stage navigator + chat panel + artifact/preview panels, honoring non-linearity.
- **Depends on:** 04, 05, 08
- **Tasks:** workspace layout; clickable stage navigator with statuses; chat panel bound to conductor; per-stage panel scaffolds with refine/skip/proceed; live event rendering.
- **Deliverables:** navigable workspace driving the stub conductor.
- **Acceptance:** user can move between stages, send intents, see streamed responses; skip/jump reflected in UI.

### Epic 2 — Sandbox & Web IDE

**Phase 10 — Sandbox base image**
- **Goal:** Secure Docker image for generated apps (Node 20 + pnpm + Playwright + git).
- **Depends on:** 01
- **Tasks:** `sandbox/Dockerfile` (non-root, Playwright deps), entrypoint, size/security hardening; build + publish locally.
- **Deliverables:** buildable image + smoke test.
- **Acceptance:** container runs a sample Node app + Playwright test.

**Phase 11 — Sandbox manager**
- **Goal:** Per-project container lifecycle via docker SDK.
- **Depends on:** 06, 10
- **Tasks:** create/start/stop/destroy; per-project workspace volume; resource caps (CPU/mem/pids), no host net, timeouts; map project ↔ container; idle reaping.
- **Deliverables:** sandbox manager + tests.
- **Acceptance:** projects get isolated containers; limits enforced; cleanup works.

**Phase 12 — Workspace filesystem service**
- **Goal:** Read/write/list workspace files via API; git-backed workspace (for diffs).
- **Depends on:** 11
- **Tasks:** file tree, read/write, safe path handling, `git init` + commit helpers (used by repair diffs).
- **Deliverables:** FS API + tests.
- **Acceptance:** files round-trip; git commits recorded; path traversal blocked.

**Phase 13 — Command execution & terminal**
- **Goal:** Run commands in the sandbox; stream a real terminal to the browser.
- **Depends on:** 05, 11
- **Tasks:** exec API (stream stdout/err, exit codes, timeouts, cancel); WS bridge to a shell; frontend xterm.js terminal.
- **Deliverables:** working web terminal + command runner.
- **Acceptance:** interactive terminal works; long commands stream + cancel cleanly.

**Phase 14 — Web IDE (Monaco) + live code streaming**
- **Goal:** In-browser editor bound to the workspace; render code as the agent writes it.
- **Depends on:** 09, 12
- **Tasks:** Monaco integration, file tree, tabs, dirty-state, save; subscribe to agent file-write events to stream code live (read-only "generating…" mode) then hand back editing.
- **Deliverables:** functional IDE with live-generation view.
- **Acceptance:** edits persist to sandbox; live streaming shows files appearing/updating.

**Phase 15 — Live preview**
- **Goal:** Run the generated app in its sandbox and preview it in-app.
- **Depends on:** 11, 13
- **Tasks:** start dev servers (Vite FE, Express BE) in container; reverse-proxy a preview URL (Caddy/Traefik subdomain in prod, port map locally); preview pane + refresh + logs.
- **Deliverables:** live preview pane.
- **Acceptance:** a scaffolded app renders in the preview; BE reachable from FE.

### Epic 3 — Design Stage (pluggable)

**Phase 16 — DesignProvider interface & registry**
- **Goal:** Provider-agnostic design abstraction.
- **Depends on:** 08
- **Tasks:** `DesignProvider` base (`generate_from_image`, `generate_from_text`, `fetch_code`, `refine`), registry + selection, artifact mapping to `design` artifacts.
- **Deliverables:** interface + registry + a fake provider for tests.
- **Acceptance:** stage handler works against the fake provider end-to-end.

**Phase 17 — Stitch MCP provider**
- **Goal:** Real Stitch integration with robust auth/quota handling.
- **Depends on:** 16
- **Tasks:** MCP client to Stitch (image→UI, text→UI, fetch HTML/CSS); centralized credential + token-refresh; quota tracking + exhaustion handling; error/backoff; map returned HTML/CSS into design artifacts.
- **Deliverables:** Stitch provider + integration tests (mocked) + a live smoke path.
- **Acceptance:** screenshot → design artifact; token refresh + quota-exhaustion handled gracefully (fallback/queue/clear message).

**Phase 18 — Figma MCP provider (alternative)**
- **Goal:** Second provider proving pluggability.
- **Depends on:** 16
- **Tasks:** Figma MCP adapter to the same interface; provider switch in settings.
- **Deliverables:** Figma provider + tests.
- **Acceptance:** switching providers requires no stage-handler change.

**Phase 19 — Design stage handler & UI**
- **Goal:** Full design stage: intake, generate, iterative refine, approve/skip/bring-your-own.
- **Depends on:** 09, 14, 16, 17
- **Tasks:** handler (intake screenshots/text/own designs → generate → store); refine loop (chat → regenerate, versioned); frontend upload + design preview + chat-refine + approve/skip; import-own-design path.
- **Deliverables:** working design stage.
- **Acceptance:** upload → preview → refine ×N → approve; skip and bring-your-own both work; artifacts versioned.

### Epic 4 — Build Stage (agentic codegen)

**Phase 20 — Anthropic client & model routing**
- **Goal:** Robust Claude client with routing, streaming, retries, cost tracking.
- **Depends on:** 07
- **Tasks:** `anthropic_client.py` (Messages API, tool use, streaming); model router (Sonnet for codegen, Haiku for classify/summarize; env-configurable IDs); retry/backoff; per-call token accounting → Run/cost; budget guardrails.
- **Deliverables:** client + router + tests (mocked).
- **Acceptance:** streamed tool-use loop works against a mock; costs recorded; budget cap halts.

**Phase 21 — Agent tools bound to the sandbox**
- **Goal:** The tool surface the codegen/test/repair agents use.
- **Depends on:** 12, 13, 20
- **Tasks:** tools: `read_file`, `write_file`, `list_dir`, `run_command`, `install_deps`, `run_tests`, `start_preview`, `git_commit`; strict schemas; all operate on the project sandbox; guardrailed (no host access).
- **Deliverables:** tool library + tests.
- **Acceptance:** an agent can scaffold/modify/run code purely through tools.

**Phase 22 — Fixed-stack app skeleton template**
- **Goal:** Opinionated MERN-TS skeleton the agent fills (raises first-pass success).
- **Depends on:** 10
- **Tasks:** `templates/app-skeleton/` — FE (Vite+TS+router+api client+env), BE (Express+TS+Mongoose+Zod+health+error middleware+config), shared conventions, test scaffolds (Vitest/Jest/Playwright configs), README; a `.env` contract for injected Mongo URI.
- **Deliverables:** buildable skeleton that runs empty.
- **Acceptance:** skeleton boots FE+BE, connects to a Mongo URI, passes placeholder tests.

**Phase 23 — Codegen agent loop**
- **Goal:** Generate a working app from design + requirements onto the skeleton.
- **Depends on:** 15, 19, 21, 22
- **Tasks:** plan → instantiate skeleton → implement features (routes, models, UI, wiring) from design artifacts + requirements; stream code to IDE; run/boot to verify; structured "build report".
- **Deliverables:** codegen producing a running app.
- **Acceptance:** for a sample spec, generated app builds, boots, and serves a working feature; code streamed live.

**Phase 24 — Build stage handler & UI**
- **Goal:** Build stage with conversational change requests.
- **Depends on:** 09, 23
- **Tasks:** handler (generate → preview → change-request loop → proceed/skip); frontend build panel (live generation, preview, "request changes" chat, proceed/skip-to-deploy).
- **Deliverables:** working build stage.
- **Acceptance:** generate → preview → request change → regenerate; skip-testing path available.

### Epic 5 — Requirements & Test Generation

**Phase 25 — Structured requirements schema & API**
- **Goal:** The fill-in-the-blanks requirements model.
- **Depends on:** 07
- **Tasks:** `RequirementSpec` schema (features → inputs/expected behaviors/acceptance), CRUD API, validation.
- **Deliverables:** requirements API + tests.
- **Acceptance:** specs persist, validate, version.

**Phase 26 — Requirements UI (guided form)**
- **Goal:** Easy per-feature capture.
- **Depends on:** 09, 25
- **Tasks:** guided form builder (add feature → fill blanks), inline help, review summary; bind to stage.
- **Deliverables:** requirements panel.
- **Acceptance:** user builds a multi-feature spec via forms; saved + editable.

**Phase 27 — Test generation agent**
- **Goal:** Compile requirements → unit + Playwright E2E tests.
- **Depends on:** 21, 25
- **Tasks:** test-gen agent (Sonnet) mapping each feature/acceptance criterion to tests (Vitest/Jest+supertest unit; Playwright E2E against the preview); write into the sandbox; `TestSuite` artifacts traceable to requirements.
- **Deliverables:** generated suites.
- **Acceptance:** each acceptance criterion yields ≥1 test; suites are runnable.

**Phase 28 — Test runner & result parsing**
- **Goal:** Execute suites in the sandbox and produce structured results.
- **Depends on:** 13, 27
- **Tasks:** run unit + Playwright in container; parse JUnit/JSON → per-test pass/fail + failure detail + stdout; `TestRun` artifacts; surface to UI.
- **Deliverables:** runner + parsers + tests.
- **Acceptance:** results are structured, per-test, with actionable failure info.

### Epic 6 — Self-Healing Repair Loop (core)

**Phase 29 — Failure analyzer & minimal repair context**
- **Goal:** Turn failures into a minimal, diff-aware repair context (Section 9).
- **Depends on:** 12, 28
- **Tasks:** map failing tests → source files; compute diff since last passing run (git); assemble minimal context (failing test + touched files + diff).
- **Deliverables:** analyzer + tests.
- **Acceptance:** context excludes irrelevant files; diff correct.

**Phase 30 — Diff-aware repair agent**
- **Goal:** Targeted patching from minimal context.
- **Depends on:** 21, 29
- **Tasks:** repair agent (Sonnet) that patches only the failing behavior without breaking passing tests; apply + git-commit; re-run affected then full suite.
- **Deliverables:** repair agent + tests.
- **Acceptance:** for seeded bugs, agent fixes failing tests without regressions.

**Phase 31 — Loop controller (bounded + convergent)**
- **Goal:** The bounded, no-progress-aware, escalating controller.
- **Depends on:** 30
- **Tasks:** iterate with `max_iterations`; no-progress detection (`stall_threshold`, regression guard); convergence metrics; human-escalation payload + resume-with-guidance (re-enters Build); `RepairAttempt`/`Run` records with cost.
- **Deliverables:** loop controller + tests (converges, stalls→escalates, respects cap).
- **Acceptance:** never loops unbounded; escalates with a clear "stuck" summary; full audit trail.

**Phase 32 — Repair & test UI**
- **Goal:** Make the loop legible and steerable.
- **Depends on:** 09, 28, 31
- **Tasks:** test-results panel; live repair attempts (iteration, failing set shrinking, diffs); escalation prompt for user guidance; re-run controls.
- **Deliverables:** test+repair panel.
- **Acceptance:** user watches the loop, reads failures/diffs, and can intervene on escalation.

### Epic 7 — Infra Analysis & Deployment

**Phase 33 — Infra analyzer**
- **Goal:** Classify a project's deployment needs and produce a plan.
- **Depends on:** 12
- **Tasks:** inspect app (FE build target, BE presence, DB usage, env) → `infra_plan` (static FE / persistent BE / DB) + recommended targets + required env/secrets.
- **Deliverables:** analyzer + tests.
- **Acceptance:** correct plan for FE-only, FE+BE, and FE+BE+DB samples.

**Phase 34 — Secrets & credential vault**
- **Goal:** Secure storage/use of provider tokens (platform + BYO).
- **Depends on:** 03
- **Tasks:** encrypted `Credential` store (Fernet), per-user BYO + platform-owned creds, decrypt-at-use, never log/return; settings UI to add BYO tokens.
- **Deliverables:** vault + UI.
- **Acceptance:** secrets encrypted at rest; never exposed to client; used correctly.

**Phase 35 — Deploy provider adapters (Vercel + Render)**
- **Goal:** Deploy FE→Vercel, BE→Render, in both modes.
- **Depends on:** 33, 34
- **Tasks:** Vercel adapter (SPA deploy, env, domain, status/logs); Render adapter (Node web service, env, status/logs); seamless (platform creds) + BYO (user creds); a common `DeployProvider` interface.
- **Deliverables:** adapters + integration tests (mocked) + live smoke path.
- **Acceptance:** a built app deploys FE+BE and returns live URLs in both modes.

**Phase 36 — DB provisioning**
- **Goal:** Give each project a database; inject its URI.
- **Depends on:** 02, 34
- **Tasks:** create/assign a per-project Mongo database in the shared Atlas cluster (or accept user URI); inject `MONGODB_URI` into generated app env for preview + deploy; isolation guarantees.
- **Deliverables:** provisioning service + tests.
- **Acceptance:** projects get isolated DBs; generated app connects in preview and prod; BYO URI honored.

**Phase 37 — Deploy orchestration**
- **Goal:** End-to-end deploy: build → deploy FE+BE → wire env/DB → health-check.
- **Depends on:** 35, 36
- **Tasks:** orchestrate build artifacts, ordered deploy, env wiring (FE→BE URL, BE→DB URI), health checks, `Deployment` record + status events.
- **Deliverables:** deploy orchestrator + tests.
- **Acceptance:** one action takes a passing build to a healthy live deployment.

**Phase 38 — Visual deployment topology (React Flow)**
- **Goal:** n8n-style live component map.
- **Depends on:** 05, 37
- **Tasks:** React Flow graph (FE/BE/DB nodes + edges), live status/health, per-node logs drawer, URLs, redeploy controls.
- **Deliverables:** topology view.
- **Acceptance:** nodes reflect live status; logs viewable; redeploy works.

### Epic 8 — Live Validation

**Phase 39 — Post-deploy test runner**
- **Goal:** Re-run E2E/smoke against the live URL.
- **Depends on:** 28, 37
- **Tasks:** point Playwright at the deployed URL; smoke + critical E2E; structured live `TestRun`.
- **Deliverables:** live runner.
- **Acceptance:** live suite runs against prod; results structured.

**Phase 40 — Live-failure repair path & UI**
- **Goal:** Close the loop when the live site fails.
- **Depends on:** 31, 38, 39
- **Tasks:** on live failure → diagnose → repair (re-enter Build) → redeploy → re-validate; validation UI (pass/fail, loop, link on success).
- **Deliverables:** live-validation stage.
- **Acceptance:** induced live failure triggers repair→redeploy→revalidate; success delivers the final link.

### Epic 9 — Project Data Browser

**Phase 41 — Per-project data API**
- **Goal:** Scoped CRUD over a project's DB.
- **Depends on:** 36
- **Tasks:** list collections, read/query docs, create/update/delete, strictly scoped to the project's DB; guardrails.
- **Deliverables:** data API + tests.
- **Acceptance:** only the owning project's data is reachable; CRUD works.

**Phase 42 — Data browser UI**
- **Goal:** In-app data management.
- **Depends on:** 09, 41
- **Tasks:** collections list, document table, view/edit/create/delete, simple query/filter.
- **Deliverables:** data panel.
- **Acceptance:** user manages project data end-to-end.

### Epic 10 — Evaluation Harness

**Phase 43 — Benchmark spec format & seeds**
- **Goal:** Define and seed evaluation specs.
- **Depends on:** 25
- **Tasks:** spec format (input screenshots/prompt + requirements + expected features); 5–10 representative seed specs under `eval/specs/`.
- **Deliverables:** spec schema + seeds.
- **Acceptance:** specs load and validate.

**Phase 44 — Headless pipeline runner & metrics**
- **Goal:** Run the full pipeline on specs without UI; collect metrics.
- **Depends on:** 23, 28, 31, 37, 43
- **Tasks:** headless driver through all stages; capture first-pass vs post-repair pass rate, repair-iteration counts, tokens/₹ cost, screenshot→URL wall-clock; persist runs.
- **Deliverables:** eval runner.
- **Acceptance:** produces a metrics record per spec.

**Phase 45 — Results dashboard/report**
- **Goal:** Present evaluation evidence (for paper/pitch).
- **Depends on:** 44
- **Tasks:** aggregate metrics; charts + exportable report (CSV/markdown).
- **Deliverables:** results view + export.
- **Acceptance:** dashboard shows aggregates; report exports.

### Epic 11 — Hardening, PWA polish & Demo

**Phase 46 — Observability & cost tracking**
- **Goal:** Traceable runs + spend visibility.
- **Depends on:** 20
- **Tasks:** structured logging, per-run traces, per-project cost dashboard, alerts on cap.
- **Deliverables:** observability surfaces.
- **Acceptance:** every run traceable with cost; caps enforced visibly.

**Phase 47 — Security & sandbox hardening pass**
- **Goal:** Close escape/abuse vectors.
- **Depends on:** 11, 34
- **Tasks:** review sandbox isolation (net/caps/limits/timeouts), secret handling, authz on every route, rate limits, input validation; threat-model doc.
- **Deliverables:** hardening changes + doc.
- **Acceptance:** checklist passes; no secret leakage; no cross-project access.

**Phase 48 — Resilience & error handling**
- **Goal:** Graceful behavior under failure.
- **Depends on:** 08, 20, 35
- **Tasks:** timeouts/cancellation everywhere; container-crash recovery; provider-failure fallbacks (esp. Stitch quota); resumable long tasks; user-facing error UX.
- **Deliverables:** resilience improvements.
- **Acceptance:** injected failures degrade gracefully with clear messaging.

**Phase 49 — PWA finalize**
- **Goal:** Production-quality PWA.
- **Depends on:** 04
- **Tasks:** offline shell polish, caching strategy, update flow, installability across platforms.
- **Deliverables:** finalized PWA.
- **Acceptance:** installable + offline shell; passes PWA audits.

**Phase 50 — Demo scenario & seed**
- **Goal:** A flawless hero demo: screenshots → deployed URL, with the middle back-and-forth.
- **Depends on:** all prior
- **Tasks:** scripted hero scenario + seed data; polish the exact path (upload → design refine → build → tests+repair → deploy → live link); fallbacks for on-stage reliability; short runbook.
- **Deliverables:** demo runbook + seeds.
- **Acceptance:** end-to-end hero flow runs reliably start to finish.

---

## 11. Milestones

- **M1 (Epics 0–2):** authenticated workspace + working web IDE/terminal/preview over sandboxes.
- **M2 (Epics 3–4):** screenshots/text → design → generated running app with live code + change requests.
- **M3 (Epics 5–6):** requirements → tests → **bounded self-healing repair** (the contribution) working.
- **M4 (Epic 7–8):** dual-mode deploy + visual topology + live validation → **deployed URL delivered**.
- **M5 (Epics 9–11):** data browser, evaluation evidence, hardening, PWA polish, **hero demo**.

## 12. Risks & Mitigations

- **Stitch quota/auth (D10):** central creds + token refresh + quota tracking + Figma fallback + clear messaging.
- **Repair loop non-convergence (D7):** hard caps + no-progress detection + escalation; measured by the eval harness.
- **Sandbox security:** strict isolation + hardening pass (Phase 47); never run generated code outside sandboxes.
- **Cost overrun (₹6k):** Haiku for small calls, budget caps, cost dashboard; cache/skeleton to cut tokens.
- **Scope creep:** fixed stack + out-of-scope list; resist adding stacks/collab/payments.
- **Vercel BE limitation:** BE on Render, not Vercel (D11).

## 13. Environment Variables (`.env.example` reference)

```
# Core
FORGEFLOW_ENV=local
SECRET_KEY=...
FERNET_KEY=...                      # secret encryption
# Anthropic
ANTHROPIC_API_KEY=...
MODEL_CODEGEN=claude-sonnet-5
MODEL_ROUTING=claude-haiku-4-5-20251001
BUDGET_CAP_INR_PER_PROJECT=...
BUDGET_CAP_INR_GLOBAL=...
# MongoDB (control plane + shared app-data cluster)
MONGODB_URI=...                     # Atlas cluster
FORGEFLOW_META_DB=forgeflow_meta
# Sandbox
SANDBOX_IMAGE=forgeflow-sandbox:latest
SANDBOX_CPU_LIMIT=...
SANDBOX_MEM_LIMIT=...
SANDBOX_IDLE_TIMEOUT_S=...
PREVIEW_BASE_DOMAIN=...             # prod reverse-proxy domain
# Design providers
STITCH_* (GCP project, OAuth/service-account, quota)   # see Phase 17
FIGMA_* (token)                                         # see Phase 18
# Deploy providers (platform-owned defaults; BYO stored per-user)
VERCEL_TOKEN=...
RENDER_API_KEY=...
# Repair loop
REPAIR_MAX_ITERATIONS=5
REPAIR_STALL_THRESHOLD=2
```

---

*End of IMPLEMENTATION_PLAN.md — expand each phase in Section 10 into `plans/phase-XX-*.md` per the startup prompt.*
