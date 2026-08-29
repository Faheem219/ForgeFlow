# Phase 62 — Deploy truthfulness: real env vars, real logs, BYO readiness, and teardown

## Title & ID

- **ID:** `phase-62`
- **Title:** Deploy truthfulness — project-scoped env vars, provider logs, BYO credential readiness, DB-reachability guard, deployment teardown
- **Epic:** Epic 7 — Deploy *(amends `phase-35`/`phase-37`/`phase-38`/`phase-58`)*
- **Milestone:** M4
- **Status:** ✅ done *(2026-08-29)*

## Goal

Make the Deploy stage tell the truth about what it did, and let the user undo it.

Five findings from a live session (2026-08-29), all confirmed in the code. Four are about the panel
claiming something the provider does not actually show; the fifth is a missing affordance. Two of
them are the *same* underlying gap: `DeployProvider` methods that were built and unit-tested in
`phase-35` and then never wired to anything — `set_env` and `destroy`.

## Context

**1. Env vars are set, but nowhere the user can see them.** `VercelDeployProvider.deploy`
(`app/deploy/providers/vercel.py:63-90`) puts env inside the **deployment creation body**
(`body["env"]`, plus `body["build"]["env"]` for the SPA). Vercel applies that to *that immutable
deployment* — it never appears under Project → Settings → Environment Variables. So the reported
symptom is exactly right, and so is the observation that the deployed frontend still calls the
deployed backend: runtime wiring works, visibility does not.

The adapter already has the correct mechanism. `VercelDeployProvider.set_env`
(`vercel.py:93-120`) upserts **project-scoped** production vars via
`POST /v10/projects/{project}/env?upsert=true`, and it is covered by
`tests/deploy/test_vercel_adapter.py:113`. `grep -rn "set_env" backend/app` returns **only the
definitions** — the orchestrator never calls it. `phase-58`'s own task list said *"set them
project-scoped before triggering the deploy, exactly as the FE path already does"*; the FE path did
not in fact do that, so neither leg ended up doing it.

Three consequences, in order of severity:

- **A redeploy from the Vercel dashboard produces a broken app.** A new deployment created outside
  ForgeFlow inherits *project* env, of which there is none — the backend comes up with no
  `MONGODB_URI`.
- In BYO mode the vars are invisible and uneditable in the user's own account.
- The node drawer's `set` label (`NodeDrawer.tsx:116`) is a claim about the *wiring plan*, derived
  from `topology_snapshot` edges (`topologyModel.ts:166-174`), not about provider state.

**2. The Logs tab shows ForgeFlow's log, not Vercel's.** `GET /projects/{id}/deploy/latest/logs`
(`app/deploy/deploy_router.py:88-102`) returns the orchestrator's own step list —
`db: provisioned (platform)`, `be: live https://…` — assembled in `orchestrate.py` and stored as a
blob. That is a genuinely useful pipeline record, but it is not what "Logs" on a Vercel node
implies. `VercelDeployProvider.logs` (`vercel.py:129-147`) does call Vercel's real
`GET /v2/deployments/{id}/events`, and again **no route reaches it**.

It cannot simply be called, either: `Deployment` (`app/db/models/deployment.py:15-26`) records
`urls` and `topology_snapshot` but **not the provider deployment ids**, and `logs(ref)` keys off
`ref.id`. The id exists only inside `orchestrate.deploy()` and is discarded when the function
returns.

**3. Bring-your-own gives no readiness signal.** `DeployPanel.tsx:155-171` toggles `mode` between
`seamless` and `byo` and sends it on the intent; nothing tells the user that BYO needs a Vercel
token and (optionally) a MongoDB URI in Settings, or whether theirs are stored. The failure is
correct but late — `BaseDeployProvider._token` (`base.py:195-210`) raises *"No vercel token stored
for this account"* only once Deploy has been pressed and the pipeline has started. The metadata to
answer it up-front already exists: `GET /credentials` (`credentials_router.py:47-53`) returns
kind/scope/created_at/last4 with no values.

