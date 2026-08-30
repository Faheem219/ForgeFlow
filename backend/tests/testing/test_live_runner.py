"""Live runner orchestration (phase-39): base-URL override, env=live results, parser reuse.

The runner's collaborators (exec, workspace, sandbox egress, the URL probe) are all injected, so the
whole live path — warm-up, egress window, subset, parse, persist, emit — runs without Docker and
without touching the network.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.core.errors import UserError
from app.db.blobs import get_blob_store
from app.db.models import Deployment
from app.db.models.enums import DeployMode, TestEnv
from app.sandbox.exec import ExecOutcome
from app.sandbox.schemas import FileContent, FileNode
from app.testing.live import LiveTestRunner
from app.testing.models import TestStatus
from tests.testing.conftest import RecordingEmitter, make_project, playwright_report

pytestmark = pytest.mark.usefixtures("mongo_db", "blob_env")

_REPORT = "frontend/playwright-report.json"
_SPEC = "frontend/e2e/todos.spec.ts"
_LIVE_URL = "https://forgeflow-web.vercel.app"


class FakeExec:
    """Records the live invocation; serves a canned exit code + stdout."""

    def __init__(self, exit_code: int = 0, stdout: bytes = b"") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.calls: list[dict[str, object]] = []

    async def run(
        self,
        project: object,
        cmd: list[str],
        cwd: str = "",
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture: bool = True,
    ) -> ExecOutcome:
        self.calls.append({"cmd": cmd, "cwd": cwd, "env": env, "timeout": timeout})
        ref = await get_blob_store().put(self.stdout) if self.stdout else None
        return ExecOutcome(
            exec_id="e1",
            exit_code=self.exit_code,
            duration_s=0.0,
            timed_out=False,
            cancelled=False,
            output_ref=ref,
        )


class FakeWorkspace:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    async def read(self, project: object, path: str) -> FileContent:
        if path not in self.files:
            raise FileNotFoundError(path)
        return FileContent(path=path, content=self.files[path], size=len(self.files[path]))

    async def tree(
        self, project: object, path: str = ".", depth: int | None = None
    ) -> list[FileNode]:
        return [
            FileNode(path=p, type="file", size=len(c))
            for p, c in self.files.items()
            if p.startswith(path)
        ]


class FakeSandbox:
    """Tracks the egress window so tests can assert it opens and always closes."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.attached = False

    async def attach_egress_network(self, project_id: str) -> bool:
        self.events.append("attach")
        self.attached = True
        return True

    async def detach_egress_network(self, project_id: str) -> None:
        self.events.append("detach")
        self.attached = False


def _workspace(*, failing: bool = False, tagged: bool = False) -> FakeWorkspace:
    spec = "test('[ac-list] shows todos', () => {});"
    if tagged:
        spec = "test('[ac-list] @smoke shows todos', () => {});\ntest('[ac-x] other', () => {});"
    return FakeWorkspace({_REPORT: playwright_report(failing=failing), _SPEC: spec})


def _runner(
    exec_service: FakeExec,
    workspace: FakeWorkspace,
    sandbox: FakeSandbox,
    emitter: RecordingEmitter | None = None,
    *,
    probe_ok: bool = True,
) -> LiveTestRunner:
    async def probe(_url: str) -> bool:
        return probe_ok

    async def sleep(_s: float) -> None:
        return None

    return LiveTestRunner(
        exec_service=exec_service,
        workspace=workspace,
        sandbox=sandbox,
        emitter=emitter or RecordingEmitter(),
        probe=probe,
        sleep=sleep,
    )


async def _deployment(project_id: PydanticObjectId, url: str = _LIVE_URL) -> Deployment:
    return await Deployment(
        project_id=project_id,
        mode=DeployMode.seamless,
        urls={"fe": url, "be": "https://api.onrender.com"},
        status="live",
    ).insert()


async def test_playwright_is_pointed_at_the_deployed_url() -> None:
    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)
    exec_service = FakeExec()

    await _runner(exec_service, _workspace(), FakeSandbox()).run(project)

    call = exec_service.calls[0]
    env = call["env"]
    assert isinstance(env, dict)
    # The skeleton's playwright.config reads PLAYWRIGHT_BASE_URL — this is the whole override.
    assert env["PLAYWRIGHT_BASE_URL"] == _LIVE_URL
    assert call["cwd"] == "frontend"
    assert "playwright" in call["cmd"]  # type: ignore[operator]


async def test_results_are_marked_live_and_reuse_the_phase28_parser() -> None:
    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)

    run = await _runner(FakeExec(exit_code=1), _workspace(failing=True), FakeSandbox()).run(project)

    assert run.env is TestEnv.live
    assert len(run.results) == 2  # same Playwright report, same parser as the sandbox runner
    # Criterion traceability survives into production results (phase-28 tagging).
    assert {r["criterion_id"] for r in run.results} == {"ac-list", "ac-uiadd"}
    assert [f["criterion_id"] for f in run.failures] == ["ac-uiadd"]


async def test_an_explicit_base_url_overrides_the_recorded_deployment() -> None:
    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)
    exec_service = FakeExec()

    await _runner(exec_service, _workspace(), FakeSandbox()).run(
        project, base_url="https://staging.example.com"
    )

    env = exec_service.calls[0]["env"]
    assert isinstance(env, dict)
    assert env["PLAYWRIGHT_BASE_URL"] == "https://staging.example.com"


async def test_live_validation_without_a_deployment_is_refused() -> None:
    project = await make_project()

    with pytest.raises(UserError, match="deployed URL"):
        await _runner(FakeExec(), _workspace(), FakeSandbox()).run(project)


