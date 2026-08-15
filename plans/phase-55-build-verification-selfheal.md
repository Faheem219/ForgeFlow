# Phase 55 — Build verification & bounded self-heal

## Title & ID

- **ID:** `phase-55`
- **Title:** Build verification & bounded self-heal
- **Epic:** Epic 13 — Build Reliability *(added per user directive)*
- **Milestone:** M2 *(hardens M2's stated outcome — a generated **running app**)*
- **Status:** ✅ done *(2026-08-15; shipped as two commits — plumbing then verification)*

## Goal

After codegen writes the app, verify it for real — install, typecheck, boot, placeholder check — and repair what is broken through the existing **bounded** repair loop, stopping immediately when the failure is the sandbox rather than the code. Build reports `complete` only when the app actually works.

## Context

The **user directive**: *"the Build phase itself should build the entire project and keep fixing the product until it actually works, instead of me having to repeatedly tell the agent what is broken… if the error comes in the BE or FE after completion, if the terminal gives any code-based error then it will be sent to [the agent]; if it is a sandbox env error then it will not be forwarded — it will stop."*

Today the acceptance bar is the last line of `implement_user_prompt`: *"Implement the remaining plan onto the skeleton now, then verify it boots."* Nothing typechecks, nothing re-runs, and `app/orchestrator/stages/build.py:79-82` marks the stage `complete` iff `boot_status == "healthy"`. Worse, `_BuildTracker.boot_status()` (`codegen.py:175-182`) conflates causes: `unhealthy` means "a dev server exited", whether from a code bug, the pids limit, or missing `node_modules`.

Meanwhile the project's **stated contribution** — the diff-aware, stall-detecting, escalating `RepairLoopController` (§9, D7) — is wired only into the Test stage. Build never calls it. This phase connects them.

Locked decisions honoured: **the loop stays bounded** (D7 / Golden Rule 5 — this phase reuses the existing controller rather than writing a second loop, and adds explicit caps plus a wall-clock ceiling); cost discipline holds (D4 — no new Sonnet surface beyond repair, and env failures spend **zero** iterations); artifacts stay versioned (§7); and no hard prerequisite is added (D12 — verification degrades gracefully when upstream artifacts are missing).

## Depends on

- **Phase IDs:** `phase-23` (codegen loop), `phase-24` (build stage handler), `phase-29` (repair context), `phase-30` (repair agent), `phase-31` (repair loop controller), `phase-15` (preview service), `phase-13` (command execution), `phase-51` (layered config).
- **External:** Docker/Colima for manual verification only. Every unit test runs without it — providers are mocked and `RepairAgent`'s collaborators are all injectable.

### Prerequisite check

- `phase-29`, `phase-30`, `phase-31` are ✅; `tests/orchestrator/stages/test_repair_loop.py` is green.
- `tests/config/test_registry_complete.py` is green **before** starting — it fails the build if any `Settings` field lacks a registry entry, and this phase adds twelve.
- `cd backend && uv run pytest -q` green.

## Detailed tasks

Ship as **two commits**: tasks 1–8 are plumbing with no behaviour change (every existing test stays green), tasks 9–12 are the user-visible verification.

### Commit 1 — plumbing

1. **`ArtifactService.get_latest_of_kind` (`app/orchestrator/artifacts.py`).** `codegen.py:512-513` calls `get_latest(project_id, Stage.build, ArtifactType.code_change)` and bails when `meta.kind != "build_report"`. `phase-56` adds a `build_plan` artifact under the *same* `(stage, type)` pair, at which point `get_latest` returns the plan, `_previous_report` returns `None`, and incremental resume silently dies. Add `get_latest_of_kind(project_id, stage, type, kind)` — reverse `list_versions`, first `meta.kind` match — and repoint `_previous_report` at it now, before the collision can exist.

2. **Per-call turn bound (`app/agents/anthropic_client.py:159`).** `run_tool_loop` reads `anthropic_max_tool_turns` (250) from config with no override, so `phase-56`'s N phase loops would give an effective ceiling of N×250. Add `max_turns: int | None = None`, defaulting to the config read.

3. **Preview log tail (`app/sandbox/preview.py:45-53, 165-188`).** `_pump` streams output to the hub and buffers **nothing**, so a crashed dev server leaves no evidence to diagnose or repair from. Add `log_tail: deque[str]` (maxlen `preview_log_tail_lines`) to `_Proc`, append in `_pump`, and expose `PreviewService.log_tail(project, process) -> str`.

4. **Null-`ExitCode` fix (`app/sandbox/runtime.py:439-441`).** `wait()` returns `int(exec_inspect(...).get("ExitCode") or 0)` — a container that died mid-exec reports **0 = success**. Return a new `EXIT_UNKNOWN = -1` when `ExitCode` is `None`. Every consumer improves: `app/sandbox/deps.py:103`, `app/agents/tools/definitions.py:143`, `app/testing/runner.py:194`, and the preview health probe at `preview.py:160` all currently read a dead container as a pass. **Behaviour change in a hot path — its own commit, its own test, a changelog note.**

5. **`SandboxManager.container_state` (`app/sandbox/manager.py`).** `async def container_state(self, project_id) -> str` returning `running | exited | missing` from `container.status`. An independent liveness signal, so a classifier keyed on exit codes can never be fooled by a stale one.

6. **`ensure_dependencies` returns its outcome (`app/sandbox/deps.py:73-112`).** It currently returns `bool` and swallows failures, which then resurface downstream as a *test* failure blamed on generated code. Return the `ExecOutcome` (or `None` when skipped). Callers: `preview.py:213` (ignores the return) and its tests.

7. **`changed_paths` (`app/sandbox/git.py`, `app/sandbox/workspace.py`).** `git diff --name-only <a> <b>`, mirroring the existing `diff` (`git.py:66-73`, `workspace.py:111-116`). Needed by the widening analyzer in task 10.

8. **`RepairLoopController` parameterization (`app/orchestrator/stages/repair.py`).** Three additive changes, all defaulted so the Test stage is byte-identical:

   ```python
   def __init__(self, ..., *, stage: Stage = Stage.test, owns_stage_status: bool = True) -> None
   async def run(self, ..., max_iterations: int | None = None) -> RepairLoopResult
   ```

   - L350 `set_status(..., Stage.test, ...)` → `if self._owns_stage_status: ... self._stage`. Build passes `owns_stage_status=False`, because the conductor owns build's status via `StageResult.next_status`.
   - L296 / L351 / L356 and `_persist` L397 → `self._stage`. Build repairs then persist under `(build, repair_attempt)` — a distinct audit trail, which is what we want.
   - `RESUME_ACTION` (L68-72) already points at build. No change.
   - While in the file: promote the hard-coded `_MAX_REGRESSIONS = 2` (L64) to config `repair_max_regressions`.

### Commit 2 — verification

9. **Failure classifier (`app/agents/build_errors.py`, new).** Modelled on `diagnose()` (`app/orchestrator/stages/validate.py:167-198`) and the `DeployErrorKind` idiom (`app/deploy/providers/errors.py:17-76`):

   ```python
   class BuildFailureKind(StrEnum):
       code = "code"; environment = "environment"; provider = "provider"; budget = "budget"

   @dataclass(frozen=True)
   class BuildDiagnosis:
       kind: BuildFailureKind; reason: str; signal: str; hint: str
       @property
       def repairable(self) -> bool: return self.kind is BuildFailureKind.code

   def classify_output(text, *, exit_code=None, timed_out=False, container_alive=None) -> BuildDiagnosis
   def classify_exception(exc) -> BuildDiagnosis   # ProviderError→provider, budget UserError→budget
   ```

   Signal corpus — **none of this exists in the codebase today** (verified across `backend/app`: no OOM handling, no pids-limit detection, no container-state guard, no egress classification):

   | Class | Signals |
   |---|---|
   | container dead | `container_alive is False`, `oci runtime exec failed`, `procready not received`, `container not running`, `no such container` |
   | OOM | exit `137` / `-9`, `javascript heap out of memory`, `oomkilled`, `signal sigkill` |
   | pids limit | `fork: resource temporarily unavailable`, `spawn enomem`, `cannot fork` |
   | disk | `enospc`, `no space left on device` |
   | egress / registry | `getaddrinfo enotfound registry`, `eai_again`, `err_pnpm_meta_fetch_fail` |
   | DB unreachable | `buffering timed out after` |
   | timeout | `timed_out` is True, exit `124` (`EXIT_TIMEOUT`) |
   | **default** | `code` — mirroring `validate.py:198`'s conservative final return |

   Matched case-insensitively against the combined stdout/stderr tail, most-specific first. Erring toward `code` is deliberate: misreading a real bug as an env problem skips a repair the loop could have made.

10. **Synthetic test results (`app/testing/synthetic.py`, new — pure, no I/O).** The repair loop's oracle is hard-typed to `TestRun`/`TestResult`, so build failures must be adapted. The idiom already exists at `app/testing/runner.py:198-212` (`_suite_failure`) — follow it exactly.

    ```python
    def typecheck_failures(output, *, package_dirs, max_results) -> list[TestResult]
    def boot_failure(process, log_tail, *, fallback_files) -> TestResult
    def command_failure(name, cmd, exit_code, output, *, timed_out, fallback_files) -> TestResult
    async def persist_synthetic_run(project_id, results, *, stdout) -> TestRun
    ```

    `typecheck_failures` is load-bearing. `tsc` emits `src/features/todos/todos.controller.ts(12,5): error TS2339: …`, and it runs with cwd `frontend`/`backend` — so the paths are **package-relative**. Re-root them via `package_dirs` to `frontend/src/…`, or every path handed to `RepairContextAnalyzer._add_files_within_budget` (`app/agents/repair_context.py:255`) raises `NotFoundError` and the loop targets nothing. Emit one `TestResult` per distinct file, grouping that file's errors into `failure.message` (first few) and `failure.stack` (all); cap at `build_typecheck_max_failures`. That is what gives `_rank_paths` (`repair_context.py:327-344`) real, frequency-rankable, **editable** source paths.

11. **Guarantee a non-empty editable set (`app/agents/build_context.py`, new).** *This is the failure mode that would silently break the whole phase.* `RepairAgent` derives editable files from `context.target_files` minus test files (`app/agents/repair.py:210`). A boot crash whose stack names only `node_modules` paths → `extract_referenced_files` strips them (`app/testing/parsers.py:50`) → `ranked == []` → `target_files == []` → `attempt=None` → `REASON_BLOCKED` → escalation **on iteration 1, having patched nothing**. Three layers:

    1. **Seed at the source.** Every synthetic result carries `fallback_files`: the files this build just wrote, filtered to the failing surface (`backend/src/**` for a BE boot failure, `frontend/src/**` for FE), newest first, capped at 8. From `_BuildTracker.files_written`. Diff-aware in spirit — "what changed since it worked" — and free.
    2. **A widening adapter.** `BuildRepairContextAnalyzer` wraps `RepairContextAnalyzer`; when the editable set comes back empty it widens from `fallback_paths`, then from `changed_paths(last_passing, HEAD)`. Its signature matches the `ContextAnalyzer` Protocol (`repair.py:176-179`) exactly, so it drops into `RepairLoopController(analyzer=…)`. Needs two one-line promotions in `repair_context.py` (`_add_files_within_budget` → `add_files`, `_finish` → `persist`), no behaviour change. The `repair_context_max_chars` cap still applies, so widening can never become "feed the whole repo".
    3. **Pre-check.** If the editable set is *still* empty, escalate directly with `stop_reason="no_repair_target"` and the log — one model-free analyzer call, zero model calls, and a far better message than `REASON_BLOCKED`.

    `criterion_id` needs no work: synthetic results carry `None`, and `_requirement_snippets` (`repair_context.py:226`) returns `[]` with no note. That already degrades cleanly.

12. **`BuildVerificationController` (`app/orchestrator/stages/build_verify.py`, new).** Every collaborator injected, mirroring `LiveValidationController` (`validate.py:215-249`). Each step emits `build.verify`:

    1. **install** — `ensure_dependencies(...)`, classified via task 9.
    2. **typecheck** — `build_typecheck_cmd`, timeout `build_typecheck_timeout_s`. Safe with Playwright specs present: `frontend/tsconfig.json` is `"include": ["src"]`, so `e2e/` is not typechecked.
    3. **preview** — `PreviewService.start` → `PreviewInfo`. `PreviewInfo.warning` (proxy missing, loopback DB — already detected at `preview.py:242-259`) is **environment by construction**: surfaced verbatim, never repaired.
    4. **placeholder check** — deterministic and model-free: grep the workspace for `Your app starts here` / `Learn Vite` / `ForgeFlow App`, and for an index route still bound to the stock `HomePage`. Classified `code` → repairable. **This is what turns `phase-54`'s prompt-only decision into a guaranteed outcome.**
    5. **repair** — for `code`-class failures only:

       ```python
       RepairLoopController(
           analyzer=BuildRepairContextAnalyzer(RepairContextAnalyzer(), fallback_paths=written_files),
           stage=Stage.build, owns_stage_status=False,
       ).run(project, synthetic_run, run=run, ctx=ctx, channel=channel,
             max_iterations=build_integration_max_iterations, cancel=cancel)
       ```

       After a `fixed` outcome, **re-run the failed step** to confirm — `RepairAgent` re-runs the *suite*, which for a synthetic run is not the same thing as re-typechecking.

    **Discipline:** classify **before** constructing the synthetic run. `environment` / `provider` / `budget` never reach the repair loop — zero iterations, zero tokens. That is the user's explicit requirement.

13. **Completion gate (`app/orchestrator/stages/build.py:79-82`).** Replace `complete iff boot_status == "healthy"` with

    ```
    complete ⟺ typecheck_ok ∧ no placeholder findings
             ∧ fe_status == "running" ∧ be_status == "running"
    ```

    Otherwise `awaiting_user` with a `stop_reason` from a fixed vocabulary, each mapping to one precise sentence in `_summarize` (L101-111): `typecheck_failed` · `placeholder_left` · `boot_unhealthy` · `repair_escalated` · `no_repair_target` · `environment` · `provider` · `budget` · `cancelled` · `wall_clock`. (`phase-56` adds `phases_incomplete`.)

    The handler runs codegen, then verification, then the gate — all inside the existing single detached `Run` (`build.py:66-77`), so the concurrent-build lock and `run.finished` semantics are untouched.

14. **Config (`app/core/config.py` + `app/core/config_registry.py` + `.env.example`).** `tests/config/test_registry_complete.py` fails the build if a `Settings` field has no registry entry — register all of these.

    | Key | Default | Purpose |
    |---|---|---|
    | `build_integration_max_iterations` | 3 | integration repair cap, separate from `repair_max_iterations` |
    | `build_max_wall_clock_s` | 3600 | overall deadline — the outermost bound |
    | `build_typecheck_cmd` | `"pnpm typecheck"` | gate command |
    | `build_typecheck_timeout_s` | 600 | gate wall clock |
    | `build_typecheck_max_failures` | 20 | cap on synthetic results per typecheck |
    | `preview_log_tail_lines` | 200 | boot-failure evidence buffer (task 3) |
    | `repair_max_regressions` | 2 | promotes `repair.py:64`'s hard-coded constant |

15. **Event types (`app/realtime/schemas.py:15-41`).** Add `build_verify = "build.verify"`, and promote today's ad-hoc `build.report` string (`codegen.py:316/351`) into the enum — §7 wants the event schema typed.

16. **Repository awareness without context bloat.** The agent must also maintain knowledge of the code files in the workspace using a compact, selective repository map rather than dumping all code files into the LLM context. It should know which files exist, what each file is for, and what has already been implemented so it can recognize existing functionality and avoid creating unnecessary new files that may already exist and only need modification.

## Out of scope for this phase

- Phasing the build (structured plan, `PhaseRunner`, resume, phase rail) — `phase-56`.
- The `phases_incomplete` gate condition — `phase-56` adds it to the vocabulary defined here.
- Test **authoring** or a test gate in Build — that stays the Test stage's job (`phase-27`/`phase-28`). Related: Playwright's `webServer` (`templates/app-skeleton/frontend/playwright.config.ts:21-28`) boots a *second* Vite inside a 256-pid container, so in-sandbox e2e during Build would manufacture the very env failure this phase classifies.
- A `PreviewStatus.unhealthy` member for hung-but-alive servers — touches the shared enum and the frontend; inferred from the health timeout here instead (see Design notes).
- Raising `anthropic_max_output_tokens` — flagged in Design notes, deliberately not bundled.

## Acceptance criteria

- A build whose generated code fails `pnpm typecheck` is repaired automatically and lands `complete`, with no user message in between.
- A build that fails because the sandbox is broken (container dead, OOM, pids exhausted, registry unreachable, app DB unreachable) lands `awaiting_user` with `stop_reason="environment"`, a message naming the actual cause and its fix, and **zero repair iterations spent** — assertable as `ScriptedAgent.iterations == []`.
- A boot failure whose stack names only `node_modules` paths still produces a non-empty editable set and a real repair attempt — it does not escalate on iteration 1.
- `complete` is impossible while typecheck is red, a placeholder survives, or either dev server is not `running`.
- Every loop is bounded: iteration cap, stall threshold, regression guard, and a wall-clock ceiling. No unbounded path exists (D7 / Golden Rule 5).
- `RepairLoopController` constructed with no keyword arguments behaves exactly as it does today — the Test stage is unaffected.
- `DockerExecHandle.wait()` returns `EXIT_UNKNOWN` (not 0) when `ExitCode` is `None`.
- `ruff` / `black` / `mypy` clean; `pytest` green including the pre-existing repair and sandbox suites.

## Tests to write

**Control plane (`pytest`)** — no Docker, no model; build on the existing `repair_loop_fakes.py`, `codegen_fakes.py`, `tests/sandbox/fakes.py`.

- `tests/agents/test_build_errors_classifier.py` *(new)* — table-driven over the whole corpus. Critically **`exit_code=0, container_alive=False` → `environment`** (the null-`ExitCode` trap) and a `TS2339` line → `code`.
- `tests/testing/test_synthetic_results.py` *(new)* — `tsc` output → one `TestResult` per file with paths **re-rooted** to `frontend/src/…`; capped at `build_typecheck_max_failures`; a boot log naming `/workspace/backend/src/…` populates `files_referenced`; an all-`node_modules` log yields empty.
- `tests/agents/test_build_repair_context.py` *(new)* — the widening analyzer fills an empty target set from `fallback_paths`, then from `changed_paths`; never exceeds `repair_context_max_chars`; with nothing to widen from, returns empty so the pre-check fires.
- `tests/orchestrator/stages/test_build_verify_selfheal.py` *(new)* — typecheck red → repair → re-typecheck green → `verified`; a stalled loop → `escalated` with `stop_reason="repair_escalated"`.
- `tests/orchestrator/stages/test_build_env_short_circuits.py` *(new)* — **`ScriptedAgent.iterations == []`** on ENOSPC / pids-limit / OOM / dead-container, with `stop_reason="environment"`. The direct test of the user's requirement.
- `tests/orchestrator/stages/test_build_completion_gate.py` *(new)* — truth table: all conditions ⇒ `complete`; each single missing condition ⇒ `awaiting_user` with the right `stop_reason`.
- `tests/orchestrator/stages/test_repair_loop_stage_param.py` *(new)* — `stage=Stage.build, owns_stage_status=False` never touches `Stage.test`, emits `stage=build`, persists under `(build, repair_attempt)`; **default construction is unchanged** (the parameterization regression guard).
- `tests/sandbox/test_exec_dead_container.py` *(new)* — `wait()` with `ExitCode: None` → `EXIT_UNKNOWN`.
- `tests/orchestrator/test_artifact_kind_lookup.py` *(new)* — `get_latest_of_kind` returns the newest `build_report` even when another kind was written after it.
- `tests/sandbox/test_preview_log_tail.py` *(new)* — `log_tail` is bounded by `preview_log_tail_lines` and returns the most recent lines.
- **Re-run / adjust:** `tests/sandbox/test_deps_bootstrap.py` (return type), `tests/orchestrator/stages/test_build_handler.py`, `tests/orchestrator/test_build_durability.py`, `tests/config/test_registry_complete.py`.

**Frontend** — none. The `stop_reason` surface lands with `phase-56`'s panel work; until then it is carried in the assistant message.

## Manual verification steps

```bash
cd backend && uv run pytest -q
uv run ruff check . && uv run black --check . && uv run mypy .
docker compose -f infra/docker-compose.yml up -d --no-deps proxy appdb
```

**Happy path** — press Build on a fresh project. Expect the Build conversation to show install → typecheck → preview → (repair, if needed) → `complete`, with no user intervention.

**Environment short-circuit** — `docker stop forgeflow-appdb`, then press Build. Expect it to stop with the DB-unreachable message, and `GET /projects/<id>/runs` to show **no** `repair:loop` run. Restart with `docker start forgeflow-appdb`.

**Code repair** — hand-edit a generated file in the IDE to introduce a type error, save, press **Rebuild**. Expect a `repair.iteration` event, then `complete`.

## Rollback / cleanup notes

Mostly code-only and reversible by revert. Three things to know:

- **The `wait()` fix is a behaviour change**, not a bug-compatible one: reverting restores the old "dead container reads as success" behaviour that several call sites silently depend on. Revert it deliberately, not incidentally.
- **New config keys** are additive with defaults; removing them is safe, but drop the `config_registry.py` entries in the same revert or `test_registry_complete.py` will fail in the opposite direction.
- **`repair_attempt` artifacts under `(build, …)`** persist after a revert. Harmless — they are versioned records, and nothing reads them by stage — but they will remain visible in the artifact list.

No migrations, no volumes, no containers to reap.

## Design notes

- **Reuse the loop, do not write a second one.** D7 and Golden Rule 5 describe *the* repair loop, singular; a second bounded loop with its own stall and regression logic would duplicate the project's stated contribution and drift from it. Three defaulted keyword arguments make the existing controller serve both stages, and `RESUME_ACTION` already pointed at build.
- **Classify before adapting.** Putting the classifier *before* the synthetic `TestRun` is what makes "sandbox errors are never forwarded" structurally true rather than a policy the loop is trusted to follow. An `environment` verdict cannot reach the agent because the input it would need is never built.
- **Erring toward `code`.** `validate.diagnose` set this precedent and the reasoning transfers: a real bug misread as an env problem skips a repair that would have worked, which is worse than one wasted iteration on a misread env failure.
- **The known preview gap.** A hung-but-alive dev server reports `starting` forever (`preview.py:190-195`) — there is no "unhealthy but running" state, and `_wait_healthy` (L300-308) returns on deadline without recording why. Treating post-timeout `starting` as unhealthy, with the log tail as evidence, is an inference rather than an observation. The clean fix is a `PreviewStatus.unhealthy` member, which touches the shared enum and the frontend — deferred rather than smuggled in here.
- **Flagged, not bundled: `anthropic_max_output_tokens` 4096 → 8192.** A large page write must fit in a single turn; a truncated write yields broken syntax that then burns repair iterations fixing our own truncation. The registry already permits up to 200 000 (`config_registry.py:225`). Left as an operator decision because it changes cost on every call, not just on build.
- **Cost posture.** The added Sonnet surface is bounded by `build_integration_max_iterations` (3) and only ever runs on `code`-class failures. `enforce_budget` already runs before and after every turn, so the existing caps bind unchanged; `Run.cost` on the `repair:loop` run is the D13 eval unit for measuring whether self-heal pays for itself.

## Implementation status (as-built, 2026-08-15)

Shipped as the planned two commits: **plumbing** (`ef1578e`, phase-55 1/2) then **verification** (`3660229`, phase-55 2/2).

**Done — every task 1–16 landed:**

- **Commit 1 — plumbing (tasks 1–8, 14, 15), no behaviour change.**
  1. `ArtifactService.get_latest_of_kind` added; `codegen._previous_report` repointed at it (filters by `meta.kind`, so a phase-56 `build_plan` can't hide the last report).
  2. `run_tool_loop` gained an optional per-call `max_turns` (defaults to the config read).
  3. `PreviewService.log_tail` + a bounded per-process `deque` filled in `_pump` (`preview_log_tail_lines`).
  4. `DockerExecHandle.wait()` returns `EXIT_UNKNOWN` (−1), never 0, when `ExitCode` is null — the hot-path behaviour change, its own test.
  5. `SandboxManager.container_state` → `running | exited | missing`.
  6. `ensure_dependencies` now returns its `ExecOutcome | None` (was `bool`); `test_deps_bootstrap` adjusted.
  7. `changed_paths` added to `WorkspaceGit` + `WorkspaceService` (`git diff --name-only`).
  8. `RepairLoopController` parameterized: `stage` / `owns_stage_status` (ctor) + per-run `max_iterations`, all defaulted so the Test stage is byte-identical; `_MAX_REGRESSIONS` promoted to config `repair_max_regressions`.
  14. Seven config keys registered (`config.py` + `config_registry.py` + `.env.example`), covered by `test_registry_complete`.
  15. `EventType.build_report` (promoted from the ad-hoc string) + `EventType.build_verify`.
- **Commit 2 — verification (tasks 9–13, 16).**
  9. `app/agents/build_errors.py` — `BuildFailureKind` + `classify_output` / `classify_exception` over the full corpus (container-dead / OOM / pids / disk / registry / DB / timeout), erring toward `code`; the `exit_code=0, container_alive=False → environment` trap is tested.
  10. `app/testing/synthetic.py` — `typecheck_failures` (paths re-rooted package-relative → `frontend/src/…`, grouped per file, capped), `boot_failure`, `command_failure`, `persist_synthetic_run`.
  11. `app/agents/build_context.py` — `BuildRepairContextAnalyzer` widens an empty editable set from `fallback_paths` then `changed_paths`, inside the `repair_context_max_chars` cap; `_add_files_within_budget`/`_finish` promoted to public `add_files`/`persist`.
  12. `app/orchestrator/stages/build_verify.py` — `BuildVerificationController` (install → typecheck → boot → placeholder → repair), all collaborators injected; classify-before-adapt so env/provider/budget spend **zero** iterations; a model-free pre-check escalates `no_repair_target`; loop bounded by `build_integration_max_iterations` + a wall-clock ceiling.
  13. `build.py` — codegen → verify → gate inside one `Run`/`ToolContext`; `complete ⟺ typecheck_ok ∧ no placeholder ∧ both dev servers running`, with the fixed `stop_reason` vocabulary in `_summarize`.
  16. **Repository awareness:** satisfied by reusing phase-54's compact `_existing_code` inventory + repository-map prompt framing (paths + sizes, contents read on demand) — no new subsystem, which would overlap phase-56 and bloat context.

**Verification performed:** all new/adjusted unit suites pass against a live Mongo (classifier corpus, synthetic re-rooting, widening analyzer + budget, completion-gate truth table, env short-circuit `iterations == []`, self-heal red→green and stall→escalate, stage-param regression guard, dead-container `wait()`, artifact-kind lookup, preview log tail); `ruff` + `black` clean; `mypy` clean on all changed modules. Pre-existing, environmental reds are unrelated to this phase: the Docker/Colima-dependent suites and `test_repair_audit_trail::test_every_iteration_is_recorded_as_an_attempt`, all red on the pre-phase tree too.

*Re-verified 2026-08-15 while starting phase-56, on a macOS box with no Docker/Colima:* the full suite is **1313 passed, 7 failed**, and every one of the 7 is pre-existing — confirmed by re-running them on the clean tree at `f479679`. They are `test_repair_fixes_seeded_bug` (2), `test_repair_minimality` (2–3, order-dependent), `test_repair_regression_report` (1), `test_repair_audit_trail` (1) and `test_sandbox_isolation` (1); each fails inside the docker SDK (`FileNotFoundError` on the Docker socket), so the earlier note's "the Docker-dependent sandbox suites" is broader than it read — it includes these repair suites, which drive a **real** sandbox.

**Remaining / follow-ups (not blockers):**

- **Manual, docker-backed verification** (the "Manual verification steps" above) has **not** been run here — no Docker/Colima on the dev box. Run it before relying on the happy-path / env-short-circuit / code-repair demos.
- **Cancellation threading.** `BuildVerificationController` honours a `cancel` event and a `build_max_wall_clock_s` deadline, but `BuildStageHandler` does not yet construct/pass a `cancel` event — the wall-clock bound still applies; wiring an explicit cancel is a small follow-up.
- **Deferred by design (unchanged from the plan):** the `stop_reason` **frontend panel** (lands with phase-56), a `PreviewStatus.unhealthy` member for hung-but-alive servers, and the `anthropic_max_output_tokens` 4096→8192 bump (operator decision).
