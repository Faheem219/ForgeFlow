"""Build verification & bounded self-heal (phase-55 task 12).

After codegen writes the app this proves it actually works — **install → typecheck → boot →
placeholder check** — and self-heals what is broken through the *existing* bounded repair loop
(phase-31), reused via its Build parameters rather than a second loop (D7 / Golden Rule 5).

The discipline the user asked for is structural, not a policy the loop is trusted to follow: every
step's failure is **classified before** any synthetic ``TestRun`` is built (:mod:`app.agents.
build_errors`). An ``environment`` / ``provider`` / ``budget`` verdict returns immediately with a
``stop_reason`` and an actionable hint — **zero** repair iterations, **zero** model tokens. Only a
``code`` verdict is adapted into synthetic results and forwarded to the loop.

Every collaborator is injected (mirroring :class:`~app.orchestrator.stages.validate.
LiveValidationController`), so the whole flow is exercised without Docker, a model, or a provider.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from beanie import PydanticObjectId

from app.agents.build_context import BuildRepairContextAnalyzer, _editable
from app.agents.build_errors import BuildDiagnosis, BuildFailureKind, classify_output
from app.agents.repair_context import RepairContextAnalyzer
from app.agents.tools.context import ToolContext
from app.core.config import get_config
from app.db.models import Project, Run
from app.db.models.enums import Stage
from app.orchestrator.stages.repair import RepairLoopController
from app.realtime.hub import emit
from app.realtime.schemas import EventType
from app.sandbox.schemas import PreviewProcess
from app.testing.models import Failure, TestResult, TestStatus
from app.testing.synthetic import boot_failure, persist_synthetic_run, typecheck_failures

# Stop-reason vocabulary — each maps to one precise sentence in build.py's _summarize (task 13).
# phase-56 adds `phases_incomplete`.
STOP_TYPECHECK = "typecheck_failed"
STOP_PLACEHOLDER = "placeholder_left"
STOP_BOOT = "boot_unhealthy"
STOP_REPAIR = "repair_escalated"
STOP_NO_TARGET = "no_repair_target"
STOP_ENV = "environment"
STOP_PROVIDER = "provider"
STOP_BUDGET = "budget"
STOP_CANCELLED = "cancelled"
STOP_WALL_CLOCK = "wall_clock"

_KIND_STOP = {
    BuildFailureKind.environment: STOP_ENV,
    BuildFailureKind.provider: STOP_PROVIDER,
    BuildFailureKind.budget: STOP_BUDGET,
}

_PREVIEW_RUNNING = "running"
FRAMEWORK_PLACEHOLDER = "placeholder"

# The demo strings that must not survive into a built app (phase-54), and where they live.
_PLACEHOLDER_STRINGS = ("Your app starts here", "Learn Vite", "ForgeFlow App")
_PLACEHOLDER_FILES = (
    "frontend/src/pages/HomePage.tsx",
    "frontend/src/components/Layout.tsx",
    "frontend/index.html",
)
_ROUTES_FILE = "frontend/src/routes.tsx"


@dataclass
class StepProbe:
    """The observable result of running one build command."""

    output: str = ""
    exit_code: int | None = 0
    timed_out: bool = False


@dataclass
class BootProbe:
    """The observable result of starting the preview servers."""

    fe_status: str
    be_status: str
    warning: str | None = None
    fe_log: str = ""
    be_log: str = ""


@dataclass
class BuildVerifyReport:
    ok: bool
    stop_reason: str | None = None
    message: str = ""
    hint: str = ""
    typecheck_ok: bool = False
    placeholder_ok: bool = False
    fe_status: str = "unknown"
    be_status: str = "unknown"
    repaired: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stop_reason": self.stop_reason,
            "message": self.message,
            "hint": self.hint,
            "typecheck_ok": self.typecheck_ok,
            "placeholder_ok": self.placeholder_ok,
            "fe_status": self.fe_status,
            "be_status": self.be_status,
            "repaired": self.repaired,
            "notes": self.notes,
        }


# Injected step seams (defaults run the real commands; tests script them).
InstallStep = Callable[[Project, ToolContext], Awaitable[StepProbe | None]]
TypecheckStep = Callable[[Project, ToolContext], Awaitable[StepProbe]]
BootStep = Callable[[Project, ToolContext], Awaitable[BootProbe]]
PlaceholderStep = Callable[[Project, ToolContext], Awaitable[list[str]]]
ContainerState = Callable[[str], Awaitable[str]]
AnalyzerFactory = Callable[[list[str]], Any]  # (written_files) -> ContextAnalyzer
RepairFactory = Callable[[Any], RepairLoopController]  # (analyzer) -> loop


class BuildVerificationController:
    def __init__(
        self,
        *,
        install: InstallStep | None = None,
        typecheck: TypecheckStep | None = None,
        boot: BootStep | None = None,
        placeholder: PlaceholderStep | None = None,
        container_state: ContainerState | None = None,
        analyzer_factory: AnalyzerFactory | None = None,
        repair_factory: RepairFactory | None = None,
    ) -> None:
        self._install = install or _default_install
        self._typecheck = typecheck or _default_typecheck
        self._boot = boot or _default_boot
        self._placeholder = placeholder or _default_placeholder
        self._container_state = container_state or _default_container_state
        self._analyzer_factory = analyzer_factory or _default_analyzer_factory
        self._repair_factory = repair_factory or _default_repair_factory

    async def run(
        self,
        project: Project,
        run: Run,
        *,
        ctx: ToolContext,
        channel: str | None = None,
        written_files: list[str] | None = None,
        cancel: Any = None,
    ) -> BuildVerifyReport:
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            return BuildVerifyReport(
                ok=False, stop_reason=STOP_ENV, message="Project not persisted"
            )
        channel = channel or str(project_id)
        written = written_files or []
        started = time.monotonic()
        deadline = float(get_config().get("build_max_wall_clock_s"))

        def _cancelled() -> bool:
            return cancel is not None and cancel.is_set()

        def _over_budget() -> bool:
            return time.monotonic() - started > deadline

        synthetic: list[TestResult] = []
        outputs: list[str] = []

        # 1) install ------------------------------------------------------------------------
        await self._step(channel, "install")
        install = await self._install(project, ctx)
        if install is not None and install.exit_code != 0:
            diagnosis = await self._classify(
                project_id, install.output, install.exit_code, install.timed_out
            )
            if not diagnosis.repairable:
                return await self._short_circuit(channel, diagnosis)
            outputs.append(install.output)
            # A code-class install failure is unusual; a failed install still can't be repaired by
            # patching source, so treat it as environment for safety rather than looping on it.
            return await self._short_circuit(
                channel,
                BuildDiagnosis(
                    BuildFailureKind.environment,
                    "the dependency install failed",
                    f"exit {install.exit_code}",
                    "The dependency install failed. Check the registry/network and rebuild.",
                ),
            )

        if _cancelled():
            return _stop(STOP_CANCELLED, "The build was cancelled.")
        if _over_budget():
            return _stop(STOP_WALL_CLOCK, "The build exceeded its wall-clock budget.")

        # 2) typecheck ----------------------------------------------------------------------
        await self._step(channel, "typecheck")
        tc = await self._typecheck(project, ctx)
        typecheck_ok = tc.exit_code == 0
        if not typecheck_ok:
            diagnosis = await self._classify(project_id, tc.output, tc.exit_code, tc.timed_out)
            if not diagnosis.repairable:
                return await self._short_circuit(channel, diagnosis)
            tc_results = typecheck_failures(
                tc.output, max_results=int(get_config().get("build_typecheck_max_failures"))
            )
            synthetic += tc_results
            outputs.append(tc.output)

        # 3) preview ------------------------------------------------------------------------
        await self._step(channel, "preview")
        boot = await self._boot(project, ctx)
        fe_ok = boot.fe_status == _PREVIEW_RUNNING
        be_ok = boot.be_status == _PREVIEW_RUNNING
        if not (fe_ok and be_ok):
            # A PreviewInfo warning (proxy missing, loopback DB) is environment by construction —
            # surfaced verbatim, never repaired.
            if boot.warning:
                return await self._short_circuit(
                    channel,
                    BuildDiagnosis(
                        BuildFailureKind.environment,
                        "the preview environment is wrong",
                        "preview warning",
                        boot.warning,
                    ),
                )
            log = f"{boot.fe_log}\n{boot.be_log}"
            diagnosis = await self._classify(project_id, log, None, False)
            if not diagnosis.repairable:
                return await self._short_circuit(channel, diagnosis)
            if not be_ok:
                synthetic.append(
                    boot_failure(
                        PreviewProcess.backend,
                        boot.be_log,
                        fallback_files=_surface(written, "backend/src"),
                    )
                )
            if not fe_ok:
                synthetic.append(
                    boot_failure(
                        PreviewProcess.frontend,
                        boot.fe_log,
                        fallback_files=_surface(written, "frontend/src"),
                    )
                )
            outputs.append(log)

        # 4) placeholder check (deterministic, model-free) ----------------------------------
        await self._step(channel, "placeholder")
        findings = await self._placeholder(project, ctx)
        placeholder_ok = not findings
        if findings:
            synthetic.append(_placeholder_result(findings))

        # 5) repair (code-class failures only) ----------------------------------------------
        repaired = False
        if synthetic:
            if _cancelled():
                return _stop(STOP_CANCELLED, "The build was cancelled before repair.")
            outcome = await self._repair(project, run, ctx, channel, synthetic, outputs, written)
            if outcome.stop_reason is not None:
                return outcome
            repaired = True
            # Re-run only the steps that had failed — the RepairAgent re-runs the *suite*, which for
            # a synthetic run is not the same thing as re-typechecking / re-booting.
            typecheck_ok, placeholder_ok, fe_ok, be_ok = await self._reverify(
                project, ctx, channel, typecheck_ok, placeholder_ok, fe_ok, be_ok
            )

        # gate ------------------------------------------------------------------------------
        stop = _gate_stop_reason(typecheck_ok, placeholder_ok, fe_ok, be_ok)
        report = BuildVerifyReport(
            ok=stop is None,
            stop_reason=stop,
            message=(
                "The build is verified and working."
                if stop is None
                else _GATE_MESSAGE.get(stop, "The build did not pass verification.")
            ),
            typecheck_ok=typecheck_ok,
            placeholder_ok=placeholder_ok,
            fe_status=boot.fe_status,
            be_status=boot.be_status,
            repaired=repaired,
        )
        await self._emit_report(channel, report)
        return report

    # -- repair -------------------------------------------------------------------------

    async def _repair(
        self,
        project: Project,
        run: Run,
        ctx: ToolContext,
        channel: str,
        synthetic: list[TestResult],
        outputs: list[str],
        written: list[str],
    ) -> BuildVerifyReport:
        """Run the bounded loop on the code-class failures. Returns a stop report, or ok=True."""
        assert project.id is not None
        analyzer = self._analyzer_factory(list(written))
        synthetic_run = await persist_synthetic_run(
            project.id, synthetic, stdout="\n".join(outputs)
        )

        # Pre-check: one model-free analyzer call. An empty editable set escalates directly, with a
        # far better message than the loop's generic REASON_BLOCKED — and zero model calls.
        precheck = await analyzer.analyze(project, synthetic_run, persist=False)
        if not _editable(precheck):
            return _stop(
                STOP_NO_TARGET,
                "The build failed but nothing editable could be identified to repair — the failure "
                "points outside the generated feature code. Review the logs and guide the build.",
            )

        loop = self._repair_factory(analyzer)
        result = await loop.run(
            project,
            synthetic_run,
            run=run,
            ctx=ctx,
            channel=channel,
            max_iterations=int(get_config().get("build_integration_max_iterations")),
            cancel=None,
        )
        if not result.fixed:
            summary = ""
            if result.escalation is not None:
                summary = result.escalation.summary
            return _stop(
                STOP_REPAIR,
                "The build failures could not be repaired automatically — the bounded loop "
                f"escalated. {summary}".strip(),
            )
        return BuildVerifyReport(ok=True)  # sentinel: repair fixed it; the caller re-verifies

    async def _reverify(
        self,
        project: Project,
        ctx: ToolContext,
        channel: str,
        typecheck_ok: bool,
        placeholder_ok: bool,
        fe_ok: bool,
        be_ok: bool,
    ) -> tuple[bool, bool, bool, bool]:
        await self._step(channel, "reverify")
        if not typecheck_ok:
            typecheck_ok = (await self._typecheck(project, ctx)).exit_code == 0
        if not placeholder_ok:
            placeholder_ok = not await self._placeholder(project, ctx)
        if not (fe_ok and be_ok):
            boot = await self._boot(project, ctx)
            fe_ok = boot.fe_status == _PREVIEW_RUNNING
            be_ok = boot.be_status == _PREVIEW_RUNNING
        return typecheck_ok, placeholder_ok, fe_ok, be_ok

    # -- classification + events --------------------------------------------------------

    async def _classify(
        self, project_id: PydanticObjectId, output: str, exit_code: int | None, timed_out: bool
    ) -> BuildDiagnosis:
        # An independent liveness signal (task 5) — an exit code can be stale, the state cannot.
        alive = await self._container_state(str(project_id)) == "running"
        return classify_output(
            output, exit_code=exit_code, timed_out=timed_out, container_alive=alive
        )

    async def _short_circuit(self, channel: str, diagnosis: BuildDiagnosis) -> BuildVerifyReport:
        """An env/provider/budget verdict: stop here, forward nothing to the repair loop."""
        report = BuildVerifyReport(
            ok=False,
            stop_reason=_KIND_STOP[diagnosis.kind],
            message=f"The build stopped — {diagnosis.reason}. This is not a code problem.",
            hint=diagnosis.hint,
        )
        await self._emit_report(channel, report)
        return report

    async def _step(self, channel: str, step: str) -> None:
        await emit(
            channel, EventType.build_verify, {"stage": "build", "step": step}, stage=Stage.build
        )

    async def _emit_report(self, channel: str, report: BuildVerifyReport) -> None:
        await emit(
            channel,
            EventType.build_verify,
            {"stage": "build", "step": "done", **report.to_dict()},
            stage=Stage.build,
        )


# --------------------------------------------------------------------- pure helpers


def _stop(reason: str, message: str, hint: str = "") -> BuildVerifyReport:
    return BuildVerifyReport(ok=False, stop_reason=reason, message=message, hint=hint)


_GATE_MESSAGE = {
    STOP_TYPECHECK: "The generated code still does not typecheck.",
    STOP_PLACEHOLDER: "The skeleton demo placeholder still appears in the built app.",
    STOP_BOOT: "The app did not boot cleanly — a dev server is not running.",
}


def _gate_stop_reason(
    typecheck_ok: bool, placeholder_ok: bool, fe_ok: bool, be_ok: bool
) -> str | None:
    """`complete` ⟺ typecheck_ok ∧ no placeholder ∧ both servers running (task 13)."""
    if not typecheck_ok:
        return STOP_TYPECHECK
    if not placeholder_ok:
        return STOP_PLACEHOLDER
    if not (fe_ok and be_ok):
        return STOP_BOOT
    return None


def _surface(written: list[str], root: str) -> list[str]:
    """The build-authored files under a package root, newest-first, capped — the repair seed."""
    return [p for p in reversed(written) if p.startswith(root)][:8]


def _placeholder_result(findings: list[str]) -> TestResult:
    """One failing result whose editable targets are the placeholder files themselves."""
    return TestResult(
        name="placeholder check",
        status=TestStatus.failed,
        framework=FRAMEWORK_PLACEHOLDER,
        file=findings[0],
        failure=Failure(
            message="the skeleton demo placeholder still appears: " + ", ".join(findings),
            assertion=None,
            stack=None,
            files_referenced=list(findings),
        ),
    )


# --------------------------------------------------------------------- default steps (real)


async def _read_output(outcome: Any) -> str:
    from app.db.blobs import get_blob_store

    ref = getattr(outcome, "output_ref", None)
    if not ref:
        return ""
    try:
        data = await get_blob_store().get(ref)
    except Exception:
        return ""
    return data.decode("utf-8", errors="replace")


async def _default_install(project: Project, ctx: ToolContext) -> StepProbe | None:
    from app.sandbox.deps import ensure_dependencies
    from app.sandbox.runtime import DockerRuntime
    from app.sandbox.workspace import active_runtime_provider

    runtime = await active_runtime_provider()(project)
    if not isinstance(runtime, DockerRuntime):
        return None
    outcome = await ensure_dependencies(project, runtime)
    if outcome is None:
        return None
    return StepProbe(
        output=await _read_output(outcome), exit_code=outcome.exit_code, timed_out=outcome.timed_out
    )


async def _default_typecheck(project: Project, ctx: ToolContext) -> StepProbe:
    cmd = shlex.split(str(get_config().get("build_typecheck_cmd")))
    outcome = await ctx.exec_service.run(
        project, cmd, timeout=float(get_config().get("build_typecheck_timeout_s"))
    )
    return StepProbe(
        output=await _read_output(outcome), exit_code=outcome.exit_code, timed_out=outcome.timed_out
    )


async def _default_boot(project: Project, ctx: ToolContext) -> BootProbe:
    info = await ctx.preview.start(project)
    tail = getattr(ctx.preview, "log_tail", None)
    fe_log = str(tail(project, PreviewProcess.frontend)) if callable(tail) else ""
    be_log = str(tail(project, PreviewProcess.backend)) if callable(tail) else ""
    return BootProbe(
        fe_status=str(info.fe_status),
        be_status=str(info.be_status),
        warning=info.warning,
        fe_log=fe_log,
        be_log=be_log,
    )


async def _default_placeholder(project: Project, ctx: ToolContext) -> list[str]:
    from app.core.errors import NotFoundError, UserError

    findings: list[str] = []
    for path in _PLACEHOLDER_FILES:
        try:
            content = (await ctx.workspace.read(project, path)).content
        except (NotFoundError, UserError):
            continue
        if any(needle in content for needle in _PLACEHOLDER_STRINGS):
            findings.append(path)
    try:
        routes = (await ctx.workspace.read(project, _ROUTES_FILE)).content
        # The stock index route renders <HomePage /> as the index child.
        if "HomePage" in routes and "index: true" in routes:
            findings.append(_ROUTES_FILE)
    except (NotFoundError, UserError):
        pass
    return findings


async def _default_container_state(project_id: str) -> str:
    from app.sandbox.manager import get_manager

    return await get_manager().container_state(project_id)


def _default_analyzer_factory(written: list[str]) -> BuildRepairContextAnalyzer:
    return BuildRepairContextAnalyzer(RepairContextAnalyzer(), fallback_paths=written)


def _default_repair_factory(analyzer: Any) -> RepairLoopController:
    return RepairLoopController(analyzer=analyzer, stage=Stage.build, owns_stage_status=False)


__all__ = [
    "BootProbe",
    "BuildVerificationController",
    "BuildVerifyReport",
    "STOP_BOOT",
    "STOP_BUDGET",
    "STOP_CANCELLED",
    "STOP_ENV",
    "STOP_NO_TARGET",
    "STOP_PLACEHOLDER",
    "STOP_PROVIDER",
    "STOP_REPAIR",
    "STOP_TYPECHECK",
    "STOP_WALL_CLOCK",
    "StepProbe",
]
