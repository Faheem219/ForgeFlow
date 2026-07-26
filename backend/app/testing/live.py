"""Live validation runner (phase-39): re-run the E2E suite against the **deployed** URL.

This is the step that proves a deployment actually works, rather than that it merely went green in
a sandbox. It deliberately reuses the *same* Playwright suites and the *same* parsers as the sandbox
runner (phase-27/28) — only the base URL and the selected subset differ — so preview and production
are judged by one oracle. The result is a structured ``TestRun`` marked ``env=live``, which is what
the live-repair path (phase-40) consumes.

Three things make a live run different from a sandbox run:

**Egress.** The sandbox runs on a sealed internal network; a deployed URL is on the public internet.
The run therefore borrows a routed network (``attach_egress_network``) and gives it back in a
``finally`` — narrow in *time* rather than in reach, and never the host namespace. This is the
widest relaxation of the sandbox isolation default in the system and is flagged for phase-47.

**Cold starts.** Free-tier hosting sleeps. The control plane probes the URL until it answers before
handing the suite a browser, so a spin-up delay is never recorded as a product failure.

**Subset.** Production runs the smoke/critical slice, selected by ``@smoke``/``@critical`` tags in
the test titles. When the generated suite carries no such tags the *whole* E2E suite runs — an
untagged suite must never silently select nothing and report a vacuous pass.

The last-passing git ref is deliberately **not** advanced by a live run: that ref anchors the
diff-aware repair loop to the workspace, and a live result describes deployed code, which may be
several commits behind.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from beanie import PydanticObjectId

from app.core.config import get_config
from app.core.errors import UserError
from app.db.blobs import BlobStore, get_blob_store
from app.db.models import Deployment, Project, TestRun
from app.db.models.enums import Stage, TestEnv, TestKind
from app.db.repos import TestSuiteRepo
from app.realtime.hub import emit
from app.realtime.schemas import EventType
from app.sandbox.exec import ExecOutcome
from app.sandbox.network import egress_window
from app.sandbox.schemas import FileContent, FileNode
from app.testing.models import FRAMEWORK_PLAYWRIGHT, Failure, TestResult, TestStatus, summarize
from app.testing.parsers import extract_referenced_files, parse
from app.testing.runner import FE_DIR, PLAYWRIGHT_REPORT

logger = logging.getLogger(__name__)

#: Playwright's convention: a tag lives in the test title, so ``-g`` selects it.
SMOKE_TAG = "@smoke"
CRITICAL_TAG = "@critical"

#: ``live_test_selection`` → the tags it asks for. ``all`` means "no filter at all".
SELECTION_TAGS: dict[str, tuple[str, ...]] = {
    "smoke": (SMOKE_TAG,),
    "critical": (CRITICAL_TAG,),
    "both": (SMOKE_TAG, CRITICAL_TAG),
    "all": (),
}

_E2E_DIR = f"{FE_DIR}/e2e"
_STDOUT_TAIL = 4000


class _ExecRunner(Protocol):
    async def run(
        self,
        project: Project,
        cmd: list[str],
        cwd: str = "",
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture: bool = True,
    ) -> ExecOutcome: ...


class _LiveWorkspace(Protocol):
    async def read(self, project: Project, path: str) -> FileContent: ...

    async def tree(
        self, project: Project, path: str = ".", depth: int | None = None
    ) -> list[FileNode]: ...


class _Sandbox(Protocol):
    """The egress window the live run borrows (see module docstring)."""

    async def attach_egress_network(self, project_id: str) -> bool: ...

    async def detach_egress_network(self, project_id: str) -> None: ...


#: Probes the deployed URL; returns True once it answers. Injected so tests never hit the network.
UrlProbe = Callable[[str], Awaitable[bool]]


@dataclass(frozen=True)
class LiveSelection:
    """Which slice of the E2E suite production gets, and why."""

    tags: tuple[str, ...]
    grep: str | None  # the Playwright ``-g`` pattern; None runs everything
    reason: str

    @property
    def full_suite(self) -> bool:
        return self.grep is None


def select_live_subset(requested: str, specs: dict[str, str]) -> LiveSelection:
    """Pick the live subset from the tags the generated suite actually carries.

    ``specs`` maps spec path → source. Selection is driven by what is *present*, never by what was
    merely configured: asking for ``@smoke`` when nothing is tagged would select zero tests, and a
    zero-test run reads as a pass. Falling back to the full suite keeps live validation honest.
    """
    wanted = SELECTION_TAGS.get(requested.strip().lower(), SELECTION_TAGS["both"])
    if not wanted:
        return LiveSelection((), None, "full E2E suite (configured)")

    source = "\n".join(specs.values())
    present = tuple(tag for tag in wanted if tag in source)
    if not present:
        return LiveSelection(
            (),
            None,
            f"full E2E suite — no {' or '.join(wanted)} tagged tests in this suite",
        )
    return LiveSelection(present, "|".join(present), f"tagged {' + '.join(present)}")


class LiveTestRunner:
    def __init__(
        self,
        exec_service: _ExecRunner | None = None,
        workspace: _LiveWorkspace | None = None,
        blob_store: BlobStore | None = None,
        suites: TestSuiteRepo | None = None,
        emitter: Callable[..., Awaitable[object]] = emit,
        sandbox: _Sandbox | None = None,
        probe: UrlProbe | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._exec = exec_service
        self._workspace = workspace
        self._blobs = blob_store
        self._suites = suites or TestSuiteRepo()
        self._emit = emitter
        self._sandbox = sandbox
        self._probe = probe or _http_probe
        import asyncio

        self._sleep = sleep or asyncio.sleep

    # -- collaborators (resolved late so importing this module needs no Docker) ---------

    def _exec_service(self) -> _ExecRunner:
        if self._exec is None:
            from app.sandbox.exec import get_exec_service

            self._exec = get_exec_service()
        return self._exec

    def _workspace_service(self) -> _LiveWorkspace:
        if self._workspace is None:
            from app.sandbox.workspace import WorkspaceService

            self._workspace = WorkspaceService()
        return self._workspace

    def _sandbox_manager(self) -> _Sandbox:
        if self._sandbox is None:
            from app.sandbox.manager import get_manager

            self._sandbox = get_manager()
        return self._sandbox

    def _blob_store(self) -> BlobStore:
        return self._blobs if self._blobs is not None else get_blob_store()

    # -- the run -----------------------------------------------------------------------

    async def run(self, project: Project, *, base_url: str | None = None) -> TestRun:
        project_id = project.id
        if project_id is None:  # pragma: no cover - a persisted project always carries an id
            raise UserError("Project is not persisted")
        channel = str(project_id)

        url = base_url or await self._deployed_url(project_id)
        config = get_config()
        selection = select_live_subset(
            str(config.get("live_test_selection")), await self._read_specs(project)
        )
        await self._progress(channel, "selection", url=url, reason=selection.reason)

        outcome, stdout = await self._run_against(project, url, selection)
        results = await self._parse(project, outcome, stdout)

        run_doc = await TestRun(
            project_id=project_id,
            suite_refs=await self._suite_refs(project_id),
            results=[r.model_dump(mode="json") for r in results],
            failures=[r.model_dump(mode="json") for r in results if r.status is TestStatus.failed],
            stdout_ref=await self._store_stdout(outcome, stdout),
            env=TestEnv.live,
        ).insert()

        # NB: no last-passing advance — that ref anchors repair to the workspace, not to prod.
        await self._emit_results(channel, results, run_doc, url)
        return run_doc

    async def _run_against(
        self, project: Project, url: str, selection: LiveSelection
    ) -> tuple[ExecOutcome, bytes]:
        """Warm the site, borrow egress for as long as the suite needs, then give it back."""
        project_id = str(project.id)
        config = get_config()
        sandbox = self._sandbox_manager()

        # The same ref-counted window dependency installs use, so two egress users can never revoke
        # each other's route; it always detaches, including on timeout/crash.
        async with egress_window(project_id, manager=sandbox):
            await self._warm_up(project_id, url)
            cmd = ["pnpm", "exec", "playwright", "test", "--reporter=json"]
            cmd += [f"--retries={int(config.get('live_test_retries'))}"]
            if selection.grep:
                cmd += ["-g", selection.grep]
            outcome = await self._exec_service().run(
                project,
                cmd,
                cwd=FE_DIR,
                env={
                    "PLAYWRIGHT_BASE_URL": url,  # the skeleton config reads this (phase-22)
                    "PLAYWRIGHT_JSON_OUTPUT_NAME": PLAYWRIGHT_REPORT,
                    "CI": "1",
                },
                timeout=float(config.get("live_test_timeout_s")),
            )

        return outcome, await self._read_blob(outcome.output_ref)

    async def _warm_up(self, channel: str, url: str) -> None:
        """Poll until the deployment answers, so a cold start is never recorded as a failure."""
        config = get_config()
        attempts = int(config.get("live_warmup_attempts"))
        interval = float(config.get("live_warmup_interval_s"))
        for attempt in range(1, attempts + 1):
            if await self._probe(url):
                await self._progress(channel, "warm", url=url, attempts=attempt)
                return
            await self._progress(channel, "waking", url=url, attempt=attempt)
            if attempt < attempts:
                await self._sleep(interval)
        # Not fatal: the suite still runs and will report what it finds — but say so plainly.
        logger.warning("live URL %s did not answer warm-up probes", url)
        await self._progress(channel, "cold", url=url, attempts=attempts)

    # -- inputs ------------------------------------------------------------------------

    async def _deployed_url(self, project_id: PydanticObjectId) -> str:
        deployment = (
            await Deployment.find({"project_id": project_id})
            .sort("-created_at", "-_id")
            .first_or_none()
        )
        url = (deployment.urls.get("fe") or deployment.urls.get("be")) if deployment else None
        if not url:
            raise UserError("Live validation needs a deployed URL — run the Deploy stage first.")
        return url

    async def _read_specs(self, project: Project) -> dict[str, str]:
        """The E2E sources, for tag detection. Unreadable workspace → run the full suite."""
        specs: dict[str, str] = {}
        try:
            nodes = await self._workspace_service().tree(project, _E2E_DIR, depth=None)
        except Exception:
            logger.debug("could not list %s; live run falls back to the full suite", _E2E_DIR)
            return specs
        for node in nodes:
            if node.type != "file" or not node.path.endswith((".ts", ".tsx")):
                continue
            try:
                content = await self._workspace_service().read(project, node.path)
                specs[node.path] = content.content
            except Exception:
                continue
        return specs

    # -- outputs -----------------------------------------------------------------------

    async def _parse(
        self, project: Project, outcome: ExecOutcome, stdout: bytes
    ) -> list[TestResult]:
        try:
            report = await self._workspace_service().read(project, f"{FE_DIR}/{PLAYWRIGHT_REPORT}")
            parsed = parse(FRAMEWORK_PLAYWRIGHT, report.content)
        except Exception:
            parsed = []  # crashed before writing a report — a suite failure, handled below

        if not parsed and (outcome.exit_code != 0 or outcome.timed_out):
            tail = stdout.decode("utf-8", errors="replace")[-_STDOUT_TAIL:]
            detail = f"playwright exited {outcome.exit_code}"
            if outcome.timed_out:
                detail += " (timed out)"
            parsed = [
                TestResult(
                    name="live E2E suite",
                    status=TestStatus.failed,
                    framework=FRAMEWORK_PLAYWRIGHT,
                    failure=Failure(
                        message=f"{detail} against the deployed URL without a parseable report",
                        stack=tail or None,
                        files_referenced=extract_referenced_files(tail),
                    ),
                )
            ]
        return parsed

    async def _suite_refs(self, project_id: PydanticObjectId) -> list[PydanticObjectId]:
        suite = await self._suites.latest(project_id, TestKind.e2e)
        return [suite.id] if suite is not None and suite.id is not None else []

    async def _read_blob(self, ref: str | None) -> bytes:
        if not ref:
            return b""
        try:
            return await self._blob_store().get(ref)
        except Exception:  # a missing/broken blob must not fail the run
            return b""

    async def _store_stdout(self, outcome: ExecOutcome, stdout: bytes) -> str | None:
        data = f"=== playwright @ live (exit {outcome.exit_code}) ===\n".encode() + stdout
        try:
            return await self._blob_store().put(data)
        except Exception:  # blob trouble must not invalidate an otherwise-good run
            return None

    async def _progress(self, channel: str, step: str, **payload: Any) -> None:
        await self._emit(
            channel,
            EventType.progress,
            {"stage": "validate", "step": f"live:{step}", **payload},
            stage=Stage.validate,
        )

    async def _emit_results(
        self, channel: str, results: list[TestResult], run_doc: TestRun, url: str
    ) -> None:
        for result in results:
            await self._emit(
                channel,
                EventType.test_result,
                {
                    "type": "test",
                    "env": str(TestEnv.live),
                    "name": result.name,
                    "status": str(result.status),
                    "criterion_id": result.criterion_id,
                    "file": result.file,
                    "framework": result.framework,
                },
                stage=Stage.validate,
            )
        summary = summarize(results)
        await self._emit(
            channel,
            EventType.test_result,
            {
                "type": "summary",
                "env": str(TestEnv.live),
                "run_id": str(run_doc.id),
                "url": url,
                "total": summary.total,
                "passed": summary.passed,
                "failed": summary.failed,
                "skipped": summary.skipped,
                "green": summary.green,
            },
            stage=Stage.validate,
        )


async def _http_probe(url: str) -> bool:  # pragma: no cover - real network
    """Has the deployment woken up? Any HTTP answer counts — the suite judges correctness."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url)
        return response.status_code < 500
    except Exception:
        return False


__all__ = ["LiveSelection", "LiveTestRunner", "select_live_subset", "SELECTION_TAGS"]
