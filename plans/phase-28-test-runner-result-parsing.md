# Phase 28 — Test runner & result parsing

## Title & ID

- **ID:** `phase-28`
- **Title:** Test runner & result parsing
- **Epic:** Epic 5 — Requirements & Test Generation
- **Milestone:** M3

## Goal

Execute unit + Playwright suites in the sandbox and produce **structured, per-test** results (pass/fail + failure detail + stdout) as `TestRun` artifacts.

## Context

The measurement layer the repair loop consumes (§9 inputs: "a `TestRun` with ≥1 failure"). Structured, per-test output with referenced source files is what makes minimal repair context (`phase-29`) possible. Also updates the git **last-passing ref** (`phase-12`) that anchors repair diffs.

## Depends on

- **Phase IDs:** `phase-13`, `phase-27`
- **External:** —

### Prerequisite check

- `phase-13` ✅ (exec/streaming), `phase-27` ✅ (suites exist) or any runnable tests in the workspace.

## Detailed tasks

1. **Runner (`app/testing/runner.py`).** Run unit (Vitest/Jest) and Playwright in the container via `phase-13`; configure JSON/JUnit reporters; capture combined stdout to a blob; time-bounded; scopes: `unit|e2e|all|filter`.
2. **Parsers (`app/testing/parsers.py`).** Parse Vitest JSON, Jest JSON, and Playwright JSON/JUnit → a unified `TestResult[]`: `{name, criterion_id?, status, duration, failure:{message, assertion, stack, files_referenced[]}}`. Extract referenced source files from stacks (input to `phase-29`).
3. **Models/artifacts (`app/testing/models.py`).** `TestRun {project_id, suite_refs[], results[], failures[], stdout_ref, created_at}` (§6), versioned.
4. **Last-passing ref.** On an all-green run, update the workspace's `last-passing` git ref/tag (`phase-12`) — the anchor for repair diffs.
5. **API + events.** `POST /projects/{id}/tests/run` (scope), `GET …/tests/runs`, `GET …/tests/runs/{id}`; emit `test.result` events (per-test + summary) for the UI.

## Out of scope for this phase

- Repair (`Epic 6`), the test UI (`phase-32`), live-URL runs (`phase-39`). This is run + parse + persist + events.

## Acceptance criteria

- Unit + Playwright suites run in the sandbox; results are **structured and per-test** with actionable failure info (message, assertion, stack, referenced files).
- `criterion_id` is carried through from tagged tests to results (traceability).
- An all-green run updates the `last-passing` git ref.
- `TestRun` artifacts persisted + versioned; `test.result` events stream.

## Tests to write

- `backend/tests/testing/test_parsers.py` — Vitest/Jest/Playwright fixtures → unified `TestResult[]`; referenced-files extraction.
- `backend/tests/testing/test_runner.py` — run a sample suite; failures captured; stdout blob stored (integration behind marker).
- `backend/tests/testing/test_last_passing_ref.py` — green run updates the ref; red run does not.

## Manual verification steps

1. Run `tests/run scope=all` on a workspace with a known failing test → structured per-test results; failure shows file+assertion.
2. Fix it, re-run → all green → `last-passing` ref advances.
3. `GET /tests/runs/{id}` → full structured result + stdout ref.

## Rollback / cleanup notes

- Remove `TestRun` artifacts + stdout blobs for test projects. Reversible.

## Design notes

- Normalizing three reporters into one `TestResult` shape isolates the repair loop from framework quirks — the analyzer/agent see one schema regardless of runner.

## Status

⬜ todo
