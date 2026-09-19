"""Codegen agent (phase-23) — generate a working full-stack app onto the prebuilt skeleton.

The heart of the Build stage (D4). It is deliberately **skeleton-first**: before the model runs a
single token of implementation, the agent deterministically instantiates the fixed-stack skeleton
(phase-22) and commits it. The LLM then writes *only feature code* onto that scaffold — the core
token-saving lever (the frontend is never regenerated).

Flow (§ plan, phase-23 tasks):

    0. instantiate skeleton (deterministic copy + "skeleton" commit)  — recorded on the Run trace
    1. plan          (Haiku)  — read design + requirements, tolerate missing/stale
    2. implement     (Sonnet) — write BE/FE feature code onto the skeleton, streaming fs.write
    3. verify boot   (Sonnet) — install deps, start preview, health-check; fix trivial boot errors
    4. build report  — structured {boot_status, files_changed, features_built, follow_ups, …}

Steps 2–3 happen inside one bounded tool-use loop (`AnthropicClient.run_tool_loop`), so budget caps
and cost accounting apply throughout. Context is minimal by design (design/requirements summaries +
skeleton conventions), never the whole repo — mirroring the repair loop's discipline.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, cast

from beanie import PydanticObjectId

from app.agents.anthropic_client import AnthropicClient
from app.agents.build_phases import (
    BuildPlanAborted,
    BuildPlanner,
    PhaseOutcome,
    PhaseRunner,
    story_so_far,
)
from app.agents.build_plan import PHASE_DONE, PHASE_PENDING, BuildPlan, incremental_plan
from app.agents.build_scope import BuildScope, ScopeClassifier
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.agents.tools.registry import ToolDispatch, ToolRegistry
from app.agents.tools.skeleton import skeleton_relpaths
from app.core.config import get_config
from app.core.errors import NotFoundError, UserError
from app.db.models import Project, RequirementSpec, Run
from app.db.models.common import utcnow
from app.db.models.enums import ArtifactType, Stage, StageStatus
from app.db.models.requirement import Feature
from app.db.repos import StageStateRepo
from app.design.service import DesignService
from app.orchestrator.artifacts import ArtifactService
from app.realtime.hub import emit
from app.realtime.schemas import EventType

logger = logging.getLogger(__name__)

BOOT_HEALTHY = "healthy"
BOOT_UNHEALTHY = "unhealthy"
BOOT_DEGRADED = "degraded"
BOOT_NOT_STARTED = "not_started"

_PREVIEW_RUNNING = "running"
_PREVIEW_FAILED = "failed"

# Sentinel files that mean "the skeleton is already here" (→ build incrementally, don't rescaffold).
_INSTANTIATED_MARKERS = ("pnpm-workspace.yaml", "package.json")

BUILD_REPORT_KIND = "build_report"

# Where generated *feature* code lives. The inventory is scoped to these so the model sees its own
# prior work, not the 100+ skeleton/config files it must never touch.
_FEATURE_ROOTS = ("backend/src", "frontend/src")

# Never walk into these — they are enormous and irrelevant to what the agent has authored.
_INVENTORY_SKIP = {"node_modules", ".git", "dist", "build", ".cache", "coverage", ".turbo"}

# Skeleton files present from instantiation; listing them as "existing feature code" would invite
# the model to treat scaffolding as its own output (the phase-54 bug). The set is *derived* from the
# template directory (`skeleton_relpaths`) rather than hand-maintained, so it can never again drift
# behind the skeleton as files are added. This small literal is only a defensive floor for an exotic
# environment where the template dir is unreadable.
_SKELETON_FALLBACK = frozenset(
    {
        "backend/src/app.ts",
        "backend/src/index.ts",
        "backend/src/config.ts",
        "backend/src/db.ts",
        "frontend/src/main.tsx",
        "frontend/src/routes.tsx",
        "frontend/src/index.css",
    }
)

# Tools withheld from the MODEL's implement loop. `instantiate_skeleton` re-copies the skeleton and
# silently overwrites app.ts/routes.tsx — wiping the routes the build just registered. It is safe
# only as the deterministic step-0 action the agent runs itself (see `run`), not as a model choice.
_MODEL_EXCLUDED_TOOLS = frozenset({"instantiate_skeleton"})


def _skeleton_paths() -> frozenset[str]:
    """Every path the skeleton ships, so `_existing_code` never reports scaffold as feature code.

    Derived from the template dir; falls back to a small floor literal only if that dir is
    unreadable — a skeleton file is then still never mistaken for feature code.
    """
    try:
        return skeleton_relpaths()
    except UserError:  # skeleton dir missing/unreadable in an exotic env
        return _SKELETON_FALLBACK


_INVENTORY_MAX_FILES = 120  # a cap so a large workspace cannot blow the prompt budget

#: Human labels for the coarse build phases, mirrored onto the Run so a reattaching client can say
#: what the build is doing without replaying events.
STEP_LABELS: dict[str, str] = {
    "instantiate_skeleton": "Scaffolding the skeleton",
    "survey": "Surveying existing code",
    "plan": "Planning the build",
    "implement": "Writing feature code",
    "phase": "Implementing a phase",  # phase-56 — the per-phase implement step
    "report": "Finishing up",
    "failed": "Build failed",
}


@dataclass
class _PhaseLoopResult:
    """What the phase loop achieved, and why it stopped early if it did."""

    phases: list[PhaseOutcome]
    stop_reason: str | None
    summary: str
    note: str = ""

    @property
    def complete(self) -> bool:
        return all(p.status == PHASE_DONE for p in self.phases)

    @property
    def follow_up(self) -> str:
        if self.stop_reason == "environment":
            return (
                f"The build stopped because {self.note} — this is a sandbox problem, not a code "
                "problem. Fix it and run Build again."
            )
        return (
            "The build did not finish — it ran past its wall-clock budget. Run Build again to "
            "continue from the first unfinished phase."
        )


#: Human labels for the tool calls the build makes, surfaced as `progress` events. The long,
#: silent-looking waits are install/preview/tests — naming them is the whole point.
TOOL_STEP_LABELS: dict[str, str] = {
    "instantiate_skeleton": "Scaffolding the skeleton",
    "read_file": "Reading existing code",
    "list_dir": "Inspecting the workspace",
    "write_file": "Writing feature code",
    "install_deps": "Installing dependencies",
    "run_command": "Running a command",
    "run_tests": "Running tests",
    "start_preview": "Starting the preview servers",
    "restart_preview": "Restarting the preview servers",
    "git_commit": "Committing the workspace",
}


@dataclass
class BuildReport:
    """The structured outcome of a build (persisted as a ``code_change`` artifact)."""

    boot_status: str
    files_changed: list[str]
    features_built: list[str]
    follow_ups: list[str]
    notes: list[str]
    commit: str | None
    tests_passed: bool | None
    summary: str
    plan: str
    # phase-56 additions — ALL defaulted, so a report artifact written before phasing still parses
    # through `_previous_report` (and renders unchanged in the Build panel).
    phases: list[PhaseOutcome] = field(default_factory=list)
    verification: dict[str, Any] | None = None
    outcome: str = ""
    stop_reason: str | None = None
    # phase-60 additions — defaulted, so reports written before scope routing still parse.
    scope: str = ""
    scope_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "boot_status": self.boot_status,
            "files_changed": self.files_changed,
            "features_built": self.features_built,
            "follow_ups": self.follow_ups,
            "notes": self.notes,
            "commit": self.commit,
            "tests_passed": self.tests_passed,
            "summary": self.summary,
            "plan": self.plan,
            "phases": [p.to_dict() for p in self.phases],
            "verification": self.verification,
            "outcome": self.outcome,
            "stop_reason": self.stop_reason,
            "scope": self.scope,
            "scope_reason": self.scope_reason,
        }

    @property
    def phases_complete(self) -> bool:
        """Every planned phase reached ``done``. Vacuously true for an unphased report."""
        return all(p.status == PHASE_DONE for p in self.phases)


@dataclass
class _BuildTracker:
    """Observes tool results during the loop to assemble the build report from real signals."""

    files_written: set[str] = field(default_factory=set)
    last_commit: str | None = None
    fe_status: str | None = None
    be_status: str | None = None
    tests_passed: bool | None = None
    preview_started: bool = False

    def observe(self, name: str, args: dict[str, Any], result_json: str) -> None:
        try:
            result = json.loads(result_json)
        except json.JSONDecodeError:  # a tool that didn't return JSON — nothing to track
            return
        if not result.get("ok"):
            return
        if name == "write_file":
            path = args.get("path")
            if isinstance(path, str):
                self.files_written.add(path)
        elif name in ("start_preview", "restart_preview"):
            self.preview_started = True
            self.fe_status = _as_str(result.get("fe_status"))
            self.be_status = _as_str(result.get("be_status"))
        elif name == "run_tests":
            self.tests_passed = bool(result.get("passed"))
        elif name == "git_commit":
            sha = result.get("sha")
            if isinstance(sha, str):
                self.last_commit = sha

    def boot_status(self) -> str:
        if not self.preview_started:
            return BOOT_NOT_STARTED
        if self.fe_status == _PREVIEW_RUNNING and self.be_status == _PREVIEW_RUNNING:
            return BOOT_HEALTHY
        if _PREVIEW_FAILED in (self.fe_status, self.be_status):
            return BOOT_UNHEALTHY
        return BOOT_DEGRADED


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) else (str(value) if value is not None else None)


class CodegenAgent:
    """Drives the skeleton-first build loop and produces a :class:`BuildReport`."""

    def __init__(
        self,
        client: AnthropicClient | None = None,
        registry: ToolRegistry | None = None,
        artifacts: ArtifactService | None = None,
    ) -> None:
        self._client = client or AnthropicClient()
        self._registry = registry or default_registry()
        self._artifacts = artifacts or ArtifactService()
        self._designs = DesignService(self._artifacts)
        self._stages = StageStateRepo()
        # phase-56: the build is planned into phases and implemented one at a time.
        self._planner = BuildPlanner(self._client, self._artifacts)
        self._phases = PhaseRunner(self._client)
        # phase-60: decides whether this invocation needs planning at all.
        self._scope = ScopeClassifier(self._client)

    async def _quiesce_preview(self, project: Project, channel: str) -> None:
        """Stop a running preview so codegen writes cannot trigger a watcher restart storm."""
        try:
            from app.sandbox.preview import get_preview_service

            await get_preview_service().stop(project)
        except Exception:  # never let this block a build
            logger.debug("could not quiesce the preview before build", exc_info=True)

    async def run(
        self,
        project: Project,
        run: Run,
        *,
        ctx: ToolContext | None = None,
        channel: str | None = None,
        instruction: str | None = None,
        fresh: bool = False,
        forced_scope: BuildScope | None = None,
    ) -> BuildReport:
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            raise UserError("Project is not persisted")
        channel = channel or str(project_id)
        ctx = ctx or ToolContext.build(project, run, channel=channel)

        tracker = _BuildTracker()
        dispatch = self._make_dispatch(ctx, tracker, channel, run)
        notes: list[str] = []

        # Quiesce any preview before writing a single file (phase-59). A build rewrites dozens of
        # files; a dev server left watching restarts on each one, and those restarts race each other
        # for the same port -- the EADDRINUSE storm reported against a refine. Verification starts a
        # fresh preview at the end, which is the only preview a build should ever have running.
        await self._quiesce_preview(project, channel)

        stages = {s.stage: s.status for s in await self._stages.list_for_project(project_id)}

        # Step 0 — skeleton-first (deterministic; recorded on the trace as the first tool action).
        await self._emit(channel, "instantiate_skeleton", run)
        if await self._is_instantiated(ctx):
            notes.append(
                "Workspace already instantiated — building incrementally, not rescaffolding."
            )
        else:
            await dispatch("instantiate_skeleton", {})
            await dispatch("git_commit", {"message": "skeleton"})

        # What a previous (possibly failed) build already wrote. Withheld on an explicit
        # regenerate-everything request, which is the whole point of that escape hatch.
        await self._emit(channel, "survey", run)
        existing, existing_paths = (None, []) if fresh else await self._existing_code(ctx)
        if fresh:
            notes.append(
                "Full regeneration requested — rewrite the feature code rather than extending it."
            )
        elif existing_paths:
            notes.append(
                f"{len(existing_paths)} feature file(s) already exist — extend them, "
                "do not restart."
            )
            previous = await self._previous_report(project_id)
            if previous is not None:
                notes.append(
                    f"Previous build: boot={previous.boot_status}"
                    + (
                        f", features={', '.join(previous.features_built)}"
                        if previous.features_built
                        else ""
                    )
                )
                notes += [f"Outstanding follow-up: {f}" for f in previous.follow_ups]

        # Gather minimal upstream context (tolerate missing / stale — D12).
        design, design_note = await self._design_context(project_id, stages)
        requirements, req_note, features = await self._requirements_context(project_id, stages)
        notes += [n for n in (design_note, req_note) if n]
        # A change request (phase-24 refine) leads the context so the model prioritizes it.
        if instruction:
            notes.insert(0, f"Change request: {instruction}")

        # Step 1 — decide how much build this actually needs (phase-60), then plan only if it does.
        # A one-line refine used to trigger a full re-plan plus phase-by-phase execution; scope is a
        # property of the request, so a cheap classify call routes it.
        await self._emit(channel, "plan", run)
        decision = await self._scope.decide(
            project_id=project_id,
            run=run,
            channel=channel,
            instruction=instruction,
            has_build=bool(existing_paths),
            fresh=fresh,
            existing_files=len(existing_paths),
            forced=forced_scope,
        )
        # Deliberately NOT appended to `notes`: those record exceptions worth a user's attention
        # (missing upstream artifacts, a resume point, unfinished phases), and every build has a
        # scope. It is persisted as structured fields instead, which the Build panel renders.

        if decision.is_small:
            # No planner call, no `phase-plan/` rewrite: the change becomes one synthetic phase and
            # reuses the phase runner's bounded loop, typecheck gate, fix sub-loop and commit. The
            # saving is in PLANNING, never in proof — phase-55 verification runs unchanged below.
            assert instruction is not None  # a small scope implies a change request
            build_plan = incremental_plan(instruction)
        else:
            build_plan = await self._planner.plan(
                project,
                run,
                ctx=ctx,
                channel=channel,
                design=design,
                requirements=requirements,
                features=features,
                existing=existing,
                instruction=instruction,
            )
        plan = build_plan.to_prose()  # BuildReport.plan keeps its existing string shape

        # Step 2 — implement phase by phase (Sonnet, one *small* bounded loop each). Resume skips
        # every phase the last report already recorded as `done`.
        #
        # A small change never resumes: its synthetic phase id is stable, so a second refine would
        # see the first one's `change` phase recorded as done and skip the new request entirely.
        if decision.is_small:
            resume_from, carried = 0, cast(list[PhaseOutcome], [])
        else:
            resume_from, carried = await self._resume_point(project_id, build_plan, fresh=fresh)
        if resume_from:
            notes.append(
                f"Resuming at phase {resume_from + 1} of {len(build_plan.phases)} — "
                f"{resume_from} phase(s) already done."
            )
        try:
            phased = await self._run_phases(
                project,
                run,
                ctx=ctx,
                channel=channel,
                dispatch=dispatch,
                plan=build_plan,
                design=design,
                requirements=requirements,
                existing=existing,
                notes=notes,
                resume_from=resume_from,
                carried=carried,
                write_plan_files=not decision.is_small,
            )
        except Exception as exc:
            # Persist what the failed attempt DID achieve. Without this the files it wrote are
            # invisible in the UI and the next build has no record to resume from.
            await self._emit(channel, "failed", run)
            partial = BuildReport(
                boot_status=tracker.boot_status(),
                files_changed=sorted(tracker.files_written),
                features_built=features,
                follow_ups=[
                    "The build did not finish — run it again to resume from the files already "
                    "written.",
                    *self._follow_ups(tracker),
                ],
                notes=[*notes, f"Build failed: {exc}"],
                commit=tracker.last_commit,
                tests_passed=tracker.tests_passed,
                summary=f"Build failed before completing: {exc}",
                plan=plan,
                phases=list(carried),
                outcome="failed",
                stop_reason="failed",
                scope=str(decision.scope),
                scope_reason=decision.reason,
            )
            await self._persist_report(project_id, partial, failed=True)
            await emit(
                channel,
                EventType.build_report,
                {"stage": "build", **partial.to_dict()},
                stage=Stage.build,
            )
            raise

        # Step 3 — build report.
        await self._emit(channel, "report", run)
        # A phased build can stop short: the wall-clock deadline expires, or the sandbox breaks
        # mid-plan. That is an *unfinished* build, not a failed one: say so, keep the files, and
        # point at the resume path — the next run continues at the first phase that is not `done`.
        follow_ups = self._follow_ups(tracker)
        summary = phased.summary.strip()
        if phased.stop_reason is not None:
            follow_ups.insert(0, phased.follow_up)
            notes = [*notes, f"Build unfinished: {phased.note}."]
            summary = summary or f"Build unfinished — {phased.note}."
        elif not phased.complete:
            unfinished = [p for p in phased.phases if p.status != PHASE_DONE]
            detail = "; ".join(f"{p.title} ({p.note or p.status})" for p in unfinished)
            follow_ups.insert(
                0,
                f"The build did not finish — {len(unfinished)} phase(s) did not complete: "
                f"{detail}. Run Build again to continue from the first unfinished phase.",
            )
            notes = [*notes, f"Build unfinished: {detail}."]
        report = BuildReport(
            boot_status=tracker.boot_status(),
            files_changed=sorted(tracker.files_written),
            features_built=features,
            follow_ups=follow_ups,
            notes=notes,
            commit=tracker.last_commit,
            tests_passed=tracker.tests_passed,
            summary=summary,
            plan=plan,
            phases=phased.phases,
            outcome=("complete" if phased.complete and phased.stop_reason is None else "partial"),
            stop_reason=phased.stop_reason,
            scope=str(decision.scope),
            scope_reason=decision.reason,
        )
        await self._persist_report(project_id, report)
        await emit(
            channel,
            EventType.build_report,
            {"stage": "build", **report.to_dict()},
            stage=Stage.build,
        )
        return report

    # -- phases (phase-56) ---------------------------------------------------------------

    async def _run_phases(
        self,
        project: Project,
        run: Run,
        *,
        ctx: ToolContext,
        channel: str,
        dispatch: ToolDispatch,
        plan: BuildPlan,
        design: str | None,
        requirements: str | None,
        existing: str | None,
        notes: list[str],
        resume_from: int,
        carried: list[PhaseOutcome],
        write_plan_files: bool = True,
    ) -> _PhaseLoopResult:
        """Implement the plan one phase at a time, rewriting ``phase-plan/`` as it goes.

        A failed phase is **carried, not fatal** — aborting on the first one would waste every
        phase after it, and some failures genuinely need the whole app in view to fix (a frontend
        page against a backend route written two phases later). They are recorded and handed to
        phase-55's integration repair. The two things that *do* stop the build are the wall-clock
        deadline and an `environment` verdict, neither of which more phases can improve.
        """
        deadline = time.monotonic() + float(get_config().get("build_max_wall_clock_s"))
        # `carried` is the accumulator, not a seed to copy: if this raises part-way, the caller's
        # partial report must still record the phases that DID finish, or the next build re-runs
        # them from scratch.
        outcomes: list[PhaseOutcome] = carried
        total = len(plan.phases)
        tools = self._model_tools()
        stop_reason: str | None = None
        note = ""

        for index, phase in enumerate(plan.phases[resume_from:], start=resume_from + 1):
            if time.monotonic() > deadline:
                stop_reason = "wall_clock"
                note = "it ran past the build's wall-clock budget"
                break
            files_before = set(run.progress.files)
            if outcomes:
                # Phase N must see what phases 1…N-1 wrote (phase-64). The inventory used to be
                # taken once, before the plan, so every later phase was told "first build on a
                # bare skeleton" while the models it needed were already on disk. Paths + sizes
                # only — the cost posture is unchanged.
                existing, _ = await self._existing_code(ctx)
            try:
                outcome = await self._phases.run(
                    project,
                    run,
                    phase,
                    ctx=ctx,
                    channel=channel,
                    dispatch=dispatch,
                    tools=tools,
                    index=index,
                    total=total,
                    project_name=project.name,
                    design=design,
                    requirements=requirements,
                    existing=existing,
                    story_so_far=story_so_far(outcomes),
                    notes=notes if index == resume_from + 1 else None,
                    files_before=files_before,
                )
            except BuildPlanAborted as abort:
                # The sandbox is broken, not the code — every further phase is wasted spend.
                stop_reason = "environment"
                note = abort.reason
                notes.append(f"Build stopped — {abort.reason}. {abort.hint}".strip())
                break
            outcomes.append(outcome)
            # Rewrite the workspace checklist after EVERY phase, so it visibly fills in. A small
            # change has no plan to show, and must not clobber the one a real build left behind.
            if write_plan_files:
                await self._planner.write_workspace(
                    project, ctx, plan, {o.id: o.status for o in outcomes}
                )

        remaining = [p for p in plan.phases if p.id not in {o.id for o in outcomes}]
        for phase in remaining:  # never silently drop a phase the build did not reach
            outcomes.append(
                PhaseOutcome(
                    id=phase.id,
                    title=phase.title,
                    kind=phase.kind,
                    status=PHASE_PENDING,
                    note="not reached",
                )
            )
        if remaining and write_plan_files:
            await self._planner.write_workspace(
                project, ctx, plan, {o.id: o.status for o in outcomes}
            )

        done = [o.title for o in outcomes if o.status == PHASE_DONE]
        # The last phase's own closing line is the truest summary of the build; the phase tally is
        # the fallback for a build that never got a model to say anything useful.
        summary = next(
            (o.summary for o in reversed(outcomes) if o.summary),
            (
                f"Built {len(done)} of {len(plan.phases)} planned phase(s): " + ", ".join(done)
                if done
                else "No phase completed."
            ),
        )
        return _PhaseLoopResult(
            phases=outcomes, stop_reason=stop_reason, summary=summary, note=note
        )

    async def _resume_point(
        self, project_id: PydanticObjectId, plan: BuildPlan, *, fresh: bool
    ) -> tuple[int, list[PhaseOutcome]]:
        """Where to restart: the first phase the last report did **not** record as ``done``.

        The **report is authoritative**; workspace file presence is corroborating evidence only. A
        phase that wrote three of five files leaves those three on disk, and treating that as
        "done" would skip the remaining two forever.
        """
        if fresh:
            return 0, []
        previous = await self._previous_report(project_id)
        if previous is None or not previous.phases:
            return 0, []
        by_id = {p.id: p for p in previous.phases}
        carried: list[PhaseOutcome] = []
        for index, phase in enumerate(plan.phases):
            recorded = by_id.get(phase.id)
            if recorded is None or recorded.status != PHASE_DONE:
                return index, carried
            carried.append(recorded)
        return len(plan.phases), carried

    async def finalize_report(
        self,
        project_id: PydanticObjectId,
        report: BuildReport,
        verification: dict[str, Any],
        *,
        stop_reason: str | None,
    ) -> BuildReport:
        """Fold the verification outcome into the report and persist it as a new version.

        Codegen persists its report *before* verification runs (so a build that dies during verify
        still leaves a record of what it wrote). This adds the verdict afterwards as a second
        version rather than overwriting the first — artifacts are versioned, never mutated (§7),
        and the panel reads the newest one.
        """
        report.verification = verification
        # A phase-level stop (wall_clock / environment) survives; verification's reason refines a
        # build that got all the way through its phases.
        report.stop_reason = report.stop_reason or stop_reason
        report.outcome = "complete" if report.stop_reason is None else "partial"
        await self._persist_report(project_id, report)
        return report

    # -- context -------------------------------------------------------------------------

    async def _design_context(
        self, project_id: PydanticObjectId, stages: dict[Stage, StageStatus]
    ) -> tuple[str | None, str | None]:
        latest = await self._designs.latest(project_id)
        if latest is None:
            return None, "No design artifact — building the UI from scratch."
        try:
            payload = await self._designs.load_payload(latest)
        except UserError:
            return None, "Design artifact had no payload — building the UI from scratch."
        budget = int(get_config().get("codegen_context_char_budget"))
        summary = (
            f"HTML:\n{_truncate(payload.html, budget)}\n\nCSS:\n{_truncate(payload.css, budget)}"
        )
        note = None
        if stages.get(Stage.design) is StageStatus.stale:
            note = (
                f"Design (v{latest.version}) is stale from an upstream change — using it anyway; "
                "re-run the design stage to refresh."
            )
        return summary, note

    async def _requirements_context(
        self, project_id: PydanticObjectId, stages: dict[Stage, StageStatus]
    ) -> tuple[str | None, str | None, list[str]]:
        spec = (
            await RequirementSpec.find(RequirementSpec.project_id == project_id)
            .sort("-version")
            .first_or_none()
        )
        if spec is None or not spec.features:
            return None, "No structured requirements — inferring a minimal feature set.", []
        features = [f.name for f in spec.features]
        note = None
        if stages.get(Stage.requirements) is StageStatus.stale:
            note = (
                f"Requirements (v{spec.version}) are stale from an upstream change — using them "
                "anyway; re-run the requirements stage to refresh."
            )
        return _format_features(spec.features), note, features

    # -- helpers -------------------------------------------------------------------------

    def _model_tools(self) -> list[dict[str, Any]]:
        """The tool schemas handed to the implement loop: the full registry minus the tools that
        are safe deterministically but destructive as a model choice (see `_MODEL_EXCLUDED_TOOLS`).

        `self._registry` stays the *full* registry, so the agent's own step-0 dispatch of
        `instantiate_skeleton` still resolves.
        """
        return self._registry.anthropic_tools(exclude=set(_MODEL_EXCLUDED_TOOLS))

    def _make_dispatch(
        self, ctx: ToolContext, tracker: _BuildTracker, channel: str, run: Run
    ) -> ToolDispatch:
        async def dispatch(name: str, raw_args: dict[str, Any]) -> str:
            label = TOOL_STEP_LABELS.get(name, name)
            target = _tool_target(name, raw_args)
            # Announce the tool BEFORE it runs. install_deps/start_preview/run_tests each block for
            # minutes; unannounced they read as a hang, which is exactly what users reported.
            await emit(
                channel,
                EventType.progress,
                {
                    "stage": "build",
                    "step": "implement",
                    "tool": name,
                    "label": label,
                    "target": target,
                },
                stage=Stage.build,
            )
            run.progress.label = label
            run.progress.target = target
            run.progress.updated_at = utcnow()
            await run.save()

            result = await self._registry.dispatch(ctx, name, raw_args)
            tracker.observe(name, raw_args, result)

            # Record written files durably as they land, so a reload can rebuild the list without
            # depending on the (bounded) realtime replay ring.
            written = sorted(tracker.files_written)
            if written != run.progress.files:
                run.progress.files = written
                run.progress.updated_at = utcnow()
                await run.save()
            return result

        return dispatch

    async def _is_instantiated(self, ctx: ToolContext) -> bool:
        for marker in _INSTANTIATED_MARKERS:
            try:
                await ctx.workspace.read(ctx.project, marker)
                return True
            except NotFoundError:
                continue
        return False

    async def _existing_code(self, ctx: ToolContext) -> tuple[str | None, list[str]]:
        """An inventory of feature code already on disk: ``(prompt block, paths)``.

        This is what makes a rebuild incremental. A build that died half-way still leaves its files
        in the workspace; without telling the model they exist it starts over and rewrites them all.
        Paths + sizes only — the model pulls contents with ``read_file`` when it actually needs them
        (§7 cost discipline: never feed the whole repo when a listing suffices).
        """
        skeleton = _skeleton_paths()  # derived once; every file the skeleton itself ships
        found: list[tuple[str, int]] = []
        for root in _FEATURE_ROOTS:
            try:
                nodes = await ctx.workspace.tree(ctx.project, root, depth=None)
            except (NotFoundError, UserError):
                continue  # that half of the stack has not been created yet
            for node in nodes:
                path = getattr(node, "path", "") or ""
                if getattr(node, "type", "") == "dir" or not path:
                    continue
                parts = path.split("/")
                if any(part in _INVENTORY_SKIP for part in parts):
                    continue
                if path in skeleton:
                    continue
                found.append((path, int(getattr(node, "size", 0) or 0)))

        if not found:
            return None, []

        found.sort(key=lambda item: item[0])
        paths = [path for path, _ in found]
        shown = found[:_INVENTORY_MAX_FILES]
        lines = [f"  {path} ({_human_size(size)})" for path, size in shown]
        if len(found) > len(shown):
            lines.append(f"  …and {len(found) - len(shown)} more")
        return "\n".join(lines), paths

    async def _previous_report(self, project_id: PydanticObjectId) -> BuildReport | None:
        """The most recent build report, so a retry knows what the last attempt achieved.

        Filters by ``kind`` rather than taking the newest ``(build, code_change)`` artifact, so a
        phase-56 ``build_plan`` written under the same pair cannot hide the last report (phase-55).
        """
        latest = await self._artifacts.get_latest_of_kind(
            project_id, Stage.build, ArtifactType.code_change, BUILD_REPORT_KIND
        )
        if latest is None:
            return None
        try:
            text = await self._artifacts.get_content(latest)
            data = json.loads(text) if text else None
        except (UserError, NotFoundError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        return BuildReport(
            boot_status=str(data.get("boot_status", BOOT_NOT_STARTED)),
            files_changed=[str(f) for f in data.get("files_changed", [])],
            features_built=[str(f) for f in data.get("features_built", [])],
            follow_ups=[str(f) for f in data.get("follow_ups", [])],
            notes=[str(n) for n in data.get("notes", [])],
            commit=data.get("commit"),
            tests_passed=data.get("tests_passed"),
            summary=str(data.get("summary", "")),
            plan=str(data.get("plan", "")),
            # phase-56 fields; absent on every report written before phasing, hence the defaults.
            phases=[
                PhaseOutcome.from_dict(p) for p in data.get("phases", []) if isinstance(p, dict)
            ],
            verification=(
                data.get("verification") if isinstance(data.get("verification"), dict) else None
            ),
            outcome=str(data.get("outcome", "")),
            stop_reason=(
                str(data["stop_reason"]) if isinstance(data.get("stop_reason"), str) else None
            ),
        )

    def _follow_ups(self, tracker: _BuildTracker) -> list[str]:
        out: list[str] = []
        status = tracker.boot_status()
        if status == BOOT_NOT_STARTED:
            out.append("Preview was not started — verify the app boots before deploying.")
        elif status != BOOT_HEALTHY:
            out.append(
                f"Preview is not healthy (frontend={tracker.fe_status}, "
                f"backend={tracker.be_status}) — check the logs and run the Test & repair stage."
            )
        if tracker.tests_passed is False:
            out.append("Tests did not pass — run the Test & repair stage.")
        return out

    async def _persist_report(
        self, project_id: PydanticObjectId, report: BuildReport, *, failed: bool = False
    ) -> None:
        await self._artifacts.create_version(
            project_id,
            Stage.build,
            ArtifactType.code_change,
            text=json.dumps(report.to_dict(), indent=2),
            meta={
                "kind": BUILD_REPORT_KIND,
                "boot_status": report.boot_status,
                "features_built": report.features_built,
                "files_changed": report.files_changed,
                "follow_ups": report.follow_ups,
                "tests_passed": report.tests_passed,
                "commit": report.commit,
                "failed": failed,
                # phase-56: badge the report so the UI can render a summary without fetching it.
                "phase_count": len(report.phases),
                "outcome": report.outcome,
                "stop_reason": report.stop_reason,
            },
        )

    async def _emit(self, channel: str, step: str, run: Run | None = None) -> None:
        label = STEP_LABELS.get(step, step)
        await emit(
            channel,
            EventType.progress,
            {"stage": "build", "step": step, "label": label},
            stage=Stage.build,
        )
        # Mirror the phase onto the Run so a reattaching client knows where the build is.
        if run is not None:
            run.progress.step = step
            run.progress.label = label
            run.progress.target = ""
            run.progress.updated_at = utcnow()
            await run.save()


def _human_size(size: int) -> str:
    return f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"


def _tool_target(name: str, args: dict[str, Any]) -> str:
    """The most useful single detail about a tool call — the file, or the command being run."""
    if name in ("write_file", "read_file"):
        path = args.get("path")
        return path if isinstance(path, str) else ""
    if name == "list_dir":
        path = args.get("path")
        return path if isinstance(path, str) else ""
    if name == "run_command":
        cmd = args.get("cmd")
        return " ".join(str(c) for c in cmd[:4]) if isinstance(cmd, list) else ""
    if name == "run_tests":
        kind = args.get("kind")
        return str(kind) if kind else ""
    return ""


def _truncate(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    return text[:budget] + "\n…(truncated)…"


def _format_features(features: list[Feature]) -> str:
    blocks: list[str] = []
    for feature in features:
        parts = [f"### {feature.name}"]
        if feature.description:
            parts.append(feature.description)
        if feature.inputs:
            parts.append("Inputs: " + ", ".join(feature.inputs))
        if feature.expected_behaviors:
            parts.append("Behaviors:\n" + "\n".join(f"- {b}" for b in feature.expected_behaviors))
        if feature.acceptance_criteria:
            parts.append(
                "Acceptance:\n"
                + "\n".join(f"- {a.text} [{a.kind}]" for a in feature.acceptance_criteria)
            )
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)
