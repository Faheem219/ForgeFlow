# Phase 56 — Phased build plan & phase-by-phase codegen

## Title & ID

- **ID:** `phase-56`
- **Title:** Phased build plan & phase-by-phase codegen
- **Epic:** Epic 13 — Build Reliability *(added per user directive)*
- **Milestone:** M2 *(hardens M2's stated outcome — a generated **running app**)*
- **Status:** ⬜ todo

## Goal

Replace the single monolithic implement call with a **structured phase plan** — written into a `phase-plan/` folder in the workspace and persisted as a versioned artifact — then implement it one phase at a time, each with its own small context, its own typecheck gate, and its own commit.

## Context

The **user directive**: *"the AI will make a phase plan that will be saved in a new folder and then based on that phase plan, phase by phase the implementation will begin — a small message to give context and then continue with the implementation… in the sandbox I have folders like the frontend and the backend, and I want there another folder created having the name phase plan."*

Today `codegen.py:276-294` runs **one** `run_tool_loop` for the entire implementation, with a prose plan (`_plan`, L357-379, ~200 words) as its only structure. Three consequences:

- **Context grows all build long.** One conversation carries every file write, every tool result, every correction — so the last feature is written with the worst signal-to-noise ratio and the highest token cost.
- **Nothing is verified until the end.** `phase-55` added integration verification; without phasing, a type error introduced in the first feature is only discovered after every feature is written, and the repair loop has to diff the whole app to find it.
- **Nothing is resumable at a useful granularity.** `impl.finished` is all-or-nothing; a build that stops at its turn bound resumes by re-reading the whole workspace inventory.

Phasing fixes all three: each phase gets a fresh, small prompt (*"a small message to give context"*), a typecheck gate that localises breakage to the phase that caused it, and a commit that makes the work resumable and the diff phase-attributable.

Locked decisions honoured: planning still routes to **Haiku** via `TaskKind.summarize` (D4 / Golden Rule 7); every loop is bounded, now per-phase as well as overall (D7 / Golden Rule 5); the plan is a **versioned artifact**, never overwritten (§7); the skeleton is still copied deterministically (phase-22/23); and `fallback_plan` guarantees the build still works with requirements-only, design-only, both, or neither (D12).

## Depends on

- **Phase IDs:** `phase-54` (skeleton demo removal — the phase prompts assume the placeholder rules), `phase-55` (verification, the classifier, `get_latest_of_kind`, the `max_turns` kwarg, the completion-gate vocabulary), `phase-23`, `phase-24`, `phase-14` (web IDE — the phase-plan folder appears in its tree).
- **External:** Docker/Colima for manual verification only.

### Prerequisite check

- `phase-54` and `phase-55` are ✅ — this phase **cannot** ship first: it writes a `build_plan` artifact under the same `(stage, type)` pair as `build_report`, which silently breaks incremental resume without `phase-55`'s `get_latest_of_kind`, and its N phase loops need `phase-55`'s `max_turns` kwarg or the turn ceiling becomes N×250.
- `cd backend && uv run pytest -q` and `cd frontend && pnpm test -- --run` green.

## Detailed tasks

1. **`app/agents/build_plan.py` (new).**

   ```python
   PhaseKind = Literal["data", "backend", "frontend", "wiring"]

   @dataclass(frozen=True)
   class BuildPhase:
       id: str; title: str; kind: PhaseKind; goal: str
       files: list[str]; depends_on: list[str]; done_when: list[str]

   @dataclass
   class BuildPlan:
       phases: list[BuildPhase]; assumptions: list[str]; notes: list[str]
       source: str                                        # "model" | "fallback"
       def validate(self, *, max_phases: int) -> BuildPlan  # unique ids, no cycles, topo-ordered, capped
       def to_prose(self) -> str                            # feeds the existing BuildReport.plan field

   def parse_build_plan(text: str) -> BuildPlan | None      # strips ``` fences, finds the first {…}
   def fallback_plan(features, *, has_design: bool) -> BuildPlan
   def render_markdown(plan, outcomes) -> str               # the human checklist
   ```

   **`fallback_plan` is mandatory, not defensive** — it is what satisfies D12. No requirements, no design, unparseable model output, or an empty phase list all land on a deterministic three-phase plan: `be-core` (models + routes + validation) → `fe-core` (pages + api calls) → `wiring` (index route, nav, app name). `parse_build_plan` never raises.

2. **Prompts (`app/agents/prompts/codegen.py`).** Add `PHASE_PLAN_SYSTEM_EXTRA` and `phase_plan_user_prompt(...)` beside the existing surfaces: strict JSON only, 3–`build_max_phases` phases, ordered **data → backend → frontend → wiring**, every phase independently typecheckable, `done_when` written as checkable statements. Add `phase_user_prompt(...)` for the implement step — see task 4. Bump `PROMPT_VERSION` (`prompts/__init__.py:11`).

3. **`BuildPlanner`.** Runs the plan step on `TaskKind.summarize` → Haiku (as `_plan` does today at `codegen.py:372`), parses, validates, falls back. Persisted in **both** places:

   - **Workspace** (`build_plan_in_workspace`, default `True`): `phase-plan/plan.json` + `phase-plan/README.md`, written through `ctx.workspace.write` so each update emits `fs.write` and streams live into the IDE. Rewritten after **every** phase, so the checklist visibly fills in. Folder name from `build_plan_dir` (default `"phase-plan"`) — a sibling of `frontend/` and `backend/`, as the user asked.
   - **Artifact:** `create_version(project_id, Stage.build, ArtifactType.code_change, text=json, meta={"kind": "build_plan", "phase_count", "phase_ids", "source"})`. A new `meta.kind` under the existing type — `ArtifactType` is fixed by `IMPLEMENTATION_PLAN.md` §6, so adding a kind is the compatible move. Note the version counter is shared with `build_report` (counters are per `project_id/stage/type`), so expect gaps in each sequence; harmless, but surprising if unexplained.

4. **`app/agents/build_phases.py` (new)** — `PhaseOutcome` (`id, title, kind, status, files, commit, attempts, note`) and `PhaseRunner`. Per phase:

   1. Emit `build.phase` `{phase_id, index, total, title, kind, status}`; mirror onto `Run.progress` and `save()` — reload-survivable.
   2. **One `run_tool_loop`** with a *small* context: this phase's `goal` / `files` / `done_when`, a ≤400-char "story so far" (previous phase titles + statuses), and only the design/requirements slice this phase needs. This is the *"small message to give context"* from the directive, and it is the main cost and quality lever. Turn bound `build_phase_max_tool_turns` via `phase-55`'s `max_turns` kwarg. Tool surface narrowed beyond `phase-54`'s exclusion: no `install_deps` / `start_preview` / `run_tests` for non-`wiring` phases — those belong to verification, and offering them wastes turns.
   3. **Per-phase gate:** `pnpm typecheck` scoped to the phase's package (`--filter frontend|backend`).
   4. **Bounded per-phase fix sub-loop**, up to `build_phase_max_fix_attempts` (default 2). Each attempt classifies the output first via `phase-55`'s `classify_output` — an `environment` verdict aborts the whole build immediately — then feeds the trimmed `tsc` errors back into the *same* conversation.
   5. `git_commit(f"build({phase.kind}): {phase.title}")` — one commit per phase, so `changed_paths` and diffs are phase-attributable.
   6. Emit `build.phase` with the terminal status; rewrite `phase-plan/`.

   A failed phase does **not** abort the build. It is recorded and carried into `phase-55`'s integration repair, where the diff-aware loop gets a shot with the whole app in view.

5. **`CodegenAgent.run` becomes the phase orchestrator (`app/agents/codegen.py`).**

   ```
   0. instantiate skeleton      (unchanged)
   1. survey                    → _existing_code (scaffold-free since phase-54)
   2. plan                      → BuildPlanner → artifact + workspace
   3. phases                    → for phase in plan.phases[resume_index:]: PhaseRunner.run(...)
   4. report                    → BuildReport with phases + plan
   ```

   **Resume:** read the newest `build_plan` and `build_report` (both via `phase-55`'s `get_latest_of_kind`) and restart at the first phase whose recorded status is not `done`. The **report is authoritative**; workspace file presence is corroborating evidence only — a half-written file exists but is not "done".

   **Deadline:** a `BuildBudget` holding `deadline = monotonic() + build_max_wall_clock_s`, checked between phases. Exceeded → stop, persist a partial report through the existing partial path (`codegen.py:295-318`), `stop_reason="wall_clock"`.

6. **`BuildReport` additions (`codegen.py:114-139`).** `phases: list[PhaseOutcome]`, `verification: dict | None`, `outcome: str`, `stop_reason: str | None` — **all defaulted**, so artifacts written before this phase still parse through `_previous_report`. `_persist_report` (L548-566) adds `phase_count`, `outcome`, `stop_reason` to `meta` so the UI can badge a report without fetching its content.

7. **Completion gate (`app/orchestrator/stages/build.py`).** Add `all(p.status == "done" for p in report.phases)` to `phase-55`'s conjunction, and `phases_incomplete` to its `stop_reason` vocabulary.

8. **Progress plumbing.**
   - `app/db/models/run.py:19-32` — add `phase_index: int = 0`, `phase_total: int = 0`, `phase_id: str = ""` to `RunProgress`. All defaulted, so existing Mongo documents deserialize unchanged. Update the `step` docstring (L28) with the new vocabulary.
   - `app/realtime/schemas.py:15-41` — add `build_phase = "build.phase"`.
   - `app/orchestrator/schemas.py:165-173` + `app/orchestrator/router.py:113-122` — carry the three phase fields onto `BuildActivityPublic`, so a reload reattaches to the right phase.

9. **Config (`app/core/config.py` + `app/core/config_registry.py` + `.env.example`).**

   | Key | Default | Purpose |
   |---|---|---|
   | `build_max_phases` | 8 | caps plan size and therefore cost |
   | `build_phase_max_tool_turns` | 60 | per-phase loop bound |
   | `build_phase_max_fix_attempts` | 2 | per-phase typecheck sub-loop |
   | `build_plan_dir` | `"phase-plan"` | workspace folder name |
   | `build_plan_in_workspace` | `True` | write the plan into the workspace |

10. **Frontend — defensive parsing first (`frontend/src/features/build/api.ts:52-59`).** `parseBuildReport` is an unvalidated `JSON.parse(...) as BuildReportData`, and `BuildPanel` calls `.length` / `.join` on four array fields with no guard (L168, 388, 407-425). Any old, truncated or hand-edited artifact crashes the panel. Add per-field defaulting with a `strArray` helper, including `phases: []`, **before** adding the new fields — that is what makes pre-rework artifacts render exactly as they do today.

11. **Frontend — the phase rail (`frontend/src/features/build/BuildPanel.tsx:295-363`).** Inside the existing `build-activity` section, between the label row (L314-340) and the file list (L341-361), add `data-testid="build-phase-list"`, rendered only when `phases.length > 0` so the panel is byte-identical for pre-rework reports. Source in precedence order: live `build.phase` events → `serverBuild.phase_*` (survives reload, from `Run.progress`) → `report.phases` when idle, so a finished build still shows what it did. Each row: status glyph (✓ done / `Spinner` running / · pending / ✗ failed, reusing the existing `Badge`/`Spinner` tones) + title + `kind` badge + file count. Height-capped like the file list (`max-h-24 overflow-auto`) so it never steals editor width. The report strip (L366-429) gains a `stop_reason` line in `text-warning` when `outcome !== 'complete'`.

12. **Frontend — step vocabulary (`frontend/src/features/build/useBuildActivity.ts:15`).** `BUILD_STEPS` is a 3-element subset of a backend that emits six, so `survey` / `report` / `failed` light no dot and `STEP_LABELS[step]` is `undefined` (which collapses the label to the generic "Generating…"). Extend to `['instantiate_skeleton','survey','plan','phase','verify','repair','report']` with labels for each, keep `failed` in `STEP_LABELS` but out of `BUILD_STEPS` (terminal, not a dot), and pin the two lists in lockstep with a test. The hook also parses `build.phase` / `build.verify`, returning `phases`, `phaseIndex`, `phaseTotal`.

13. **Repository awareness without context bloat.** The agent must also maintain knowledge of the code files in the workspace using a compact, selective repository map rather than dumping all code files into the LLM context. It should know which files exist, what each file is for, and what has already been implemented so it can recognize existing functionality and avoid creating unnecessary new files that may already exist and only need modification.

## Out of scope for this phase

- The verification pipeline, the classifier and the repair wiring — `phase-55`.
- The placeholder check — `phase-55`, task 12.4.
- Test authoring or a test gate in Build — the Test stage's job (`phase-27`/`phase-28`).
- Parallel phase execution. Phases run sequentially; `depends_on` exists to order them and to reject cycles, not to schedule concurrency.
- Letting the user edit the phase plan before implementation starts. The plan is visible in `phase-plan/` and re-plannable via `refine`, but there is no approval gate — that would break the "one press" directive.

## Acceptance criteria

- One Build press produces `phase-plan/plan.json` + `phase-plan/README.md` in the workspace, visible in the IDE tree as a sibling of `frontend/` and `backend/`, and a versioned `build_plan` artifact.
- The plan is implemented phase by phase: one model loop per phase, one typecheck gate per phase, one commit per phase (`git log` in the sandbox shows `build(<kind>): <title>` entries).
- Each phase's prompt contains that phase's slice and a short "story so far" — **not** the accumulated transcript of previous phases.
- A build interrupted mid-way and re-pressed resumes at the first phase whose status is not `done`, and does not re-run completed phases.
- With no requirements and no design, the build still runs on `fallback_plan` (D12) and reaches the same completion gate.
- Every loop is bounded: per-phase turns, per-phase fix attempts, integration iterations, and the wall-clock deadline.
- The Build panel shows the phase rail live, survives a reload mid-build (via `Run.progress`), and renders a **pre-rework** report exactly as it does today.
- `ruff` / `black` / `mypy` clean; `pytest` and `pnpm test` green.

## Tests to write

**Control plane (`pytest`)**

- `tests/agents/test_build_plan_parsing.py` *(new)* — clean JSON, fenced JSON, prose-wrapped JSON; garbage → `fallback_plan`; `> build_max_phases` truncated; duplicate ids and `depends_on` cycles rejected; topological order enforced.
- `tests/agents/test_codegen_phased.py` *(new)* — extend `codegen_fakes.build_transport` to N phase loops: one `run_tool_loop` and one `git_commit` per phase, `build.phase` events in order, `Run.progress.phase_index/phase_total/phase_id` updated after each, `phase-plan/` rewritten each time.
- `tests/agents/test_codegen_resume.py` *(new)* — a report with phases `[done, failed, pending]` resumes at index 1 and does not re-run phase 0.
- `tests/agents/test_build_plan_workspace.py` *(new)* — `build_plan_in_workspace=False` writes no workspace files but still persists the artifact; the folder name follows `build_plan_dir`.
- `tests/orchestrator/test_artifact_kind_lookup.py` *(extend from phase-55)* — with a `build_plan` written **after** a `build_report`, `_previous_report` still finds the report.
- **Re-run / adjust:** `tests/orchestrator/stages/test_build_handler.py` (the `_report()` helper gains the new fields), `tests/orchestrator/test_build_durability.py`, `tests/config/test_registry_complete.py`.

**Frontend (Vitest + Testing Library)**

- `frontend/src/features/build/api.test.ts` *(new)* — `parseBuildReport` on a pre-rework report defaults every array and yields `phases: []`; on `null`, `''`, `'{'`, `'[]'`, `'{"files_changed":null}'`, `'{"notes":[1,2]}'` it never throws and never yields a non-array.
- `BuildPanel.test.tsx` *(extend)* — an old report renders (the `.length` guard); a report with `phases` renders `build-phase-list` with correct per-row statuses; with no phases the list is absent; `stop_reason` shows when `outcome !== 'complete'`.
- `useBuildActivity.test.tsx` *(extend)* — a `build.phase` event updates the rail and index/total; a table test asserting **every** id in `BUILD_STEPS` has a non-empty `STEP_LABELS` entry — the direct regression guard for the 3-vs-7 mismatch.

## Manual verification steps

```bash
cd backend && uv run pytest -q
cd ../frontend && pnpm test -- --run && pnpm lint && pnpm typecheck
docker compose -f infra/docker-compose.yml up -d --no-deps proxy appdb
```

Then press **Build** on a fresh project and watch:

1. `phase-plan/` appears in the IDE tree beside `frontend/` and `backend/`; open `README.md` and watch the checklist fill in.
2. The Build panel shows the phase rail advancing; reload the page mid-build and confirm it reattaches to the correct phase.
3. `docker exec forgeflow-sb-<project-id> git -C /workspace log --oneline` shows one `build(<kind>): <title>` commit per phase.
4. The stage lands `complete` with no refine, and `http://<project-id>.preview.localhost` serves the generated product.
5. Open an older project's build report — it must render exactly as before, with no phase rail.

## Rollback / cleanup notes

- **Code** reverts cleanly. `RunProgress` gains three defaulted fields; reverting leaves them as unread keys on existing Run documents — harmless, no migration either way.
- **`build_plan` artifacts persist** after a revert. They are versioned records and nothing else reads them, but they remain in the artifact list and continue to share `build_report`'s version counter. Removing them requires deleting by `meta.kind`; not necessary.
- **`phase-plan/` folders persist inside existing sandbox volumes.** They are inert (a dotless folder Vite and Express never serve), removable with `sandbox-reap-all` — which destroys workspace code, so prefer leaving them.
- Frontend reverts cleanly; the defensive `parseBuildReport` is worth keeping even if the rest is reverted.

## Design notes

- **Why phases beat one loop.** The three wins are context size, failure localisation, and resumability — and they compound: a smaller context makes each phase more likely to typecheck first time, and a per-phase gate means the repair loop is handed a diff of one phase rather than of the whole app. This is the same "never feed the whole repo when a diff suffices" discipline as §9, applied to generation instead of repair.
- **Why the plan is written to the workspace *and* the artifact store.** The artifact is the durable, versioned record the locked decisions require (§7). The workspace copy is what the user asked for and what the agent re-reads cheaply between phases instead of carrying the plan in every prompt. They are written from one object, so they cannot disagree.
- **Ordering rule over dependency solving.** `data → backend → frontend → wiring` is a heuristic that makes phases independently typecheckable in the common case (a frontend page importing a not-yet-written API client will not compile). `depends_on` is validated for cycles and used to order, not to schedule — parallelism would reintroduce exactly the cross-phase breakage the gate exists to catch.
- **The report is authoritative for resume, not the filesystem.** A phase that wrote three of five files leaves those three on disk; treating file presence as "done" would skip the remaining two forever. The recorded status is the only signal that distinguishes "wrote some files" from "finished".
- **Failed phases are carried, not fatal.** Aborting on the first failed phase would waste every phase after it, and some failures genuinely need the whole app in view to fix (a frontend page against a backend route written two phases later). Carrying them into integration repair is strictly more likely to converge.
- **Known cost shape.** N phases × (implement + up to 2 fixes) is several times today's spend, bounded by `build_max_phases`, `build_phase_max_tool_turns`, `build_phase_max_fix_attempts` and `build_max_wall_clock_s`. Planning stays on Haiku. `Run.cost` per phase is the D13 eval unit for judging whether phasing pays for itself against one-shot.
