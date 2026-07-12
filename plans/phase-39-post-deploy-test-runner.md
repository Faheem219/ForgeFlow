# Phase 39 — Post-deploy test runner

## Title & ID

- **ID:** `phase-39`
- **Title:** Post-deploy test runner
- **Epic:** Epic 8 — Live Validation
- **Milestone:** M4

## Goal

Re-run smoke + critical E2E against the **live deployed URL** and produce a structured live `TestRun`.

## Context

Live-site validation is where incumbents are weak (D1). Reusing the Playwright suites (`phase-27/28`) against prod proves the deployment actually works, and feeds the live-repair path (`phase-40`). Hard prereq: validate⇐deploy (§8).

## Depends on

- **Phase IDs:** `phase-28`, `phase-37`
- **External:** a healthy deployment (live FE URL).

### Prerequisite check

- A completed `Deployment` with live URLs (`phase-37`); Playwright suites exist (`phase-27`); runner/parsers (`phase-28`).

## Detailed tasks

1. **Live runner (`app/testing/runner.py` extension / `app/testing/live.py`).** Point Playwright at the deployed FE URL (base URL override); run the **smoke** subset + **critical** E2E (tagged in `phase-27`); reuse the parsers (`phase-28`) → a structured live `TestRun` marked `env=live`.
2. **Selection.** Config/annotation picks the smoke/critical subset (full suite optional); time-bounded; retries for cold starts (Render free tier spin-up).
3. **Artifacts/events.** Persist a live `TestRun`; emit `test.result {env:live}` events for the validation UI.
4. **Handler hook.** Expose to the validate stage handler (`phase-40`).

## Out of scope for this phase

- The repair→redeploy→revalidate loop + UI (`phase-40`). Just running live tests + structured results.

## Acceptance criteria

- The live suite runs against the **prod URL** and yields structured, per-test results marked `env=live`.
- Cold-start flakiness is mitigated (retries/warm-up) so results reflect real failures, not spin-up.
- Results are traceable to criteria (same tagging as `phase-28`).

## Tests to write

- `backend/tests/testing/test_live_runner.py` — base-URL override; results marked `env=live`; parser reuse (mock a served app).
- `backend/tests/testing/test_live_subset_selection.py` — smoke/critical subset chosen correctly.

## Manual verification steps

1. After a deploy, run live validation → Playwright hits the prod URL → structured results.
2. Induce a live failure (e.g., misconfigured env) → shows as a real live failure, not a cold-start artifact.

## Rollback / cleanup notes

- Remove live `TestRun` artifacts for test projects. Reversible.

## Design notes

- Live runs reuse the **same** Playwright suites and parsers as sandbox runs (only the base URL + subset differ) — one oracle across preview and prod keeps validation honest.

## Status

⬜ todo
