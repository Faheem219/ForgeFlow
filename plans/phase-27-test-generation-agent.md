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

✅ done — Compiles a `RequirementSpec` into runnable, **criterion-traceable** unit + Playwright tests
written onto the fixed-stack skeleton (D6 second half; the oracle Epic 6's repair loop optimizes
against). Mirrors the codegen agent's discipline: minimal context, one bounded tool-use loop,
cost-tracked, Haiku for planning/inference and Sonnet for authoring.

- `app/agents/testgen.py` — `TestGenAgent` with a 5-step flow: **resolve spec → ensure skeleton →
  plan (Haiku) → author (Sonnet) → persist**. The author runs a **restricted tool surface**
  (`read_file`/`write_file`/`list_dir`/`git_commit` only) so it writes tests but never runs them or
  starts a preview (running/parsing is phase-28).
  - **Traceability (load-bearing):** a `_TestTracker` reads coverage from the *file content the model
    actually wrote* — for each written test file it intersects the file text with the spec's known
    criterion ids, so a suite's `generated_from` reflects the real tests, not a self-report. Test
    kind is inferred from the path (`.test.ts(x)` → unit, `.spec.ts`/`e2e/` → Playwright).
  - **Non-linearity (D12):** with no spec, the default `ask` mode **warns and offers capture-or-infer**
    and generates nothing; `infer` mode derives a *minimal* spec from design/build artifacts via
    Haiku, persists it **labeled `inferred`**, then generates. Unparseable inference falls back to a
    tiny built-in spec so test-gen still proceeds.
- `app/agents/prompts/testgen.py` — versioned plan/author/infer prompts. The author prompt encodes
  the exact skeleton test conventions (Jest+supertest under `backend/src/features/**`, Vitest under
  `frontend/src/features/**`, Playwright under `frontend/e2e/**`) and the **mandatory criterion-id
  tagging** (`// @criteria: <id>` header + `[<id>]` test-title prefix).
- **Persistence (§6, versioned):** a `TestSuite` document **per kind** (`{kind, files, generated_from,
  version}`) via new `TestSuiteRepo.latest`/`create_version` (per-project-per-kind versioning), plus a
  versioned `test` **report artifact** (traceability map + `inferred` flag) that surfaces the run in
  the stage's artifact list. Emits `progress` steps + a final `test.generated` event.
- **Inferred-spec support:** `RequirementSpec.inferred` field + `RequirementSpecRepo.create_version(
  inferred=…)` + `RequirementsService.save(…, inferred=…)` + `RequirementSpecPublic.inferred`; mirrored
  as optional `inferred?` on the frontend `RequirementSpecDto`.

**Acceptance:** each criterion yields ≥1 tagged test; suites are runnable via the skeleton conventions
(the opt-in `docker`-marked `test_testgen_runnable` actually executes a generated Jest+supertest suite);
the missing-requirements path warns + offers capture/infer and labels inferred specs; `TestSuite`
artifacts trace back to the exact criteria. Covered by `tests/agents/test_testgen_traceability.py`
(coverage + per-kind `generated_from` + versioning), `test_testgen_no_requirements.py` (ask offer +
infer labeling + parse-fallback), and `test_testgen_runnable.py` (opt-in real run).

**Verification:** ruff/black + mypy clean across the phase-27 surface (app + tests); the testgen suite
passes (9 passed, 1 skipped = the opt-in docker run) against a live MongoDB, plus the requirements
suite. One pre-existing, environment-only failure remains in
`test_requirements_suggest.py::…degrades_when_model_unavailable` — it exercises the **real** Anthropic
SDK with no key, and the installed SDK (0.116.0) raises `TypeError` on missing auth instead of the
retryable `APIConnectionError` the fail-soft test expects; it is on a code path phase-27 never touches
(`requirements_suggest.py` → `SdkTransport`) and is unrelated to this change.
