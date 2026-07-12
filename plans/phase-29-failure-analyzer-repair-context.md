# Phase 29 — Failure analyzer & minimal repair context

## Title & ID

- **ID:** `phase-29`
- **Title:** Failure analyzer & minimal repair context
- **Epic:** Epic 6 — Self-Healing Repair Loop (core)
- **Milestone:** M3

## Goal

Turn a failing `TestRun` into a **minimal, diff-aware** repair context: the failing test source + the specific files it exercises + the diff since the last passing run (§9 step 1).

## Context

The invariant that defines the crown jewel (D7, §9): *never feed the whole repo when a diff suffices.* Context minimality is what makes repair cheap, fast, and convergent — and what the eval harness (D13) ultimately validates.

## Depends on

- **Phase IDs:** `phase-12`, `phase-28`
- **External:** —

### Prerequisite check

- `phase-28` ✅ (structured results with `files_referenced`), `phase-12` ✅ (git diff + `last-passing` ref).

## Detailed tasks

1. **Analyzer (`app/agents/repair_context.py`).** Given a failing `TestRun`:
   - For each failing test: capture `{name, criterion_id?, assertion, stack, files_referenced[]}`.
   - Map failing tests → **source files under test** (from stacks + the test's criterion/imports).
   - Compute the **diff since `last-passing`** (`phase-12` `git_diff`), scoped to touched/referenced paths.
   - Assemble `RepairContext = {failing_tests[], target_files[]:{path,content}, diff, requirement_snippets[]}` — **only** the failing test source, the files it exercises, and the relevant diff. Exclude everything else.
2. **Budgeting.** Cap context size (config); if it would exceed the cap, prioritize by failure frequency/proximity and record what was trimmed.
3. **Determinism.** Pure/So testable given a `TestRun` + workspace snapshot; no model calls here.
4. **Trace.** Persist the assembled context (blob) linked to the run for auditability + eval.

## Out of scope for this phase

- The patching agent (`phase-30`), the loop controller (`phase-31`), UI (`phase-32`). This is **context assembly only**.

## Acceptance criteria

- Context **excludes irrelevant files** (only failing test + exercised files + diff present).
- The diff correctly reflects changes since the `last-passing` ref.
- `criterion_id` and requirement snippets are included when available (traceability).
- Context respects the size cap; trimming is recorded.

## Tests to write

- `backend/tests/agents/test_repair_context_minimal.py` — for a seeded failure, context includes exactly the failing test + exercised files + diff; unrelated files absent.
- `backend/tests/agents/test_repair_context_diff.py` — diff since last-passing is correct.
- `backend/tests/agents/test_repair_context_budget.py` — oversized context is trimmed by priority + recorded.

## Manual verification steps

1. Seed a bug, run tests (red), assemble context → inspect: only relevant files + a correct diff.
2. Confirm an unrelated large file is **not** in the context.

## Rollback / cleanup notes

- Context blobs removable per run. Reversible. No code mutation in this phase.

## Design notes

- Anchoring on the `last-passing` git ref (not "previous commit") makes the diff semantically "what changed since it worked" — the right signal for targeted repair.

## Status

⬜ todo