**4. A local app-DB cluster URI silently ships a backend that cannot reach its database.**
`DbProvisioner.get_app_mongodb_uri` (`db_provision.py:139-152`) composes the deployed backend's
`MONGODB_URI` from `APP_DB_CLUSTER_URI` (falling back to `MONGODB_URI`). Both default to
`mongodb://localhost:27017` in a laptop setup. A Vercel function given a loopback URI boots
cleanly and then times out every query — mongoose's *"buffering timed out after 10000ms"* — which
reads as a code bug and sends the user into the repair loop chasing nothing. Nothing checks this
before a real, billed deployment is created.

This is the same class of mistake the sandbox path already documents at length
(`.env.example:118-124`), and the split is genuinely confusing: `APP_DB_CLUSTER_URI` must be
**public** (Atlas) so the deployed function can reach it, while `APP_DB_SANDBOX_URI` must be
**local** (`mongodb://forgeflow-appdb:27017`) because a sandbox sits on an `internal` docker
network with no route to the internet (`app/sandbox/manager.py:150-160`) outside the brief
registry-egress window an install borrows (`app/sandbox/exec.py:148-151`). `.env.example` currently
describes them as *"the SAME cluster … addressed the way a sandbox must address it"*, which stops
being true the moment the cluster is Atlas.

**5. Nothing can delete a deployment.** `VercelDeployProvider.destroy` (`vercel.py:149-154`) and
`RenderDeployProvider.destroy` exist and are tested; `grep -rn "destroy" backend/app` finds callers
only for the *sandbox* manager. So:

- There is no way to take a deployment down from the UI.
- **Deleting a project leaves its deployments live.** `ProjectService.delete`
  (`projects/service.py:139-174`) tears down the sandbox and optionally the app database, and
  reports what it could not reclaim — deployments are not in that cascade at all, so a deleted
  project keeps serving (and billing) two Vercel deployments forever.

## Depends on

- `phase-34` (credential vault) · `phase-35` (provider adapters) · `phase-36` (DB provisioning) ·
  `phase-37` (deploy orchestration) · `phase-38` (topology UI) · `phase-58` (Vercel backend) — all ✅.
- No new external credential and no new provider.

## Detailed tasks

### 1. Persist the provider refs on `Deployment` (unblocks tasks 3 and 6)

- `app/db/models/deployment.py` — add `refs: dict[str, DeploymentRef]` keyed by target (`"fe"`,
  `"be"`), each carrying `provider`, `id` and `project`. This is the missing handle for *every*
  post-deploy provider operation; `logs`, `destroy` and a future `status` re-check all key off it.
  Secret-safe by construction: a ref is an opaque deployment id and a project name, never a token.
- `orchestrate.py` — record `be_result.ref` / `fe_result.ref` on the `Deployment` it inserts.
- Absent on every pre-phase-62 record, so both consumers must degrade rather than fail: an old
  deployment offers the pipeline log and no delete, with the reason stated in the UI.

### 2. Mirror env to project-scoped variables (`orchestrate.py`, `vercel.py`)

- After a target's deploy returns (the Vercel *project* does not exist until its first deployment),
  call `provider.set_env(result.ref, env, mode=…, user_id=…)` for that target's env map.
- Keep the inline deployment env exactly as it is. It is what makes the *current* deployment
  correct; the project-scoped copy is what makes the dashboard honest and the next
  dashboard-triggered redeploy work. Neither replaces the other.
- **Fail soft.** A working deployment must not be reported as failed because a follow-up env write
  was rejected; log it, append a line to the pipeline log (`env: …`), and carry on. The deployment
  is already live and correct at this point.
- `vercel.py` — `set_env` currently hardcodes `"type": "plain"`, which stores a connection string
  in the clear. Choose per key: `encrypted` for secret-shaped names (`MONGODB_URI`, anything
  matching `*_URI`/`*_SECRET`/`*_TOKEN`/`*_KEY`/`*_PASSWORD`), `plain` for the rest
  (`NODE_ENV`, `VITE_API_BASE_URL`). Encrypted values still list in the dashboard and can be
  revealed there — which is what the user asked to see — but are not returned by the API.
- Render's `set_env` replaces the whole env set (`render.py:91`); it is called the same way, and
  the backend env map is complete on every deploy, so replace is correct there.

