# Phase 66 — Stage reset: take back a "done" without re-running anything

## Title & ID

- **ID:** `phase-66`
- **Title:** Stage reset. A finished stage can be marked not-done again; Deploy resets to a blank slate.
- **Epic:** Epic 1 — Orchestration Core *(added per user directive)*
- **Milestone:** M5 *(demo reliability)*
- **Status:** ✅ done *(2026-10-02)*

## Goal

Any finished stage can be put back to "not done" with one click, without deleting or re-running
anything. Deploy (and Validate) go further and return to a blank slate: the live deployment is
taken down and both panels read as never run.

## Context

**Reported 2026-10-02 (user).** A build takes too long to run live, so the team demos with two
projects. A new one shows Requirements and Design. A second, already built to Build, shows Test,
Deploy and live validation. After each demo that second project's later stages read *Complete*, and
there was no way back short of rebuilding a new project up to Build every time. The request: *"a
button in which I can reset that stage as not done or unverified so once shown that same can be
reverted back and then can be demonstrated in the next demo."*

**What already existed, and why it was not enough.** `unskip` (D12, extended 2026-08-29) undoes a
skip, and a build approval marks a build complete. Nothing took a `complete` back. `refine` reopens
a stage, but it is a request for *more work*: it marks downstream stale and, for Build, runs the
agent.

**Decisions (user-selected, 2026-10-02).**

1. **Build and Test (and the other non-deploy stages) reset the status only.** `complete` becomes
   `awaiting_user` ("Needs you"). Nothing is deleted and no other stage moves. The work stays on
   disk, so Build's existing **Approve** (or a stage's **Proceed**) puts it back to `complete` for
   free.
2. **Deploy resets to a blank slate.** The deployment is actually destroyed at the provider, so the
   next demo shows a fresh deploy. Deploy goes back to `empty` ("Not started") and its panel looks
   exactly as it did before the first deploy. Validate goes blank with it, because its evidence was
   about a deployment that no longer exists.
3. **Validate on its own also resets to blank** (live results hidden, deployment left live). This
   was not stated explicitly. It follows from the user grouping the live-test stage with Deploy as
   "completely blank, as if nothing was done yet for those stages".

**Locked decisions honoured.**
- **Non-linearity (D12).** Reset is a new, always-reversible move, gated by no hard prereq. Deploy
  resetting Validate *restores* `validate ⇐ deploy` rather than weakening it, the same move
  phase-62's teardown makes.
- **Artifacts are versioned, never overwritten (§7).** Nothing is deleted. A blank-slate reset
  records a `cleared_at` cut-off on the stage, and the panels' "latest" reads ignore anything older.
  The retired deployment record, the old validation report and the old live run all remain as
  history.
- **Data is never dropped by a hosting action (D8).** A deploy reset is phase-62's teardown (hosting
  only), so the project database is untouched.
- **The conductor owns legality (§8).** Reset is an intent action answered by the conductor itself,
  like `unskip`. No stage handler runs and nothing is charged.

## Depends on

- `phase-06` (state machine) · `phase-08` (conductor) · `phase-62` (deployment teardown) — all ✅.

## Detailed tasks

1. **State machine** (`app/projects/state_machine.py`). New `Action.reset`, with the per-stage policy
   in `RESET_TO_BLANK = {deploy, validate}` and `reset_target()`:
   - status-only stages: legal only from `complete`, and go to `awaiting_user`;
   - blank stages: legal from anything but `empty`/`in_progress`, and go to `empty`. Every strictly
     downstream stage also goes to `empty`, and each is reported on `ApplyResult.cleared`.
2. **Persistence** (`app/db/models/stage_state.py`, `app/db/repos/repos.py`).
   `StageState.cleared_at`, written by `StageStateRepo.mark_cleared` and read by `cleared_at()`.
   Status writes leave it alone, so the cut-off survives the stage running again.
3. **Service** (`app/projects/service.py`). `transition_stage` empties and marks each cleared stage,
   emits a `stage.transition` for every stage that moved, and on a deploy reset tears down every live
   deployment through the existing fail-soft `_destroy_deployments`. `TransitionOutcome` gains
   `cleared`, `deployments_destroyed` and `warnings`. Because the service does this rather than the
   conductor, the raw `POST /stages/{stage}/transition` path cannot reset deploy without the
   teardown.
4. **Conductor** (`app/orchestrator/conductor.py`). `IntentAction.reset` → `Action.reset`, answered
   by `_reset` with no handler. A reset is refused while the stage's work is in flight (its
   `conductor:<stage>` run plus `codegen:build` / `repair:loop` / `deploy` / `validate`, and for a
   blank reset every downstream stage's too). Otherwise a refine-driven build, which leaves the DB
   status `complete` while it runs, would put `complete` straight back when it finished. The reply
   says what happened and how to undo it, and names anything the provider refused to remove.
5. **Blank panels** (`app/deploy/deploy_router.py`, `app/testing/validate_router.py`).
   `GET /deploy/latest` (and its logs and delete) and `GET /validate/latest` / `live-run` ignore
   records created before the stage's `cleared_at`.
