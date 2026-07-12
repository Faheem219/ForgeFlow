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

⬜ todo
