# Phase 40 — Live-failure repair path & UI

## Title & ID

- **ID:** `phase-40`
- **Title:** Live-failure repair path & UI
- **Epic:** Epic 8 — Live Validation
- **Milestone:** M4

## Goal

Close the loop when the live site fails: diagnose → repair (re-enter Build) → redeploy → re-validate, with a validation UI that ends on the final live link.

## Context

The full "deployed URL delivered, and *verified*" promise (D1, M4). Reuses the bounded repair loop (`phase-31`) against live failures and the deploy orchestrator (`phase-37`) to redeploy — non-linearity in action (D12): a live failure re-enters Build, not a dead end.

## Depends on

- **Phase IDs:** `phase-31`, `phase-38`, `phase-39`
- **External:** a deployment + live test capability.

### Prerequisite check

- `phase-39` ✅ (live results), `phase-31` ✅ (repair loop), `phase-37/38` ✅ (redeploy + topology).

## Detailed tasks

1. **Validate handler (`app/orchestrator/stages/validate.py`).** `proceed` → run live validation (`phase-39`); on failure → **diagnose** (map live failures to code via the analyzer, `phase-29`, using the deployed artifacts + logs) → run the **bounded repair loop** (`phase-31`) in Build → **redeploy** (`phase-37`) → **re-validate**; bound the outer loop too (max redeploy cycles, config) and escalate on cap. On success, record the final live URL.
2. **Diagnosis inputs.** Combine live `TestRun` failures + BE/FE deploy logs (`phase-35`) into the repair context; env/config issues surfaced distinctly from code bugs (an env fix may not need a code patch).
3. **Validation UI (`src/features/deploy/Validate.tsx` or `features/validation/`).** Live pass/fail per test, the repair→redeploy→revalidate timeline, escalation prompt (reuse `phase-32`), and a prominent **final live link** on success.
4. **Events.** `validate.status`, reuse `repair.*` + `deploy.status`.

## Out of scope for this phase

- New repair/deploy mechanics (reused). Eval harness (`Epic 10`).

## Acceptance criteria

- An **induced live failure** triggers repair → redeploy → re-validate; on success the UI delivers the **final live link**.
- The outer (redeploy) loop is **bounded** and escalates on cap with a clear summary (no unbounded redeploy loops).
- Env/config failures are distinguished from code failures and handled appropriately.

## Tests to write

- `backend/tests/orchestrator/stages/test_validate_success.py` — live pass → final link recorded.
- `backend/tests/orchestrator/stages/test_validate_repair_cycle.py` — induced failure → repair→redeploy→revalidate → pass (mocked deploy/live runner + repair).
- `backend/tests/orchestrator/stages/test_validate_outer_bound.py` — persistent failure → bounded redeploy cycles → escalation.
- `frontend/src/features/deploy/Validate.test.tsx` — timeline + final link + escalation (mock).

## Manual verification steps

1. Deploy a good build → validate → green → final link shown.
2. Induce a live failure → watch diagnose→repair→redeploy→revalidate → recovers → link.
3. Induce an unfixable live failure → bounded cycles → escalation summary.

## Rollback / cleanup notes

- Revert repair commits; redeploy a known-good build; clean live resources. Reversible via provider APIs.

## Design notes

- The **outer** redeploy loop gets its own bound (not just the inner repair cap) so a broken deploy config can't cause infinite redeploys — same "never unbounded" invariant applied one level up (D7).

## Status

✅ done — **M4 is complete**: a deployed URL is no longer just delivered, it is *verified*. A live
failure re-enters Build instead of being a dead end (D12), and the loop that does it is bounded at
both levels.

- `app/orchestrator/stages/validate.py` — `LiveValidationController.run(project, *, mode)`:

      validate (phase-39) → diagnose → repair (phase-31) → redeploy (phase-37) → re-validate

  Green on any cycle ⇒ `validated` + the final live URL. Each step emits `validate.status`, and a
  `ValidationReport` (outcome, cycle timeline, URL, reason, failing tests, the inner escalation) is
  persisted as a versioned artifact so the panel hydrates after a reload and eval (phase-44) can
  read it.
- **Bounded outer loop.** `validate_max_cycles` caps repair→redeploy→re-validate. The inner repair
  loop already caps *its* iterations; without this second cap a repair that keeps "succeeding"
  against a deployment that keeps failing would redeploy forever — the never-unbounded invariant
  (D7) applied one level up. Verified exactly: N cycles, N redeploys, then escalation.
- **Diagnosis (pure, `diagnose()`).** Live failures are classified **code** vs **env** from the
  failure text, the deployment status and the deploy log. An env verdict (connection refused, 5xx,
  a degraded deployment, a suite that could not run) escalates immediately with the evidence and
  **zero repair iterations** — a code patch cannot fix a missing env var, and pretending otherwise
  burns budget and time. The signal list is deliberately conservative: misreading a real bug as an
  env problem would skip a repair the loop could have made.
- **Faithful composition.** The repair loop is seeded with the *live* `TestRun`, so the agent works
  from production failures (the phase-39 `env=sandbox` guard keeps the two straight). A repair that
  escalates never redeploys; a redeploy that comes back degraded is not re-validated, because that
  would only produce noise; a `ProviderError` mid-redeploy is reported, not raised.
- `app/testing/validate_router.py` — `GET /validate/latest` (the report) and `GET /validate/live-run`
  (per-test production results, never a sandbox run). Read-only: validation runs through the
  conductor, so validate⇐deploy stays enforced in one place.
- `features/deploy/Validate.tsx` — mounted lazily as the Validate stage panel: a prominent **final
  live link** on success, the cycle timeline (failures → diagnosis → repair → redeploy), per-test
  live results with their criteria, live `validate.status` progress, and an escalation card whose
  reason is spelled out. When the *inner* loop escalated, phase-32's `Escalation` component is reused
  as-is so the resume-through-Build contract stays in one place.
- Config: `validate_max_cycles`; new `EventType.validate_status`.

**Acceptance:** an induced live failure triggers repair → redeploy → re-validate and the UI delivers
the final live link; the outer loop is bounded and escalates on cap with a clear summary; env/config
failures are distinguished from code failures and handled differently. Covered by
`test_validate_success.py` (5), `test_validate_repair_cycle.py` (6), `test_validate_outer_bound.py`
(9, incl. the pure diagnosis cases), `test_validate_router.py` (6) and `Validate.test.tsx` (9).

**Verification:** ruff/black/mypy clean across the phase-40 surface; backend **634 passed** (+26)
with the 8 known pre-existing env-only failures unchanged; frontend lint/typecheck/format clean with
**150 passed** (+9) and the panel code-split into its own chunk. Live-verified **9/9** against real
MongoDB: green→verified; failure→`validating → diagnosed → repairing → redeploying → validating →
validated` in 2 cycles; repair seeded with the live run; env→escalated with 0 repair iterations;
never-green→bounded at the cap; **through the real conductor** validate/proceed → `complete` with the
link surfaced, and validate-before-deploy still refused; both read endpoints serving the panel.

**Deferred (as scoped):** no new repair or deploy mechanics were written — both are reused. The eval
harness is Epic 10. A genuinely induced *live* failure against real Vercel/Render resources remains
deferred (billable); the cycle is proven end-to-end against real MongoDB with the three heavy
subsystems injected.
