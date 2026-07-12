# Phase 27 — Test generation agent

## Title & ID

- **ID:** `phase-27`
- **Title:** Test generation agent
- **Epic:** Epic 5 — Requirements & Test Generation
- **Milestone:** M3

## Goal

Compile a `RequirementSpec` into runnable unit (Vitest/Jest+supertest) and Playwright E2E tests, written into the sandbox as `TestSuite` artifacts traceable to acceptance criteria.

## Context

Implements D6's second half and feeds the crown-jewel repair loop (Epic 6): tests are the oracle the repair loop optimizes against. Traceability (criterion → test) is what makes minimal repair context possible (§9).

## Depends on

- **Phase IDs:** `phase-21`, `phase-25`
- **External:** `ANTHROPIC_API_KEY`.

### Prerequisite check

- `phase-25` ✅ (spec with stable criterion ids), `phase-21` ✅ (tools to write into the sandbox). Skeleton test configs from `phase-22` present.

## Detailed tasks

1. **Test-gen agent (`app/agents/testgen.py`).** Sonnet tool-use loop mapping **each acceptance criterion** → ≥1 test:
   - `kind=unit` → Vitest (FE) / Jest+supertest (BE) in the skeleton's test conventions.
   - `kind=e2e` → Playwright specs against the preview base URL.
   - Write tests via `phase-21` tools; annotate each test with its `criterion.id` (comment/tag) for traceability.
2. **Non-linearity.** If no `RequirementSpec` exists (skipped requirements), warn and offer to (a) capture minimal requirements or (b) infer a minimal spec from design/build artifacts — user chooses (D12). Inferred specs are marked as such.
3. **Artifacts.** Persist `TestSuite` artifacts `{kind, files[], generated_from:[criterion ids], version}` (§6), versioned.
4. **Routing/cost.** Planning/summaries → Haiku; test authoring → Sonnet; token/₹ on the `Run`; budget caps respected.

## Out of scope for this phase

- Running tests + parsing results (`phase-28`), the repair loop (`Epic 6`), the test/repair UI (`phase-32`).

## Acceptance criteria

- **Each acceptance criterion yields ≥1 test**, tagged with its `criterion.id`.
- Generated suites are **runnable** in the sandbox (correct configs, imports, selectors) using the skeleton conventions.
- Missing-requirements path warns and offers capture-or-infer; inferred specs are labeled.
- `TestSuite` artifacts trace back to the exact criteria.

## Tests to write

- `backend/tests/agents/test_testgen_traceability.py` — every criterion → ≥1 tagged test (mock model).
- `backend/tests/agents/test_testgen_runnable.py` — generated suite executes in a sample workspace (integration behind marker).
- `backend/tests/agents/test_testgen_no_requirements.py` — skipped-requirements path warns + offers capture/infer.

## Manual verification steps

1. With a 2-feature spec, run test-gen → unit + Playwright files appear in the IDE, tagged by criterion.
2. Run them (via `phase-28`) → they execute (pass/fail meaningfully).
3. Skip requirements, run test-gen → warned + offered capture/infer.

## Rollback / cleanup notes

- Remove generated test files (revert workspace commit) + `TestSuite` artifacts. Reversible.

## Design notes

- Criterion-tagged tests are the backbone of the repair loop's "which requirement failed" mapping (`phase-29`), so tagging is mandatory, not cosmetic.

## Status

⬜ todo
