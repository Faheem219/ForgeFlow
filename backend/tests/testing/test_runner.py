"""Runner orchestration (phase-28): scope selection, combine, persist, suite failures, events.

The fast tests drive the runner with a fake exec + a workspace pre-seeded with reporter JSON (no
Docker). The opt-in ``docker``-marked test runs the runner's exact Jest invocation against the real
skeleton toolchain and parses it, proving the reporter flags produce parseable output.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.db.blobs import get_blob_store
from app.db.models.enums import TestKind
from app.db.repos import TestRunRepo, TestSuiteRepo
from app.testing.models import TestStatus
from app.testing.parsers import parse_jest
from app.testing.runner import TestRunner
from tests.testing.conftest import (
    FakeRunnerWorkspace,
    RecordingEmitter,
    ScriptedExec,
    jest_report,
    make_project,
    playwright_report,
    vitest_report,
)

pytestmark = pytest.mark.usefixtures("mongo_db", "blob_env")

_VITEST = "frontend/vitest-report.json"
_JEST = "backend/jest-report.json"
_PLAYWRIGHT = "frontend/playwright-report.json"


async def test_unit_scope_runs_vitest_and_jest_only() -> None:
    project = await make_project()
    exec_service = ScriptedExec()
    workspace = FakeRunnerWorkspace({_VITEST: vitest_report(), _JEST: jest_report(failing=False)})

    run = await TestRunner(exec_service=exec_service, workspace=workspace).run(
        project, scope="unit"
    )

    assert [c["framework"] for c in exec_service.calls] == ["vitest", "jest"]  # no playwright
    assert len(run.results) == 2
    assert run.failures == []


async def test_all_scope_combines_three_reporters() -> None:
    project = await make_project()
    exec_service = ScriptedExec(
        exit_codes={"jest": 1, "playwright": 1},
        stdout={"jest": b"jest ran", "vitest": b"vitest ran", "playwright": b"pw ran"},
    )
    workspace = FakeRunnerWorkspace(
        {
            _VITEST: vitest_report(),
            _JEST: jest_report(failing=True),
            _PLAYWRIGHT: playwright_report(failing=True),
        }
    )

    run = await TestRunner(exec_service=exec_service, workspace=workspace).run(project, scope="all")

    assert [c["framework"] for c in exec_service.calls] == ["vitest", "jest", "playwright"]
    assert len(run.results) == 5  # 1 vitest + 2 jest + 2 playwright
    # Two real per-test failures carried through with their criterion ids.
    failed_crits = {f["criterion_id"] for f in run.failures}
    assert failed_crits == {"ac-empty", "ac-uiadd"}

    # Combined stdout is stored with per-framework headers.
    assert run.stdout_ref is not None
    combined = (await get_blob_store().get(run.stdout_ref)).decode("utf-8")
    assert "=== jest (exit 1) ===" in combined and "=== vitest (exit 0) ===" in combined


async def test_missing_report_with_nonzero_exit_is_a_suite_failure() -> None:
    project = await make_project()
    exec_service = ScriptedExec(
        exit_codes={"jest": 1},
        stdout={"jest": b"SyntaxError: boom\n    at backend/src/app.ts:1:1"},
    )
    # Only the vitest report exists; jest crashed before writing one.
    workspace = FakeRunnerWorkspace({_VITEST: vitest_report()})

    run = await TestRunner(exec_service=exec_service, workspace=workspace).run(
        project, scope="unit"
    )

    assert len(run.failures) == 1
    suite_fail = run.failures[0]
    assert suite_fail["name"] == "jest suite"
    assert "exited 1" in suite_fail["failure"]["message"]
    assert "backend/src/app.ts" in suite_fail["failure"]["files_referenced"]


async def test_persists_run_with_suite_refs() -> None:
    project = await make_project()
    assert project.id is not None
    suite = await TestSuiteRepo().create_version(
        project.id, TestKind.unit, ["backend/src/features/todos/todos.test.ts"], ["ac-add"]
    )

    exec_service = ScriptedExec()
    workspace = FakeRunnerWorkspace({_VITEST: vitest_report(), _JEST: jest_report(failing=False)})
    run = await TestRunner(exec_service=exec_service, workspace=workspace).run(
        project, scope="unit"
    )

    assert run.suite_refs == [suite.id]
    assert run.id is not None
    reloaded = await TestRunRepo().get(run.id)
    assert reloaded is not None and len(reloaded.results) == 2


async def test_emits_per_test_and_summary_events() -> None:
    project = await make_project()
    emitter = RecordingEmitter()
    workspace = FakeRunnerWorkspace({_VITEST: vitest_report(), _JEST: jest_report(failing=False)})

    await TestRunner(exec_service=ScriptedExec(), workspace=workspace, emitter=emitter).run(
        project, scope="unit"
    )

    kinds = [p.get("type") for _e, p in emitter.events]
    assert kinds.count("test") == 2  # one per result
    summaries = [p for _e, p in emitter.events if p.get("type") == "summary"]
    assert len(summaries) == 1
    assert summaries[0]["green"] is True and summaries[0]["passed"] == 2


async def test_result_dicts_round_trip_to_test_result() -> None:
    project = await make_project()
    workspace = FakeRunnerWorkspace({_JEST: jest_report(failing=True)})
    run = await TestRunner(
        exec_service=ScriptedExec(exit_codes={"jest": 1}), workspace=workspace
    ).run(project, scope="unit")
    # Persisted dicts re-validate into TestResult (the API relies on this).
    from app.testing.models import TestResult

    results = [TestResult.model_validate(r) for r in run.results]
    statuses = {r.criterion_id: r.status for r in results}
    assert statuses["ac-add"] is TestStatus.passed
    assert statuses["ac-empty"] is TestStatus.failed


# --------------------------------------------------------------------- real toolchain (opt-in)

_PNPM = shutil.which("pnpm")
_BUILD_ENABLED = os.getenv("FORGEFLOW_SKELETON_BUILD") == "1" and _PNPM is not None


@pytest.mark.docker
@pytest.mark.skipif(
    not _BUILD_ENABLED,
    reason="set FORGEFLOW_SKELETON_BUILD=1 (with pnpm available) to run the real toolchain",
)
def test_runner_jest_invocation_is_parseable(tmp_path: Path) -> None:
    """The runner's exact Jest reporter flags produce JSON that parse_jest understands."""
    from app.agents.tools.skeleton import skeleton_source_dir

    assert _PNPM is not None
    dest = tmp_path / "app"
    shutil.copytree(skeleton_source_dir(), dest)
    feature = dest / "backend" / "src" / "features" / "health"
    feature.mkdir(parents=True)
    (feature / "health.test.ts").write_text(
        "import request from 'supertest'\n"
        "import { createApp } from '../../app'\n"
        "describe('health', () => {\n"
        "  it('[ac-health] returns ok', async () => {\n"
        "    const res = await request(createApp()).get('/health')\n"
        "    expect(res.status).toBe(200)\n"
        "  })\n"
        "})\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=dest, check=True)
    subprocess.run([_PNPM, "install", "--no-frozen-lockfile"], cwd=dest, check=True)
    subprocess.run(
        [
            _PNPM,
            "exec",
            "jest",
            "--json",
            "--outputFile=jest-report.json",
            "--testLocationInResults",
        ],
        cwd=dest / "backend",
        check=True,
    )
    report = (dest / "backend" / "jest-report.json").read_text(encoding="utf-8")
    results = parse_jest(report)
    assert any(r.criterion_id == "ac-health" and r.status is TestStatus.passed for r in results)
