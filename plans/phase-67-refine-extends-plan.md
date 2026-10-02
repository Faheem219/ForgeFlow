# Phase 67 — A refine extends the phase plan; it never replaces it

## Title & ID

- **ID:** `phase-67`
- **Title:** Change requests extend the build plan. Small ones are made directly; large ones append
  their own phases. The plan that built the app is never re-planned.
- **Epic:** Epic 13 — Build Reliability *(amends `phase-56` and `phase-60`)*
- **Milestone:** M5
- **Status:** ✅ done *(2026-10-02)*

## Goal

Refining a finished build behaves like editing an existing codebase. A small request is fixed
directly. A big one gets a few phases of its own, appended to the existing plan. In both cases
everything already built (its phases, its plan file, its code) is kept exactly as it was.

## Context

**Reported 2026-10-02 (user).** *"Once the build is complete and I am making a new change in the
refine phase, the model wipes out the phase plan entirely and builds new. This many a time leads to
breaking of other functionalities… if it is a small change then fix directly, else if the fix is big
then and only then make a phase plan."*

**Root cause, traced.** phase-60 routed a refine by scope, but its `large` branch was the *whole-app*
planner:

1. `BuildPlanner.plan` got the instruction appended as one line (*"plan ONLY for it"*). It was still
   told to produce **at least 3 phases** and that **the final phase is always `wiring`**, which
   rewrites routes, the layout, the HTML title and the placeholder tests. For a change to a working
   app, that phase re-touches the files every other feature depends on. That is the breakage.
2. `write_workspace(plan, {})` rewrote `phase-plan/plan.json` and `README.md` from scratch, with
   every status reset to pending.
3. `_resume_point` matched the new plan's ids against the last report. New ids meant
   `resume_from = 0`, so every planned phase ran. Worse, a new phase that happened to reuse an old
   done id (`be-core`) would have been **skipped**.
4. The new report recorded only the new plan's phases, so the Build panel's phase history vanished.
   The small path had the same last problem: its report held just the one synthetic phase.
5. The classifier's tie-break was *"when genuinely unsure, answer LARGE"*, and every classifier
   failure fell back to `large` too. That is the opposite of the user's rule.