async def test_egress_is_opened_for_the_run_and_always_handed_back() -> None:
    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)
    sandbox = FakeSandbox()

    await _runner(FakeExec(), _workspace(), sandbox).run(project)

    assert sandbox.events == ["attach", "detach"]
    assert sandbox.attached is False


async def test_egress_is_handed_back_even_when_the_suite_explodes() -> None:
    """The sandbox must not keep internet access because a run crashed."""
    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)
    sandbox = FakeSandbox()

    class Exploding(FakeExec):
        async def run(self, *args: object, **kwargs: object) -> ExecOutcome:
            raise RuntimeError("docker died")

    with pytest.raises(RuntimeError):
        await _runner(Exploding(), _workspace(), sandbox).run(project)

    assert sandbox.events == ["attach", "detach"]
    assert sandbox.attached is False


async def test_a_cold_site_is_warmed_before_the_suite_runs() -> None:
    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)
    emitter = RecordingEmitter()
    woke_after = 3
    seen = {"probes": 0}

    async def probe(_url: str) -> bool:
        seen["probes"] += 1
        return seen["probes"] >= woke_after

    async def sleep(_s: float) -> None:
        return None

    runner = LiveTestRunner(
        exec_service=FakeExec(),
        workspace=_workspace(),
        sandbox=FakeSandbox(),
        emitter=emitter,
        probe=probe,
        sleep=sleep,
    )
    run = await runner.run(project)

    assert seen["probes"] == woke_after  # it waited for the spin-up instead of failing
    steps = [p.get("step") for _e, p in emitter.events if "step" in p]
    assert "live:waking" in steps and "live:warm" in steps
    assert run.env is TestEnv.live


async def test_a_site_that_never_answers_still_reports_what_the_suite_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warm-up is best-effort: an unreachable site yields real failures, not a crash."""
    monkeypatch.setenv("LIVE_WARMUP_ATTEMPTS", "2")
    reset_config()
    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)
    emitter = RecordingEmitter()

    run = await _runner(
        FakeExec(exit_code=1), _workspace(failing=True), FakeSandbox(), emitter, probe_ok=False
    ).run(project)

    steps = [p.get("step") for _e, p in emitter.events if "step" in p]
    assert "live:cold" in steps
    assert run.env is TestEnv.live
    assert len(run.failures) == 1


async def test_a_crashed_suite_becomes_one_live_failure() -> None:
    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)
    workspace = FakeWorkspace({_SPEC: "test('x', () => {});"})  # no report written

    exec_service = FakeExec(exit_code=1, stdout=b"boom")
    run = await _runner(exec_service, workspace, FakeSandbox()).run(project)

    assert len(run.results) == 1
    assert run.results[0]["status"] == TestStatus.failed
    assert "deployed URL" in run.results[0]["failure"]["message"]


async def test_tagged_suites_run_only_the_tagged_subset() -> None:
    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)
    exec_service = FakeExec()

    await _runner(exec_service, _workspace(tagged=True), FakeSandbox()).run(project)

    cmd = exec_service.calls[0]["cmd"]
    assert isinstance(cmd, list)
    assert "-g" in cmd and "@smoke" in cmd[cmd.index("-g") + 1]


async def test_untagged_suites_run_unfiltered() -> None:
    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)
    exec_service = FakeExec()

    await _runner(exec_service, _workspace(), FakeSandbox()).run(project)

    assert "-g" not in exec_service.calls[0]["cmd"]  # type: ignore[operator]


async def test_live_events_carry_the_live_env_for_the_validation_ui() -> None:
    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)
    emitter = RecordingEmitter()

    await _runner(FakeExec(), _workspace(), FakeSandbox(), emitter).run(project)

    results = [p for _e, p in emitter.events if p.get("type") == "test"]
    summary = next(p for _e, p in emitter.events if p.get("type") == "summary")
    assert results and all(p["env"] == "live" for p in results)
    assert summary["env"] == "live" and summary["url"] == _LIVE_URL


async def test_a_live_run_never_moves_the_repair_anchor() -> None:
    """`last-passing` anchors repair to the workspace; prod may be several commits behind."""
    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)
    workspace = _workspace()

    await _runner(FakeExec(), workspace, FakeSandbox()).run(project)

    # The live workspace seam exposes no set_last_passing at all — assert it was never needed.
    assert not hasattr(workspace, "last_passing")


async def test_a_live_run_does_not_hijack_the_sandbox_repair_loop() -> None:
    """Live runs share the TestRun collection, so `latest` must be asked for the right env.

    Without this the newest run after a live validation would be the live one, and clicking Repair
    would patch workspace code from a production result — phase-40's path, not phase-31's.
    """
    from app.db.models import TestRun
    from app.db.repos import TestRunRepo

    project = await make_project()
    assert project.id is not None
    await _deployment(project.id)

    sandbox_run = await TestRun(project_id=project.id, env=TestEnv.sandbox).insert()
    live_run = await _runner(FakeExec(), _workspace(), FakeSandbox()).run(project)  # newer

    runs = TestRunRepo()
    newest = await runs.latest(project.id)
    newest_sandbox = await runs.latest(project.id, env=TestEnv.sandbox)
    newest_live = await runs.latest(project.id, env=TestEnv.live)

    assert newest is not None and newest.id == live_run.id  # newest overall
    assert newest_sandbox is not None and newest_sandbox.id == sandbox_run.id
    assert newest_live is not None and newest_live.id == live_run.id
