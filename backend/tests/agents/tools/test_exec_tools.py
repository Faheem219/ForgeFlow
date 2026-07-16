"""Exec-family tools (run_command / install_deps / run_tests) against a fake ExecService."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.db.models import Project, Run
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db", "blob_env")


def _ctx(project: Project, run: Run, exec_service: FakeExec) -> ToolContext:
    return ToolContext.build(
        project, run, workspace=FakeWorkspace(), exec_service=exec_service, preview=FakePreview()
    )


async def _dispatch(ctx: ToolContext, name: str, args: dict[str, object]) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(await default_registry().dispatch(ctx, name, args)))


async def test_run_command_forwards_argv_and_returns_output(
    project_run: tuple[Project, Run],
) -> None:
    fake = FakeExec(exit_code=0, output=b"built ok")
    ctx = _ctx(*project_run, fake)

    result = await _dispatch(ctx, "run_command", {"cmd": ["pnpm", "build"], "cwd": "frontend"})
    assert result["exit_code"] == 0
    assert result["output"] == "built ok"
    assert fake.calls == [{"cmd": ["pnpm", "build"], "cwd": "frontend", "timeout": None}]


async def test_install_deps_builds_the_right_command(project_run: tuple[Project, Run]) -> None:
    fake = FakeExec()
    ctx = _ctx(*project_run, fake)

    await _dispatch(ctx, "install_deps", {"manager": "pnpm", "packages": ["zod"], "dev": True})
    await _dispatch(ctx, "install_deps", {"manager": "npm", "packages": ["react"]})
    await _dispatch(ctx, "install_deps", {})  # no packages → install the lockfile

    assert [c["cmd"] for c in fake.calls] == [
        ["pnpm", "add", "-D", "zod"],
        ["npm", "install", "react"],
        ["pnpm", "install"],
    ]


async def test_run_tests_maps_scope_and_reports_pass_fail(
    project_run: tuple[Project, Run],
) -> None:
    passing = FakeExec(exit_code=0)
    ctx = _ctx(*project_run, passing)
    result = await _dispatch(ctx, "run_tests", {"scope": "unit"})
    assert passing.calls[0]["cmd"] == ["pnpm", "test:unit"]
    assert result["passed"] is True

    failing = FakeExec(exit_code=1)
    ctx2 = _ctx(*project_run, failing)
    result2 = await _dispatch(ctx2, "run_tests", {"scope": "e2e", "filter": "smoke"})
    assert failing.calls[0]["cmd"] == ["pnpm", "test:e2e", "smoke"]
    assert result2["passed"] is False


async def test_preview_tools_delegate(project_run: tuple[Project, Run]) -> None:
    project, run = project_run
    preview = FakePreview()
    ctx = ToolContext.build(
        project, run, workspace=FakeWorkspace(), exec_service=FakeExec(), preview=preview
    )

    started = await _dispatch(ctx, "start_preview", {})
    assert started["fe_status"] == "running" and preview.started == 1

    await _dispatch(ctx, "restart_preview", {})
    assert preview.restarted == 1