6. **UI** (`frontend/src/features/workspace/StageReset.tsx`, `useStageReset.ts`, `stageMeta.ts`,
   `StageNavigator.tsx`, `Workspace.tsx`, every stage panel).
   - A **Reset** button in each stage panel header. It renders only when `canReset`, so Build shows
     it where *Approve* hides once complete.
   - A **Reset** item in the stage navigator's menu.
   - Deploy's reset asks for confirmation first (it destroys the live site); every other reset is a
     badge change and does not.
   - After a reset, every query it can change is refreshed (stages, messages, build report,
     deployment, deploy logs, validation report, live run).

## Out of scope for this phase

- Deleting artifacts, test runs, build files or the app database. Reset never destroys data.
- A "reset everything from here" bulk action. Deploy's reset already covers Validate, which is the
  demo's whole tail. Test is one more click.
- Stuck `in_progress` recovery. A stage that is genuinely working cannot be reset, by design.

## Acceptance criteria

1. A complete Build or Test resets to "Needs you" with no handler run, no artifact touched and no
   other stage moved. Approve/Proceed returns it to complete without a run.
2. A status-only reset is rejected unless the stage is complete.
3. A Deploy reset destroys the live deployment at the provider, sets Deploy and Validate to "Not
   started", and leaves Build/Test as they were. The deployment record survives, marked `deleted`.
4. After a Deploy reset, `GET /deploy/latest`, its logs, `GET /validate/latest` and
   `GET /validate/live-run` all return `null`. A deploy made after it is shown normally.
5. A provider refusing teardown does not block the reset, and the leftover is named in the reply.
6. A reset is refused while the stage's (or, for deploy, validate's) work is running.
7. The UI offers Reset only where it is legal, and Deploy's asks for confirmation.

## Tests to write

- `tests/projects/test_state_machine.py` — the reset truth table (status-only vs blank, legality,
  downstream emptied, no hard-prereq gate, restore point cleared).
- `tests/orchestrator/test_conductor_reset.py` — the conductor-level guarantees above, against a
  recording provider stub.
- `tests/deploy/test_reset_blank_slate.py` — the panel reads over HTTP before/after a reset, and a
  status-only reset hiding nothing.
- `tests/projects/test_transition_events.py` — a deploy reset names what it cleared and emits
  validate's own transition. Every other transition keeps its payload shape.
- `StageNavigator.test.tsx`, `BuildPanel.test.tsx`, `DeployPanel.test.tsx` — where Reset is offered,
  what it sends, the deploy confirmation, and the blank panel after it.

## Manual verification steps

1. Take a project through Build → Test → Deploy → Validate.
2. **Test → Reset**: the badge reads *Needs you*; results are still listed; *Proceed* marks it
   complete again.
3. **Deploy → Reset → Reset and take it down**: the Vercel deployments disappear from the Vercel
   dashboard. The Deploy panel shows the empty topology and a **Deploy** (not Redeploy) button.
   Validate shows *Not validated yet*. Build is still *Complete*.
4. Deploy again: the new deployment appears as normal.

## Rollback / cleanup notes

Additive. `cleared_at` defaults to `None` and older rows parse unchanged. Reverting the code removes
the action, and any `cleared_at` already written is then simply ignored.

## Follow-up (2026-10-02, reported from a live reset)

The first live Deploy reset left both Vercel projects in the dashboard. The teardown reported
`vercel rejected the credentials (403)` for each target, and the redeploy after it failed with both
nodes red and no reason anywhere on screen. Fixed:

- **Teardown deletes the Vercel project, not the deployment.** Vercel will not delete a project's
  *current production* deployment, which is the one ForgeFlow ships. Even a deleted deployment
  leaves the project, its alias and its env vars in the dashboard. `VercelDeployProvider.destroy`
  now issues `DELETE /v9/projects/{name}` for projects ForgeFlow named (`MANAGED_NAME_PREFIX`,
  `forgeflow-`). Anything else falls back to the single deployment. A 404 counts as success, so the
  records of several redeploys, which all name the same project, tear down idempotently. Render
  deletes are idempotent too.
- **A refused teardown is retried, not orphaned.** The retired record keeps the refs the provider
  refused, and the next reset (or project delete) retries them. Previously they were dropped, which
  left a live site nothing in ForgeFlow pointed at.
- **A failed deploy says why.** A provider error now names the provider's own error code
  (`403, forbidden`; never its free-text body, which can quote the request). A refused token comes
  with a hint naming which token to replace. Each failed node keeps its reason and hint on the
  record. The deploy reply lists them instead of "check the step logs", the node drawer shows them
  after a reload, and a target that never reached the provider no longer claims to "predate
  provider log support".

A 403 on the *deploy itself* means Vercel refused the token: expired, revoked, or for a different
account/team than the projects. No code change can fix that. The message and hint now say so and
name the setting to replace.

## Design notes

- **Why a cut-off and not a delete.** "Blank as if nothing was done" is about what the panel
  *shows*. Deleting the deployment record would lose the audit trail phase-62 deliberately keeps,
  and deleting validation evidence would break §7. A per-stage timestamp is one field, one filter
  per read, and survives the stage running again.
- **Why "Needs you" rather than "Not started" for Build/Test.** The work exists. "Not started" would
  claim otherwise, and Build's panel would offer a *Build* instead of *Approve*.
- **Why the service tears down, not the conductor.** The raw transition endpoint also reaches the
  state machine. A deploy reset that skipped the teardown would hide a still-live, still-billing
  site.
