# Phase 58 — Vercel-hosted backend & deploy correctness

## Title & ID

- **ID:** `phase-58`
- **Title:** Vercel-hosted backend & deploy correctness (source delivery, D11 amendment, partial-failure guard)
- **Epic:** Epic 7 — Deploy *(amends `phase-35`/`phase-37`)*
- **Milestone:** M4
- **Status:** ⬜ todo

## Goal

Make **Deploy actually ship the generated app**. Today the backend leg cannot work at all: Render
builds by cloning a git repo, and nothing in ForgeFlow ever pushes the sandbox workspace anywhere.
Move the backend to **Vercel**, which accepts inline file uploads — the same mechanism the frontend
already uses — so what deploys is exactly what the repair loop validated, with no git host, no new
credential and no push plumbing. Then fix the partial-failure guard that lets a dead backend ship a
live frontend.

## Context

Three findings, in order of severity.

**1. The backend leg has never been able to work.** `RenderDeployProvider.deploy`
(`app/deploy/providers/render.py:66-84`) sends `body["repo"] = spec.repo_url`, and the orchestrator
fills that from **global** config — `repo_url=str(config.get("deploy_repo_url")) or None`
(`app/deploy/orchestrate.py:190`), one value for every project on the instance. Meanwhile
`app/sandbox/git.py` exposes only `ensure_init`/`commit`/`diff`/`changed_paths`/`set_last_passing`;
`grep -rn "git push" backend/app` returns nothing. The workspace lives in a Docker volume and never
leaves it. So Render clones whatever `DEPLOY_REPO_URL` points at — never the generated app.
`config.py:298` calls the field *"blank locally/mocked"*, and `phase-37`'s own as-built note records
the live Vercel/Render path as deferred ("billable resources"). This is a **gap in the plan**, not a
regression: the spec never said how code reaches Render.

**2. D11's rationale is now outdated.** D11 reads *"FE→Vercel (SPA), BE→Render (persistent Node
service)"* because *"Vercel alone can't host a persistent Node backend + Mongo"*. Vercel now runs
Express applications directly. Crucially it deploys them from an **inline file upload**
(`POST /v13/deployments` with a `files` array) — precisely what `VercelDeployProvider.deploy`
(`app/deploy/providers/vercel.py:53-84`) already does for the SPA. Adopting it deletes the entire
source-delivery problem rather than solving it. **The user has authorised amending D11** (Golden
Rule 1 satisfied: asked and answered, 2026-08-23).

The skeleton is already the right shape for this: `templates/app-skeleton/backend/src/app.ts`
exports `createApp()` and `src/index.ts` merely listens (`index.ts:10-13`). A Vercel entrypoint is
`export default createApp()` — the listener stays for preview and local dev.

**3. A real defect, adjacent.** `deploy()` defines "the backend failed" two different ways:

- `orchestrate.py:124` — the FE-skip guard: `if plan.fe is not None and be_error is None`. Catches
  only a raised `ProviderError`.
- `orchestrate.py:287` — the roll-up: `not _is_live(be, be_error)`, where `_is_live` (`:275-276`)
  demands `error is None and result.status is DeployState.live`.

A backend that returns `DeployState.failed`, or that is still `building` when `_await_live`
(`:222-233`) exhausts `DEPLOY_HEALTH_TIMEOUT_S`, raises nothing. `be_error` stays `None`, the guard
passes, and a **real, billed** Vercel frontend ships wired to an API that is not there. The roll-up
then correctly reports `failed` — the two halves disagree about the same event.
`tests/deploy/test_orchestrate_partial_failure.py:66` asserts *"the frontend is never attempted when
there is no backend URL to wire it to"*, but `FakeDeployProvider(fail=True)` only ever `raise`s
(`tests/deploy/orchestrate_fakes.py:65-71`), so the non-live-status branch has no coverage at all.
The missing feature (1) triggers exactly this untested branch.

## Depends on

- `phase-34` (credential vault) · `phase-35` (provider adapters) · `phase-36` (DB provisioning) ·
  `phase-37` (deploy orchestration) · `phase-22` (app skeleton) — all ✅.
- No new external credential: the existing `VERCEL_TOKEN` (platform) / vault `vercel` kind (BYO)
  now covers both targets.

## Detailed tasks

