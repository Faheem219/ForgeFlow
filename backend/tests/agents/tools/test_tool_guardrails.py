"""Guardrails: unsafe paths / host-access attempts are rejected before touching the sandbox."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.db.models import Project, Run
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")


async def _dispatch(ctx: ToolContext, name: str, args: dict[str, object]) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(await default_registry().dispatch(ctx, name, args)))


async def test_absolute_and_traversal_paths_are_rejected(
    project_run: tuple[Project, Run],
) -> None:
    project, run = project_run
    ctx = ToolContext.build(
        project, run, workspace=FakeWorkspace(), exec_service=FakeExec(), preview=FakePreview()
    )

    absolute = await _dispatch(ctx, "read_file", {"path": "/etc/passwd"})
    assert absolute["ok"] is False and "bsolute" in absolute["error"]  # "Absolute paths…"

    traversal = await _dispatch(ctx, "write_file", {"path": "../escape.txt", "content": "x"})
    assert traversal["ok"] is False and "raversal" in traversal["error"]  # "Path traversal…"

    home = await _dispatch(ctx, "read_file", {"path": "~/.ssh/id_rsa"})
    assert home["ok"] is False


async def test_run_command_rejects_a_host_cwd_before_executing(
    project_run: tuple[Project, Run],
) -> None:
    project, run = project_run
    fake = FakeExec()
    ctx = ToolContext.build(
        project, run, workspace=FakeWorkspace(), exec_service=fake, preview=FakePreview()
    )

    result = await _dispatch(ctx, "run_command", {"cmd": ["ls"], "cwd": "/etc"})
    assert result["ok"] is False
    assert fake.calls == []  # the command never reached the sandbox