### 3. Real provider logs, with the pipeline log kept beside them

- `deploy_router.py` — add `GET /projects/{id}/deploy/latest/logs/{target}` returning the provider's
  own log lines for that target's stored ref (`provider_for(target).logs(ref, …)`).
  - `target` ∈ {`fe`, `be`}; `db` has no provider deployment and returns an empty list with a
    reason, not a 404.
  - Provider failure is **not** a 500: a deleted/expired deployment or a revoked token yields an
    empty list plus an `error` string the drawer can show. The panel must never break because a
    log fetch did.
  - The route needs the `mode` the deployment used (`Deployment.mode`) and the owner's `user_id`
    for BYO token resolution — both already on the record.
- `NodeDrawer.tsx` — two log sources behind a small segmented control: **Provider** (default for
  `fe`/`be` when a ref exists) and **Pipeline** (today's step log, and the only option for `db` and
  for pre-phase-62 records). Label the section honestly in both states.
- Scope: Vercel's events API serves **build** logs. Runtime/function logs are a different streaming
  endpoint and are explicitly out of scope (below).

### 4. BYO readiness in the Deploy stage

- `credentials_router.py` — the existing `GET /credentials` already returns what is needed; no new
  endpoint. (It is per-user and value-free by construction.)
- `DeployPanel.tsx` — when `mode === 'byo'`, render a readiness strip: one row per required
  credential (**Vercel** for both targets on the default path, or **Render** when the backend
  provider is Render; **MongoDB URI** optional-but-recommended), each **Stored** (with `last4`) or
  **Missing**, and a link to Settings → Provider credentials.
- **Block Deploy** while a required credential is missing, with the reason in the button's title —
  the same shape as the existing `deployDisabledReason` prereq hint, so there is one mechanism for
  "you cannot deploy yet, here is why". The server-side check in `_token` stays as the real
  enforcement; this is a hint, exactly as `prereqUnmet` is.
- The backend provider key must reach the client so the strip asks for the right token: expose it
  read-only (e.g. on the infra plan or a small `GET /deploy/config`) rather than hardcoding
  `vercel` in the UI.
- Seamless mode shows nothing new — platform credentials are not the user's business.

### 5. Guard a database the deployed backend cannot reach (`db_provision.py`, `orchestrate.py`)

- Add `assert_reachable_from_internet(uri)` (name it for what it checks) to `DbProvisioner`: a
  platform-mode URI whose host is `localhost`/`127.0.0.1`/`::1`/`host.docker.internal`, or a bare
  docker service name with no dots (`mongodb://forgeflow-appdb:27017`), cannot be reached from a
  deployed function.
- Call it in `orchestrate.deploy()` at the **db** step, *before* any provider call — so the deploy
  fails with a clear `UserError` naming `APP_DB_CLUSTER_URI` instead of creating billed deployments
  that will time out. A **BYO** URI is exempt (the user supplied it; it is theirs to get right) and
  so is the preview/sandbox path, which is *supposed* to be local.
- `.env.example` — rewrite the `APP_DB_CLUSTER_URI` / `APP_DB_SANDBOX_URI` block for the case that
  actually happens: the cluster is Atlas (public, used by the deployed app **and** by the control
  plane) while the sandbox URI is the local `appdb` service, because the sandbox has no internet.
  State the consequence plainly — **preview data and deployed data are different databases** — and
  that this is the only combination that works without granting sandboxes permanent egress.

### 6. Delete a deployment

- `app/deploy/teardown.py` (new, small) — `DeploymentTeardown.destroy(deployment, user_id)`: call
  `provider.destroy(ref)` for each stored ref, **fail soft per target**, and return a report naming
  what could not be reclaimed. Modelled on `ProjectService.delete`'s `DeleteReport`, for the same
  reason: an unreachable provider must not leave the user with a record they cannot remove.
- `deploy_router.py` — `DELETE /projects/{id}/deploy/latest`, returning that report. Destructive and
  outward-facing, so the UI confirms first.
