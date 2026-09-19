# ForgeFlow app-skeleton

The **fixed-stack generated-app template** — a complete, runnable **pnpm monorepo** that every
ForgeFlow project starts from. The codegen agent *fills this in*; it **never rescaffolds** it. That
is the single biggest first-pass-success and token-cost lever (D2, IMPLEMENTATION_PLAN §12).

> **Do not** add app-specific/feature code here. This is the shared base for *all* generated apps.
> Feature code is written into instantiated copies (in a sandbox), never back into this template.

## Stack (locked — D2 + Tailwind directive)

| Layer | Tech |
|-------|------|
| Frontend | React 18 + Vite + TypeScript (strict) + **Tailwind** + React Router |
| Backend | Node + Express + TypeScript + Mongoose + **Zod** |
| FE tests | Vitest + Testing Library (unit), Playwright (E2E) |
| BE tests | Jest + supertest |
| Tooling | pnpm workspaces, ESLint, `tsc` |

## Layout

```
app-skeleton/
  package.json           # workspace root: dev/build/test/lint scripts that fan out to both packages
  pnpm-workspace.yaml    # packages: frontend, backend
  .env.example           # the injected env contract (see below)
  frontend/
    src/
      lib/api.ts         # typed API client (reads VITE_API_BASE_URL) — the ONLY place fetch lives
      components/Layout.tsx
      pages/HomePage.tsx # example page (replace with features)
      routes.tsx         # route table — agent adds routes at FORGEFLOW:ROUTES
      features/          # ← feature code goes here (see features/README.md)
    e2e/                 # Playwright specs
    tailwind.config.ts   # Tailwind pre-configured — zero styling setup is ever generated
  backend/
    src/
      app.ts             # express app factory — agent mounts routers at FORGEFLOW:ROUTES
      index.ts           # entry: listen first, then connect Mongo (non-fatal)
      config.ts          # typed env (PORT, MONGODB_URI, NODE_ENV)
      db.ts              # mongoose connect/disconnect helpers
      routes/health.ts   # GET /health (DB-independent liveness for preview/deploy probes)
      middleware/        # errorHandler (+ AppError, notFound), requestLogger
      lib/validate.ts    # Zod body-validation middleware
      test/              # test database helper (useTestDb) + the Jest global setup/teardown
      features/          # ← feature code goes here (see features/README.md)
```

## Fill-in points (where the agent writes)

- **Frontend routes** — `frontend/src/routes.tsx` at the `FORGEFLOW:ROUTES` marker.
- **Frontend features** — `frontend/src/features/<feature>/` (see `frontend/src/features/README.md`).
- **Backend routers** — mounted in `backend/src/app.ts` at the `FORGEFLOW:ROUTES` marker.
- **Backend features** — `backend/src/features/<feature>/` (see `backend/src/features/README.md`).

Everything else (build config, Tailwind, the API client, the app factory, middleware, health, the
test database helper) is **scaffold** and must not be regenerated.

## Backend tests that touch data (phase-65)

Data tests run against a **real MongoDB**: one in-memory server per Jest run, started by
`backend/src/test/globalSetup.ts`. One call at the top of a test file wires it up:

```ts
import { useTestDb } from '../../test/db'

useTestDb() // own database per file · collections emptied between tests · dropped afterwards
```

Inside the ForgeFlow sandbox the server is the `mongod` baked into the sandbox image, so this works
with **no network**, and nothing is downloaded. On a laptop or in CI, `mongodb-memory-server-core`
downloads a binary on first use, as usual. It is the `-core` package on purpose: it has no postinstall
hook, so installing the app, including the deployed backend's build, never fetches ~105 MB.

The setup also points `MONGODB_URI` at the in-memory server for the duration of the run, so no test
can reach a real database, whoever launched Jest. If the server cannot start, suites that do not use
the database still run, and `useTestDb()` fails at once with `[forgeflow:test-db-unavailable]` and
the reason. `jest.config.js` caps `maxWorkers` at 2, because the sandbox has one CPU and 1 GB to share.

## Env contract (injected — never committed)

| Var | Package | Injected by | Meaning |
|-----|---------|-------------|---------|
| `MONGODB_URI` | backend | preview (phase-15) / deploy (phase-37) / provisioning (phase-36) | per-project isolated database |
| `PORT` | backend | preview / Render | API listen port (default 3001) |
| `NODE_ENV` | both | preview / deploy | `development` \| `production` |
| `VITE_API_BASE_URL` | frontend | preview / deploy | absolute base URL of the backend API |

`.env.example` is authoritative; real values are injected at run time and never checked in.

## Scripts (run from the workspace root)

| Command | Effect |
|---------|--------|
| `pnpm install` | install all workspace deps |
| `pnpm dev` | run FE (Vite) + BE (Express, `tsx watch`) in parallel |
| `pnpm build` | build both packages (`vite build`, `tsc`) |
| `pnpm test` / `pnpm test:unit` | unit suites (FE Vitest + BE Jest) — no server needed (BE starts its own in-memory MongoDB) |
| `pnpm test:e2e` | Playwright E2E (needs a booted preview / live URL via `PLAYWRIGHT_BASE_URL`) |
| `pnpm lint` / `pnpm typecheck` | ESLint / `tsc --noEmit` across both packages |

These map 1:1 to the agent's `run_tests` tool (`all`→`pnpm test`, `unit`→`pnpm test:unit`,
`e2e`→`pnpm test:e2e`) and to the preview dev commands (phase-15 config).

## Instantiation contract (deterministic — no LLM tokens)

ForgeFlow copies this template into a project's sandbox `/workspace` with **no model involvement**
(`instantiate_skeleton`, phase-21 → `app.agents.tools.skeleton.copy_skeleton`):

1. **Copy** every template file into `/workspace` (deterministic file copy; UTF-8 text).
2. **`git init` + initial commit** `"skeleton"` — the baseline the diff-aware repair loop diffs against.
3. **`pnpm install`** — resolve dependencies.

Only *after* this does the agent write feature code onto the scaffold (phase-23).

## Design notes

- **Tailwind** in the generated-app FE is a plan-consistent extension explicitly requested by the
  user; it does not change the locked stack (D2). Tailwind **v3** is used deliberately (most-trodden,
  best-documented setup ⇒ highest codegen first-pass success).
- **`/health` is DB-independent** so the preview/deploy health probe passes even before Mongo
  connects (`index.ts` listens first, connects after).
- **Playwright** is pinned to the version baked into the sandbox image (chromium pre-installed at
  `/ms-playwright`), so no `playwright install` is needed at run time.
- **CORS** is permissive in the skeleton (dev preview across subdomains); tighten per-project before
  a production deploy.
