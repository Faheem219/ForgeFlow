"""Test runner (phase-28): execute the suites in the sandbox and persist structured results.

Runs each framework with a JSON reporter writing to a workspace file, reads it back through the FS
layer, and parses it into the unified :class:`TestResult` shape. On an all-green run it advances the
``last-passing`` git ref — the anchor the diff-aware repair loop (phase-29) uses. Everything the
runner touches (exec, workspace, blobs) is injected behind a Protocol, so the orchestration is fully
testable without Docker (a fake exec + a workspace pre-seeded with reporter JSON).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from beanie import PydanticObjectId

from app.core.config import get_config
from app.core.errors import NotFoundError, UserError
from app.db.blobs import BlobStore, get_blob_store
from app.db.models import Project, TestRun
from app.db.models.enums import Stage, TestEnv, TestKind
from app.db.repos import TestSuiteRepo
from app.realtime.hub import emit
from app.realtime.schemas import EventType
from app.sandbox.exec import ExecOutcome
from app.sandbox.schemas import FileContent, GitInfo
from app.sandbox.workspace import WorkspaceService
from app.testing.models import (
    FRAMEWORK_JEST,
    FRAMEWORK_PLAYWRIGHT,
    FRAMEWORK_VITEST,
    Failure,
    TestResult,
    TestScope,
    TestStatus,
    summarize,
)
from app.testing.parsers import extract_referenced_files, parse

logger = logging.getLogger(__name__)

# Where each reporter writes its JSON (relative to the package cwd the command runs in).
_VITEST_REPORT = "vitest-report.json"
_JEST_REPORT = "jest-report.json"
# Public: the live runner (phase-39) reuses the same Playwright invocation shape.
PLAYWRIGHT_REPORT = "playwright-report.json"

FE_DIR = "frontend"
_BE_DIR = "backend"

# How much captured stdout to keep on a suite-level failure (a crashed run has no per-test JSON).
_STDOUT_TAIL = 4000


class _ExecRunner(Protocol):
    """The slice of :class:`~app.sandbox.exec.ExecService` the runner uses (tests inject a fake)."""

    async def run(
        self,
        project: Project,
        cmd: list[str],
        cwd: str = "",
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture: bool = True,
    ) -> ExecOutcome: ...


class _RunnerWorkspace(Protocol):
    """The slice of :class:`~app.sandbox.workspace.WorkspaceService` the runner uses."""

    async def read(self, project: Project, path: str) -> FileContent: ...

    async def git_info(self, project: Project) -> GitInfo: ...

    async def set_last_passing(self, project: Project, sha: str) -> None: ...


@dataclass(frozen=True)
class _Invocation:
    framework: str
    cwd: str
    cmd: list[str]
    report_path: str  # workspace-relative
    env: dict[str, str] | None = field(default=None)


def _plan_invocations(scope: TestScope, name_filter: str | None) -> list[_Invocation]:
    invocations: list[_Invocation] = []
    if scope in ("unit", "all"):
        vitest = ["pnpm", "exec", "vitest", "run", "--reporter=json"]
        vitest.append(f"--outputFile={_VITEST_REPORT}")
        jest = ["pnpm", "exec", "jest", "--json", "--testLocationInResults"]
        jest.append(f"--outputFile={_JEST_REPORT}")
        if name_filter:
            vitest += ["-t", name_filter]
            jest += ["-t", name_filter]
        invocations.append(
            _Invocation(FRAMEWORK_VITEST, FE_DIR, vitest, f"{FE_DIR}/{_VITEST_REPORT}")
        )
        invocations.append(_Invocation(FRAMEWORK_JEST, _BE_DIR, jest, f"{_BE_DIR}/{_JEST_REPORT}"))
    if scope in ("e2e", "all"):
        pw = ["pnpm", "exec", "playwright", "test", "--reporter=json"]
        if name_filter:
            pw += ["-g", name_filter]
        invocations.append(
            _Invocation(
                FRAMEWORK_PLAYWRIGHT,
                FE_DIR,
                pw,
                f"{FE_DIR}/{PLAYWRIGHT_REPORT}",
                env={"PLAYWRIGHT_JSON_OUTPUT_NAME": PLAYWRIGHT_REPORT},
            )
        )
    return invocations


class TestRunner:
    def __init__(
        self,
        exec_service: _ExecRunner | None = None,
        workspace: _RunnerWorkspace | None = None,
        blob_store: BlobStore | None = None,
        suites: TestSuiteRepo | None = None,
        emitter: Callable[..., Awaitable[object]] = emit,
    ) -> None:
        from app.sandbox.exec import get_exec_service

        self._exec: _ExecRunner = exec_service or get_exec_service()
        self._workspace: _RunnerWorkspace = workspace or WorkspaceService()
        self._blobs = blob_store
        self._suites = suites or TestSuiteRepo()
        self._emit = emitter

    def _blob_store(self) -> BlobStore:
        return self._blobs if self._blobs is not None else get_blob_store()

    async def run(
        self, project: Project, scope: TestScope = "all", name_filter: str | None = None
    ) -> TestRun:
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            raise UserError("Project is not persisted")
        channel = str(project_id)
        timeout = float(get_config().get("test_runner_timeout_s"))

        backend_env = await self._backend_env(project)

        results: list[TestResult] = []
        stdout_chunks: list[bytes] = []
        for inv in _plan_invocations(scope, name_filter):
            env = {**backend_env, **(inv.env or {})} if inv.cwd == _BE_DIR else inv.env
            outcome = await self._exec.run(project, inv.cmd, cwd=inv.cwd, env=env, timeout=timeout)
            stdout = await self._read_blob(outcome.output_ref)
            stdout_chunks.append(
                f"=== {inv.framework} (exit {outcome.exit_code}) ===\n".encode() + stdout
            )
            parsed = await self._parse_invocation(project, inv, outcome, stdout)
            results.extend(parsed)

        stdout_ref = await self._store_stdout(stdout_chunks)
        suite_refs = await self._suite_refs(project_id, scope)

        run_doc = await TestRun(
            project_id=project_id,
            suite_refs=suite_refs,
            results=[r.model_dump(mode="json") for r in results],
            failures=[r.model_dump(mode="json") for r in results if r.status is TestStatus.failed],
            stdout_ref=stdout_ref,
            env=TestEnv.sandbox,
        ).insert()

        summary = summarize(results)
        if summary.green:
            info = await self._workspace.git_info(project)
            if info.current_sha:
                await self._workspace.set_last_passing(project, info.current_sha)

        await self._emit_results(channel, results, run_doc, summary.green)
        return run_doc

    async def _backend_env(self, project: Project) -> dict[str, str]:
        """Env for the backend suites — the piece the skeleton's config actually reads.

        Only the backend gets it: a Vite frontend can receive nothing but ``VITE_``-prefixed vars,
        and Playwright drives the preview servers, which carry their own env already.

        **Fail soft.** A project whose tests mock Mongoose has always run green with no database
        configured at all, and turning a missing ``APP_DB_*`` setting into a red test stage would
        break those for no gain. A suite that really needs Mongo fails on its own, loudly.

        **Superseded for skeletons from phase-65 on.** Their Jest ``globalSetup`` starts an
        in-memory MongoDB (the ``mongod`` baked into the sandbox image) and overrides
        ``MONGODB_URI`` with it, so the Test stage, the agent's ``run_tests`` and a plain
        ``pnpm test`` all reach the same database without any network. This URI now only serves
        workspaces instantiated before that — and resolves only while the preview has the sandbox
        on the ``appdb`` network, which is exactly why tests stopped depending on it.
        """
        # Lazily imported to keep the testing layer decoupled from the deploy layer.
        from app.deploy.db_provision import DbProvisioner

        try:
            return {"MONGODB_URI": await DbProvisioner().get_sandbox_test_mongodb_uri(project)}
        except Exception:  # noqa: BLE001 - never fails a run that did not need a database
            logger.warning("could not resolve a test-stage MONGODB_URI", exc_info=True)
            return {}

    # -- per-invocation ------------------------------------------------------------------

    async def _parse_invocation(
        self, project: Project, inv: _Invocation, outcome: ExecOutcome, stdout: bytes
    ) -> list[TestResult]:
        parsed: list[TestResult] = []
        try:
            report = await self._workspace.read(project, inv.report_path)
            parsed = parse(inv.framework, report.content)
        except NotFoundError:
            parsed = []  # crashed before writing a report — handled as a suite failure below

        # A non-zero exit with no per-test results means the suite itself broke (compile error,
        # missing dep, timeout). Surface it as one failed result so the repair loop sees a failure.
        if not parsed and (outcome.exit_code != 0 or outcome.timed_out):
            parsed = [self._suite_failure(inv, outcome, stdout)]
        return parsed

    def _suite_failure(self, inv: _Invocation, outcome: ExecOutcome, stdout: bytes) -> TestResult:
        tail = stdout.decode("utf-8", errors="replace")[-_STDOUT_TAIL:]
        detail = f"{inv.framework} exited {outcome.exit_code}"
        if outcome.timed_out:
            detail += " (timed out)"
        return TestResult(
            name=f"{inv.framework} suite",
            status=TestStatus.failed,
            framework=inv.framework,
            failure=Failure(
                message=f"{detail} without a parseable report",
                stack=tail or None,
                files_referenced=extract_referenced_files(tail),
            ),
        )

    # -- persistence + events ------------------------------------------------------------

    async def _suite_refs(
        self, project_id: PydanticObjectId, scope: TestScope
    ) -> list[PydanticObjectId]:
        kinds: list[TestKind] = []
        if scope in ("unit", "all"):
            kinds.append(TestKind.unit)
        if scope in ("e2e", "all"):
            kinds.append(TestKind.e2e)
        refs: list[PydanticObjectId] = []
        for kind in kinds:
            suite = await self._suites.latest(project_id, kind)
            if suite is not None and suite.id is not None:
                refs.append(suite.id)
        return refs

    async def _read_blob(self, ref: str | None) -> bytes:
        if not ref:
            return b""
        try:
            return await self._blob_store().get(ref)
        except Exception:  # a missing/broken blob must not fail the run
            return b""

    async def _store_stdout(self, chunks: list[bytes]) -> str | None:
        data = b"\n".join(c for c in chunks if c)
        if not data:
            return None
        try:
            return await self._blob_store().put(data)
        except Exception:  # blob trouble must not invalidate an otherwise-good run
            return None

    async def _emit_results(
        self, channel: str, results: list[TestResult], run_doc: TestRun, green: bool
    ) -> None:
        for result in results:
            await self._emit(
                channel,
                EventType.test_result,
                {
                    "type": "test",
                    "name": result.name,
                    "status": str(result.status),
                    "criterion_id": result.criterion_id,
                    "file": result.file,
                    "framework": result.framework,
                },
                stage=Stage.test,
            )
        summary = summarize(results)
        await self._emit(
            channel,
            EventType.test_result,
            {
                "type": "summary",
                "run_id": str(run_doc.id),
                "total": summary.total,
                "passed": summary.passed,
                "failed": summary.failed,
                "skipped": summary.skipped,
                "green": green,
            },
            stage=Stage.test,
        )
