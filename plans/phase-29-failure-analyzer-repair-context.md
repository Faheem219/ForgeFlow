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

✅ done

### Completion notes

- **Analyzer** [`app/agents/repair_context.py`](../backend/app/agents/repair_context.py) —
  `RepairContextAnalyzer.analyze(project, test_run)` assembles
  `RepairContext{failing_tests, target_files, diff, requirement_snippets, base_sha, head_sha,
  trimmed, notes, ref}`. Deterministic and **model-free**; its only I/O seam is a
  `ContextWorkspace` Protocol (read + git_info + diff), so it's fully testable without Docker.
- **Minimality (the invariant):** candidate files come *only* from each failure's
  `files_referenced` (phase-28 stack extraction) plus the failing test's own `file`. Nothing else
  is ever read — an unrelated file cannot enter the context even if it is huge.
- **Diff anchored on `last-passing`** (not "previous commit"), scoped to the implicated paths, so
  it reads as "what changed since it worked". Missing ref / `head == base` short-circuit to an
  empty diff with a note rather than a whole-repo diff.
- **Traceability:** `criterion_id` (phase-25 → 27 → 28) joins each failure to its acceptance text;
  only the failing criteria's snippets are pulled in.
- **Budgeting:** three new config knobs (`repair_context_max_chars`, `..._max_file_chars`,
  `..._diff_max_chars`). Files are admitted in **failure-frequency** order (ties → first-seen);
  the diff and oversized files are truncated and everything dropped/shortened is recorded in
  `trimmed`, so a trimmed context is never silently lossy.
- **Trace:** the context JSON is stored as a blob and linked to the run via a new
  `TestRun.repair_context_ref` (additive, defaults `None`) for audit + eval (phase-44).
- **Also added:** `WorkspaceService.diff()` exposing the phase-12 `WorkspaceGit.diff` primitive.
- **Verification:** ruff/black/mypy clean; an 18-check harness exercised the pure assembly
  (frequency ranking + deterministic tie-break, unrelated-file exclusion, truncation, result
  flattening, malformed-row tolerance, context shape). The three DB/workspace suites
  (`test_repair_context_minimal|_diff|_budget.py`) run on Linux/CI.
- **Out of scope (as planned):** the patching agent (phase-30), loop controller (phase-31), UI
  (phase-32). No code mutation happens here — context assembly only.