**Decisions (from the user's directive).**

- Small → direct fix (phase-60's single synthetic phase, unchanged in mechanics).
- Large → only then plan, and plan **only the change**, appended to the existing plan.
- When unsure, or when the classifier fails or is unparseable → small.

**Locked decisions honoured.**
- **Cost discipline (Golden Rule 7).** Planning still routes to `MODEL_ROUTING`, and the change
  planner gets a smaller prompt than the whole-app one (no design HTML). Fewer phases run. The
  verification gate after a change is unchanged, so a cheap path is still a proven one.
- **Bounded (D7 / Golden Rule 5).** No new loop. A change's phases run through the same
  `PhaseRunner` bounds, and phase-55 verification and repair run exactly as before.
- **Artifacts versioned (§7).** The merged plan is a new `build_plan` version, and each report a new
  `build_report` version.
- **Non-linearity (D12).** No new prerequisites. A change to a build with no recorded plan still
  works: the repository map stands in for it.

## Depends on

- `phase-56` (phased build) · `phase-60` (scope-aware build) · `phase-64` (phase evidence) — all ✅.

## Detailed tasks

1. **Plan model** (`app/agents/build_plan.py`).
   - `PlanChange` and `BuildPlan.changes` record each request and the phases it added.
   - `BuildPlan.from_dict` reads a persisted plan back exactly (order kept; it is not re-validated).
   - `extend_plan(prior, change, instruction)` appends without re-ordering. It renames ids that
     collide with history, and remaps `depends_on` to follow.
   - `render_markdown` lists each change under its own `## Change N: …` heading below the original
     plan.
   - The synthetic small-change phase is titled after the request (`Change: fix the button`), so
     the phase history reads as a log of what was asked.
2. **Change planner** (`app/agents/prompts/codegen.py`, `app/agents/build_phases.py`).
   - `CHANGE_PLAN_SYSTEM_EXTRA` + `change_plan_user_prompt`: the app is already built, plan only the
     new phases (1..N), never repeat or redo an existing phase, extend files in place, no
     routing/identity phase unless the change adds a page.
   - `BuildPlanner.plan_change` merges, persists and writes the workspace with every earlier status
     intact. An unusable reply becomes the single direct-change phase, **not** `fallback_plan`
     (which plans a whole app).
   - `BuildPlanner.latest` reads the stored plan back.
   - `PROMPT_VERSION` bumped.
3. **Codegen** (`app/agents/codegen.py`). A change request is an instruction, on a build with feature
   code on disk, that is not a fresh rebuild.
   - `_history` rebuilds the prior plan one-to-one from the last report's phases, enriched by the
     stored plan.
   - Both scopes extend it; the large one goes through `plan_change`.
   - The run starts at `resume_from = len(history)` with the history carried.
   - `BuildReport.prior_phases` marks the carried prefix. `run_phases` and `phases_complete` judge
     only what this build ran. An earlier unfinished phase is named in the notes and left for a
     Rebuild.
4. **Stage handler** (`app/orchestrator/stages/build.py`). The wrote-nothing count and the summary
   use `run_phases`. A change's summary says how many earlier phases were kept, not re-run.
5. **Classifier** (`app/agents/build_scope.py`).
   - The tie-break is SMALL.
   - LARGE is reserved for work clearly too big for one pass.
   - Classifier failure and an unparseable reply fall back to `small`. Both are, by construction,
     changes to an existing app.
6. **UI** (`frontend/src/features/build/`).
   - `BuildReportData.prior_phases`.
   - The scope badge reads *"Planned change · N new phases"* for a large change.
   - Earlier phases stay on the rail, dimmed, with the change's phases below a hairline.

## Out of scope for this phase

- **Rebuild** (proceed with no instruction) and **Force a planned build** still plan the whole app;
  they are explicit requests for that.
- Re-running earlier unfinished phases as part of a change. A change does the change. A Rebuild
  finishes the plan.
- Appending small changes to `phase-plan/`. phase-60 keeps the small path off the plan file. The
  report (and the Build panel) records them.

## Acceptance criteria

1. A large refine on a built app runs the **change planner** (not the whole-app planner). The plan
   file keeps every earlier phase with its status and gains a `## Change N` section. Only the new
   phases are implemented and committed.
2. A small refine runs one direct pass, leaves `phase-plan/` untouched, and the report keeps every
   earlier phase.
3. A change phase whose id collides with an earlier one is renamed and runs. It is never skipped as
   "already done".
4. An unusable change plan becomes one direct pass, never a whole-app plan.
5. An earlier unfinished phase does not hold a change out of `complete`, and it is named in the
   notes.
6. When the classifier is unsure, fails or is unparseable, a change is made directly.
7. The Build panel shows earlier phases as history and labels a planned change by what it added.

## Tests to write

- `tests/agents/test_build_plan_extend.py` — extend/rename/no-reorder, persistence round-trip, the
  sectioned plan file, change titles, completion judged on this build's phases.
- `tests/agents/test_codegen_change_extends_plan.py` — the end-to-end guarantees above through the
  real client and tool registry with a scripted transport.
- `tests/agents/test_build_scope.py` — the SMALL tie-break and fallbacks.
- `BuildPanel.test.tsx` — the *Planned change* badge and the history rows.

## Manual verification steps

1. Build an app to complete. Open `phase-plan/README.md` and note its phases.
2. Refine with a one-line fix: one pass, `phase-plan/` unchanged, and the rail shows the earlier
   phases dimmed plus `Change: …`.
3. Refine with a new feature (*"add tags to todos, with a tag filter on the list page"*). The
   README gains `## Change 1: …` below the original plan, only the new phases run, and every
   earlier feature still works.

## Rollback / cleanup notes

Additive. `prior_phases` and `changes` default to 0/empty, so older reports and plans read
unchanged. Reverting restores phase-60's re-plan behaviour; already-merged plans remain valid
plans.

## Design notes

- **Why the history is rebuilt from the report, not the stored plan.** Small changes do not touch the
  plan file (phase-60), so the report is the only complete record of what ran. The stored plan
  contributes goals and files for the phases it knows.
- **Why the merged plan is not re-validated.** `validate()` topologically sorts by kind, which would
  slot a new backend phase in front of an old frontend phase. The build runs a change by slicing
  past the history, so the order must be exactly history-then-change.
- **Why the tie-break flipped.** phase-60 chose LARGE because an under-planned large change "produces
  a half-built app". With a change now planned as a delta, over-planning is no longer the safe
  direction: the cost of a wrong LARGE used to be a rewritten app. The user's rule is explicit, and a
  misjudged small change still gets the full verification gate and bounded repair.
