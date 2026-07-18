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

✅ done — Executes the unit (Vitest/Jest) + Playwright suites in the sandbox and normalizes all three
reporters into ONE structured, per-test `TestResult` shape — the measurement layer the repair loop
(Epic 6) consumes, with the criterion-id join key carried through and referenced source files
extracted for minimal repair context (phase-29).

- `app/testing/models.py` — the unified `TestResult {name, status, framework, criterion_id?, file?,
  duration_ms?, failure{message, assertion, stack, files_referenced[]}}` + `summarize()` (green =
  something passed and nothing failed, so an empty/all-skipped run never advances last-passing) + the
  API schemas.
- `app/testing/parsers.py` — pure Vitest/Jest/Playwright JSON → `TestResult[]`. Jest & Vitest share
  one walk (Vitest emulates Jest's shape); Playwright recurses suites→specs→tests. The **criterion
  id** is recovered from the test title's `[<id>]` tag (phase-27) and **referenced files** are pulled
  from failure stacks (node_modules stripped, `/workspace/` prefix normalized). Tolerates garbage
  JSON (→ `[]`).
- `app/testing/runner.py` — runs each framework with a JSON reporter writing to a workspace file,
  reads it back through the FS layer, and parses it. A non-zero exit with no per-test results becomes
  a **suite-level failure** (compile error/timeout) so the repair loop always sees a failure. Captures
  combined stdout to one blob; persists a `TestRun` (results/failures as dicts, `suite_refs`,
  `stdout_ref`); on an all-green run **advances the `last-passing` git ref** (new
  `WorkspaceService.set_last_passing`) — the anchor for repair diffs; emits per-test + summary
  `test.result` events. All deps (exec/workspace/blobs) are injected behind Protocols, so the whole
  orchestration is testable without Docker.
- `app/testing/router.py` (wired in `app/api/app.py`) — `POST /projects/{id}/tests/run` (scope +
  filter), `GET …/tests/runs`, `GET …/tests/runs/{id}`, ownership-checked (a run under another
  project is a 404). New config `test_runner_timeout_s`.

**Acceptance:** suites run in the sandbox with structured, per-test, actionable failures (message,
assertion, stack, referenced files); `criterion_id` is carried through; an all-green run advances
`last-passing` (red/empty runs do not); `TestRun`s persist and `test.result` events stream. Covered by
`tests/testing/` (22): `test_parsers` (three reporters → one shape, criterion + referenced-file
extraction, garbage tolerance), `test_runner` (scope selection, combine, suite-failure synthesis,
suite_refs, events, dict round-trip + an opt-in `docker` real-Jest run), `test_last_passing_ref`
(green advances / red + all-skipped do not), and `test_tests_api` (run wiring + reads + ownership).

**Verification:** ruff/black + mypy clean across the phase-28 surface; testing suite **21 passed / 1
skipped** (the opt-in docker toolchain run) against a live MongoDB; full backend suite green (the one
pre-existing env-only `requirements_suggest` SDK-auth failure is unrelated). Live-verified end-to-end
against MongoDB. `Test*`-named domain/result classes emit the usual harmless pytest collection
warnings (same as the existing `TestRun`/`TestSuite`/`TestKind` models).