- After a successful teardown:
  - mark the record `status = "deleted"` and clear `urls` (keep the row — it is the audit trail of
    what was deployed and when; versions are never overwritten, §7);
  - set the **deploy** stage back to `empty`, because `validate ⇐ deploy` must not stay satisfied by
    a deployment that no longer exists;
  - mark **validate** `stale` if it had produced anything, for the same reason — its evidence points
    at a dead URL.
- **The app database is never touched.** Deleting a deployment takes down the hosting, not the data;
  dropping data stays an explicit project-delete opt-in (`drop_app_data`, `db_provision.teardown`).
- `ProjectService.delete` — add deployment teardown to the cascade, fail-soft, reported in
  `DeleteReport.warnings` like the sandbox and app-DB legs. *Adjacent to the request, included
  because it is the same mechanism and the leak is real: today a deleted project keeps two billed
  deployments alive with no way to reach them from ForgeFlow.*
- `DeployPanel.tsx` — a **Delete deployment** action (confirm modal naming both URLs and stating
  that data is untouched), enabled only when a deployment with refs exists. After it completes the
  topology shows nothing live and the primary button reads **Deploy** again.

## Out of scope for this phase

- Vercel **runtime/function** logs (a separate streaming API with its own auth and polling model);
  build logs only.
- Reading provider env state back to *verify* the drawer's `set` label. Task 2 makes the claim true
  at the source; a read-back would add a provider round-trip per drawer open. Revisit if the two
  ever drift.
- Automatic Atlas provisioning. `APP_DB_CLUSTER_URI` stays operator-supplied (`phase-36`); task 5
  only refuses to deploy against one that cannot work.
- Custom domains, Vercel teams/scopes, preview-vs-production branching.
- Any change to the build stage, the repair loop, or `phase-39` live validation beyond the stage
  reset in task 6.
- Deleting *individual* nodes (just the FE, just the BE). Deployment is pipelined — the FE is built
  against the BE's URL — so a half-deleted deployment has no coherent meaning.

## Acceptance criteria

1. After a deploy, both Vercel projects list their environment variables in the dashboard:
   `MONGODB_URI` + `NODE_ENV` on the API project, `VITE_API_BASE_URL` on the web project. Secret-
   shaped values are stored `encrypted`, not `plain`. *(As built: the web project gets no `NODE_ENV`
   — the SPA is a static bundle and its `NODE_ENV` is a Vite build input consumed inside the
   sandbox, never a variable on the host. The node drawer stopped listing it for the same reason.)*
2. A redeploy triggered from the Vercel dashboard (not from ForgeFlow) produces a working backend,
   because the project now carries its own env.
3. A rejected env write leaves the deployment `live`, with the failure recorded in the pipeline log
   — never a `failed` deploy for a cosmetic follow-up.
4. Selecting the **be** or **fe** node shows Vercel's own build log; a provider error or a
   pre-phase-62 record shows the pipeline log with a stated reason, and never an error page.
5. Choosing **Bring your own** shows each required credential as Stored (last4) or Missing with a
   link to Settings, and Deploy is disabled with a reason until the required ones exist. Seamless is
   visually unchanged.
6. With `APP_DB_CLUSTER_URI` unset or pointing at localhost, Deploy fails at the **db** step naming
   the setting — and **no** Vercel deployment is created. With an Atlas URI it proceeds as today.
7. **Delete deployment** destroys both provider deployments, marks the record `deleted`, clears the
   URLs, returns deploy to `empty` and stales `validate`; the app database still exists and the data
   browser still works. A provider that refuses is reported, not swallowed, and the record still
   ends up deleted.
8. Deleting a *project* also tears its deployments down, reporting anything it could not reclaim.
9. `.env.example` states the Atlas-cluster / local-sandbox split and its consequence.

## Tests to write

- `tests/deploy/test_orchestrate_env_mirroring.py` — `set_env` is called once per deployed target,
  after that target's deploy, with the same map that went inline; a raising `set_env` still yields a
  `live` deployment with the failure in the log.
- `tests/deploy/test_vercel_adapter.py` (extend) — secret-shaped keys go up as `encrypted`, plain
  ones as `plain`; the upsert URL/params are unchanged.
- `tests/deploy/test_deployment_refs.py` — refs for both targets are persisted on the record, and a
  record without them (the migration case) is still readable.
