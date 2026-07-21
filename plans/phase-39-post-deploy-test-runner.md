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

✅ done — the E2E suite now runs against the **deployed URL** and yields a structured `TestRun`
marked `env=live`, reusing the *same* Playwright suites and the *same* phase-28 parsers as sandbox
runs. One oracle judges preview and production; only the base URL and the subset differ.

- `app/testing/live.py` — `LiveTestRunner.run(project, *, base_url=None)`: resolve the live URL (the
  latest `Deployment`'s FE URL, or an explicit override) → pick the subset → **warm up** → run
  Playwright with `PLAYWRIGHT_BASE_URL` → parse → persist `TestRun(env=live)` → emit
  `test.result {env: live}` for the validation UI. Every collaborator (exec, workspace, sandbox
  egress, the URL probe) is injected, so the whole path is testable without Docker or the network.
- `select_live_subset(requested, specs)` — pure. Production runs the `@smoke`/`@critical` slice,
  chosen from the tags the generated suite **actually carries**. An untagged suite runs in *full*:
  selecting a tag no test has would run zero tests, and a zero-test run reads as a pass.
- **Cold starts:** the control plane probes the URL (config-bounded attempts/interval) before handing
  the suite a browser, so free-tier spin-up is never recorded as a product failure. A site that never
  answers is reported plainly (`live:cold`) and the suite still runs — warm-up is best-effort, never
  a substitute for the verdict.
- The **last-passing** git ref is deliberately not advanced by a live run: that ref anchors the
  diff-aware repair loop to the workspace, and prod may be several commits behind.
- `TestRunRepo.latest(project_id, env=…)` + `repair_router` now asks for `env=sandbox`. Live runs
  share the `TestRun` collection, so without this the newest run after a validation would be the live
  one and Repair would patch workspace code from a production result (phase-40's path, not
  phase-31's). Regression-tested.
- Config: `live_test_selection`, `live_test_timeout_s`, `live_warmup_attempts`,
  `live_warmup_interval_s`, `live_test_retries`, `egress_network_name` (+ `.env.example`).

**Acceptance:** the live suite runs against the prod URL with per-test results marked `env=live`;
cold-start flakiness is mitigated by the warm-up probe + a Playwright retry; results stay traceable
to criteria through the same `[<id>]` tagging as phase-28. Covered by `tests/testing/test_live_runner.py`
(14) and `tests/testing/test_live_subset_selection.py` (8), plus `tests/sandbox/test_egress_network.py`
(7) for the isolation properties.

**Verification:** ruff/black/mypy clean across the phase-39 surface; full backend suite **608 passed**
(+29) with the 8 known pre-existing env-only failures unchanged. Live-verified 9/9 against real
MongoDB, including the real `_http_probe` against a real local HTTP server (True while served, False
once closed) — the one path the unit tests fake.

## Deviation: sandbox egress (approved)

The plan assumed live E2E could just run; it could not. The sandbox is created with
`network_mode=none` (phase-11) and the only existing relaxation — the preview network — is
`internal=True`, documented as having no route to the host or the internet. Reaching a deployed URL
needs egress that did not exist, and Golden Rule 3 forbids the alternative of running generated code
in the control plane. **This was raised and the user approved a temporary egress network.**

`build_egress_network_kwargs` creates a **routed** (`internal=False`) bridge that the sandbox joins
via `attach_egress_network` for one live run and leaves in a `finally` — including when the run
crashes or times out (tested). It is narrow in *time* rather than in reach, is still never the host
namespace, and is labelled `role=egress` for auditing. This is the widest relaxation of the sandbox
isolation default in the system and is **flagged for the phase-47 hardening review**, alongside the
phase-15 preview network.

## Deviation: smoke/critical tags

This phase's plan says the subset is "tagged in `phase-27`", but testgen emits no such tags — only
the `[<criterion-id>]` title tag. Rather than expand scope into phase-27, the tag convention is
defined here (`@smoke` / `@critical` in the test title, selected via Playwright's `-g`) with the
untagged-suite fallback above, so live validation is correct today and narrows automatically once
generated suites carry tags.

**Deferred (as scoped):** the repair→redeploy→revalidate loop and the validation UI are phase-40;
this phase stops at running live tests and persisting structured results. `LiveTestRunner` is the
entry point phase-40's validate handler calls. The live smoke path against a genuinely deployed app
remains deferred (it needs billable Vercel/Render resources).