### 1. Amend the locked decision (docs first, so the code change is never silent)

- `IMPLEMENTATION_PLAN.md` §1 **D11** — rewrite to *"FE→Vercel (SPA), BE→Vercel (Node function),
  DB→Atlas"*, with the amendment dated and the original rationale retained as superseded context.
- `IMPLEMENTATION_PLAN.md` risk §12 — replace the "Vercel BE limitation" entry with the serverless
  trade-offs actually taken on (cold starts, no long-lived process, connection reuse, Atlas
  network access).
- `CLAUDE.md` — update the **Deploy** bullet under *Tech stack* (currently "Vercel (SPA FE), Render
  (Node BE). **BE is never deployed to Vercel.**").
- `plans/phase-35-deploy-provider-adapters.md` — append an amendment note; do **not** rewrite
  history in the as-built section.

### 2. Skeleton: make the generated backend Vercel-deployable (`templates/app-skeleton/`)

- `backend/api/index.ts` — `export default createApp()`. The Vercel Node runtime hands the exported
  Express app each request. `src/index.ts` is untouched: preview, `pnpm dev` and local runs still
  bind a port.
- `backend/src/db.ts` — cache the Mongoose connection on `globalThis` so warm invocations reuse it
  instead of opening a socket per request (the standard serverless pattern, and the difference
  between a working API and Atlas connection-limit exhaustion). Must remain a no-op for the
  long-lived preview process.
- `backend/vercel.json` — route every path to the function; pin the Node runtime.
- Keep `pnpm start`/`pnpm dev` working unchanged — `make skeleton-verify` must stay green, and the
  **preview stage must be entirely unaffected** (it is the demo hero-path).

### 3. Vercel adapter: serve both targets (`app/deploy/providers/vercel.py`)

- `target` becomes a **set** of served targets (`{fe, be}`); `BaseDeployProvider._check_target`
  (`base.py:294-300`) generalises from identity to membership. The Render adapter keeps its single
  `be` target so the mechanism is unchanged, not special-cased.
- Backend deploys upload **source** files plus `vercel.json` and let Vercel build (`builds`/
  `functions` config), rather than uploading a prebuilt bundle: the Node build is Vercel's job.
- `DeploySpec` gains nothing; `files` now serves both targets, and `repo_url`/`branch`/`root_dir`/
  `start_cmd` become Render-only (documented as such in the docstring).
- Env vars: the backend needs `MONGODB_URI` + `NODE_ENV` at **runtime**, not build time — set them
  project-scoped before triggering the deploy, exactly as the FE path already does for
  `VITE_API_BASE_URL`.

### 4. Registry & orchestration (`app/deploy/providers/__init__.py`, `app/deploy/orchestrate.py`)

- `provider_for(target)` maps `be` → Vercel by default, with `DEPLOY_BE_PROVIDER` (`vercel` |
  `render`) as the config seam so Render remains reachable for anyone who supplies a repo. Default
  `vercel`.
- `_deploy_backend` (`:169-195`) gains a workspace-source step mirroring `_build_frontend`: collect
  the backend directory from the sandbox via the existing archive-API workspace reader, honouring
  `BackendPlan.dir` and excluding `node_modules`/`.git`/`dist`.
- **Fix the guard** (`:124`): `if plan.fe is not None and _is_live(be_result, be_error)`. A backend
  that is not live must stop the frontend deploy — matching `_overall_status`'s existing definition
  and preventing a billed deployment wired to nothing.
- Report the skip honestly: emit an `fe` step with status `skipped` and the reason, so the topology
  and the UI show why rather than showing nothing.

### 5. Config & docs

- `.env.example` + `Settings` + `config_registry`: add `DEPLOY_BE_PROVIDER`; re-comment
  `DEPLOY_REPO_URL` as *Render-only, unused on the default Vercel path*.
- `DEVELOPER.md` §3 — replace the "Deploy — what actually works" section: with `VERCEL_TOKEN` and an
  Atlas URI the **whole** deploy stage now runs from a laptop.

## Out of scope for this phase

- Render removal. The adapter, its tests and the `render` credential kind all stay; only the default
  target mapping changes.
- Custom domains, preview/production branching, Vercel teams/scopes beyond the existing token scope.
- Any change to the repair loop, the build stage, or the live-validation runner (`phase-39`) beyond
  the URL it reads from `Deployment.urls`.
- Automatic Atlas provisioning — `APP_DB_CLUSTER_URI` is still operator-supplied (`phase-36`).

## Acceptance criteria

1. With `VERCEL_TOKEN` and an Atlas `APP_DB_CLUSTER_URI` set, one Deploy press on a built project
   produces **two live URLs**, the frontend calling the backend, the backend reading/writing Atlas.
2. No git remote, GitHub token, or `DEPLOY_REPO_URL` is required for the default path.
3. What deploys is byte-identical to the validated workspace — no rescaffolding, no template.
4. A backend that returns a non-live **status** (not only a raised error) stops the frontend deploy;
   the deployment is `failed`, no Vercel FE deployment is created, and the `fe` step says why.
5. `_check_target` still refuses a mismatched spec; Render still refuses an `fe` spec.
6. `make skeleton-verify` green; the preview stage behaves exactly as before.
7. D11, risk §12, `CLAUDE.md` and `DEVELOPER.md` all state the amended split; no doc still claims
   "BE is never deployed to Vercel".

## Tests to write

- `tests/deploy/test_vercel_backend_spec.py` — a `be` spec reaches Vercel's transport with the right
  URL, body, source files and runtime env; a prebuilt-`files` FE spec is unchanged.
- `tests/deploy/test_provider_registry.py` (extend) — `be` → Vercel by default, → Render under
  `DEPLOY_BE_PROVIDER=render`; Vercel still refuses nothing it serves; Render still refuses `fe`.
- `tests/deploy/test_orchestrate_partial_failure.py` (extend) — **the missing case**: a fake whose
  `deploy` *returns* `DeployState.failed` (and a second that stays `building` past the deadline);
  assert `fe.deploy_specs == []`, status `failed`, and an `fe` step with `skipped` + reason.
- `tests/deploy/orchestrate_fakes.py` — `FakeDeployProvider` gains a `status=` knob so a non-live
  return is expressible; the existing raise-based `fail=True` stays.
- `tests/deploy/test_orchestrate_env_wiring.py` (extend) — the BE gets `MONGODB_URI` as a runtime
  env var on the Vercel path.
- `templates/app-skeleton` — a unit test that `api/index.ts` exports the same app `createApp()`
  builds, and that the cached connection helper opens exactly one connection across two calls.

## Manual verification steps

1. `make sandbox-build`, build a project to green, set `VERCEL_TOKEN` + an Atlas URI.
2. Deploy. Both URLs live; create a record through the FE and confirm it lands in Atlas.
3. Revoke the Atlas URI, redeploy: the BE goes non-live, **no** FE deployment is created, the stage
   reports `failed` with the reason on the `fe` step.
4. `DEPLOY_BE_PROVIDER=render` with a real repo URL: the Render path still deploys as before.

## Rollback / cleanup notes

- Reverting is `DEPLOY_BE_PROVIDER=render` plus the doc amendment — no data migration. Existing
  `Deployment` records keep their `be_target` string, so history stays readable.
- Vercel projects created during testing must be deleted by hand; `destroy` covers ForgeFlow-created
  ones.

## Design notes

- **Why not GitHub-repo-per-project.** It was the alternative (and the user's first choice): create
  a repo via the GitHub API, push the workspace from the *control plane* (never handing a token to
  the sandbox), let Render clone it. It preserves D11 and gives a genuinely persistent process, at
  the cost of a new provider, a new credential kind, and repo lifecycle/cleanup per project. Vercel's
  inline upload achieves the same delivery guarantee with **none** of that surface, so the source of
  truth stays the sandbox workspace. Render stays behind a config seam for the persistent-process
  case.
- **Serverless is a good fit for what ForgeFlow generates.** The fixed stack (D2) is React + Express
  + Mongoose CRUD — request/response only, no websockets, no background jobs. The one real hazard is
  per-invocation connection churn, which the cached-connection change addresses directly.
- **Golden Rules preserved.** The generated-app stack (D2) is untouched — same Express, same
  Mongoose, one added entrypoint. Untrusted code still never executes in the control plane: the
  workspace is *read* via the archive API and handed to Vercel. Secrets stay vault-resolved at call
  time. `deploy ⇐ build` is still the only prerequisite.
