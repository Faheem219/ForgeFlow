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
from dataclasses import dataclass, field
from typing import Any

from beanie import PydanticObjectId

from app.agents.anthropic_client import STOP_MAX_TURNS, AnthropicClient
from app.agents.models import TaskKind
from app.agents.prompts import system_prompt
from app.agents.prompts.codegen import (
    CODEGEN_SYSTEM_EXTRA,
    PLAN_SYSTEM_EXTRA,
    implement_user_prompt,
    plan_user_prompt,
)
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.agents.tools.registry import ToolDispatch, ToolRegistry
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
# the model to treat scaffolding as its own output.
_SKELETON_FILES = {
    "backend/src/app.ts",
    "backend/src/index.ts",
    "backend/src/config.ts",
    "backend/src/db.ts",
    "frontend/src/main.tsx",
    "frontend/src/routes.tsx",
    "frontend/src/index.css",
}

_INVENTORY_MAX_FILES = 120  # a cap so a large workspace cannot blow the prompt budget

#: Human labels for the coarse build phases, mirrored onto the Run so a reattaching client can say
#: what the build is doing without replaying events.
STEP_LABELS: dict[str, str] = {
    "instantiate_skeleton": "Scaffolding the skeleton",
    "survey": "Surveying existing code",
    "plan": "Planning the build",
    "implement": "Writing feature code",
    "report": "Finishing up",
    "failed": "Build failed",
}

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
        }


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

    async def run(
        self,
        project: Project,
        run: Run,
        *,
        ctx: ToolContext | None = None,
        channel: str | None = None,
        instruction: str | None = None,
        fresh: bool = False,
    ) -> BuildReport:
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            raise UserError("Project is not persisted")
        channel = channel or str(project_id)
        ctx = ctx or ToolContext.build(project, run, channel=channel)

        tracker = _BuildTracker()
        dispatch = self._make_dispatch(ctx, tracker, channel, run)
        notes: list[str] = []

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

        # Step 1 — plan (Haiku).
        await self._emit(channel, "plan", run)
        plan = await self._plan(
            project_id, run, project.name, design, requirements, channel, instruction, existing
        )

        # Steps 2–3 — implement + verify boot (Sonnet, bounded tool-use loop).
        await self._emit(channel, "implement", run)
        try:
            impl = await self._client.run_tool_loop(
                task_kind=TaskKind.codegen,
                project_id=project_id,
                run=run,
                system=system_prompt(CODEGEN_SYSTEM_EXTRA),
                messages=[
                    {
                        "role": "user",
                        "content": implement_user_prompt(
                            project.name, plan, design, requirements, notes, existing
                        ),
                    }
                ],
                tools=self._registry.anthropic_tools(),
                tool_dispatch=dispatch,
                channel=channel,
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
            )
            await self._persist_report(project_id, partial, failed=True)
            await emit(
                channel, "build.report", {"stage": "build", **partial.to_dict()}, stage=Stage.build
            )
            raise

        # Step 4 — build report.
        await self._emit(channel, "report", run)
        # The loop can stop at its safety bound (or on a model that repeats itself) with real work
        # already committed. That is an *unfinished* build, not a failed one: say so, keep the
        # files, and point at the resume path — the next run extends them rather than restarting.
        follow_ups = self._follow_ups(tracker)
        summary = impl.text.strip()
        if not impl.finished:
            reason = (
                f"stopped after {impl.turns} tool turns (its safety bound)"
                if impl.stopped == STOP_MAX_TURNS
                else "stopped because it began repeating the same action"
            )
            follow_ups.insert(
                0, f"The build did not finish — it {reason}. Run Build again to continue from here."
            )
            notes = [*notes, f"Build unfinished: {reason}."]
            summary = summary or f"Build unfinished — it {reason}."
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
        )
        await self._persist_report(project_id, report)
        await emit(
            channel, "build.report", {"stage": "build", **report.to_dict()}, stage=Stage.build
        )
        return report

    # -- steps ---------------------------------------------------------------------------

    async def _plan(
        self,
        project_id: PydanticObjectId,
        run: Run,
        project_name: str,
        design: str | None,
        requirements: str | None,
        channel: str,
        instruction: str | None = None,
        existing: str | None = None,
    ) -> str:
        user = plan_user_prompt(project_name, design, requirements, existing)
        if instruction:
            user += f"\n\nThe user requested this specific change — plan for it: {instruction}"
        result = await self._client.run_tool_loop(
            task_kind=TaskKind.summarize,  # routes to MODEL_ROUTING (Haiku) — cheap planning
            project_id=project_id,
            run=run,
            system=system_prompt(PLAN_SYSTEM_EXTRA),
            messages=[{"role": "user", "content": user}],
            channel=channel,
        )
        return result.text.strip() or "(no explicit plan produced)"

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
                if path in _SKELETON_FILES:
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
        """The most recent build report, so a retry knows what the last attempt achieved."""
        latest = await self._artifacts.get_latest(project_id, Stage.build, ArtifactType.code_change)
        if latest is None or latest.meta.get("kind") != BUILD_REPORT_KIND:
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