- `tests/deploy/test_provider_logs_route.py` — `be`/`fe` return provider lines; `db` returns empty
  with a reason; a raising provider returns empty + `error`, never a 5xx; ownership is enforced.
- `tests/deploy/test_db_reachability_guard.py` — localhost / `127.0.0.1` / bare-service-name /
  blank-with-local-`MONGODB_URI` all refuse **before** any provider call (assert the fake provider
  recorded zero deploys); an Atlas-shaped URI passes; a BYO URI is exempt.
- `tests/deploy/test_deployment_teardown.py` — destroy is called per stored ref; a raising provider
  is reported and the record is still marked `deleted`; stage states end `empty`/`stale`; the app
  database is untouched (the dropper is never called).
- `tests/projects/test_project_service.py` (extend) — project delete tears deployments down and
  reports failures as warnings without blocking the delete.
- `frontend/src/features/deploy/DeployPanel.test.tsx` — BYO shows Stored/Missing rows and disables
  Deploy while a required credential is missing; seamless renders as before; delete asks for
  confirmation, then calls the endpoint and returns the panel to its un-deployed state.
- `frontend/src/features/deploy/NodeDrawer.test.tsx` (extend) — provider/pipeline log toggle,
  provider-error fallback, and the pre-phase-62 record path.

## Manual verification steps

1. Set `VERCEL_TOKEN` and an Atlas `APP_DB_CLUSTER_URI`; leave `APP_DB_SANDBOX_URI` at the local
   `appdb` service. Deploy a built project.
2. Open both projects in the Vercel dashboard: env vars are listed. Redeploy the API project *from
   Vercel* and confirm it comes up working.
3. Open the backend node: Vercel's build log renders; switch to Pipeline and see ForgeFlow's steps.
4. Switch to **Bring your own** with no credentials stored: both rows read Missing and Deploy is
   disabled. Add a Vercel token in Settings; the row flips to Stored and Deploy enables.
5. Point `APP_DB_CLUSTER_URI` at `mongodb://localhost:27017` and deploy: it refuses at the db step,
   naming the setting, and nothing appears in Vercel.
6. Delete the deployment: both Vercel deployments disappear, the panel returns to un-deployed,
   Validate goes stale, and the data browser still shows the project's data.

## Rollback / cleanup notes

- Additive throughout: `Deployment.refs` defaults to empty, so existing records keep working with
  the pipeline log and no delete. No migration.
- Reverting task 2 leaves already-mirrored project env vars in place on Vercel; they are harmless
  (the inline env still wins for a ForgeFlow-created deployment) and removable from the dashboard.
- The DB guard (task 5) is the only change that can *stop* a deploy that previously started. It is
  deliberate — the deploys it stops could never have worked — but it is also the first thing to
  loosen if a legitimate cluster address trips it.

## Design notes

- **Why mirror rather than move.** Deployment-level env is what makes *this* deployment correct at
  the instant it is created; project-level env is what makes every *later* deployment correct,
  including ones ForgeFlow did not create. They answer different questions, and the cost of keeping
  both is one idempotent upsert per target.
- **Why the pipeline log survives.** It is the only record of what ForgeFlow itself did — which
  database mode was chosen, why the frontend was skipped, why an env write failed. Vercel's build
  log cannot contain any of that. The bug was calling it "Logs" on a node whose provider has logs of
  its own, not keeping it.
- **Why the guard lives in the orchestrator, not the config layer.** A localhost cluster URI is
  perfectly valid for preview, the data browser and local development — it is only wrong at the
  moment a *deployed* function has to reach it. Rejecting it at config load would break the ordinary
  laptop setup for everyone who never deploys.
- **Golden Rules preserved.** No new stack, provider or credential kind. No untrusted code runs in
  the control plane. Secrets stay vault-resolved at call time and are never returned to the client —
  the readiness strip shows *presence and last4*, which `GET /credentials` already exposes, and the
  new logs route returns provider text, not env. Every added provider call is bounded and fail-soft.
  `deploy ⇐ build` and `validate ⇐ deploy` remain the only hard prerequisites — task 6 *restores*
  the second one rather than weakening it.
