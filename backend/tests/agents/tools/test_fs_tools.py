"""FS + git tools against a real local-runtime workspace (phase-21)."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest

from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.db.models import Project, Run
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")


def _ctx(project: Project, run: Run) -> ToolContext:
    return ToolContext.build(
        project, run, workspace=FakeWorkspace(), exec_service=FakeExec(), preview=FakePreview()
    )


async def _dispatch(ctx: ToolContext, name: str, args: dict[str, object]) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(await default_registry().dispatch(ctx, name, args)))


async def test_write_then_read_round_trips(project_run: tuple[Project, Run]) -> None:
    ctx = _ctx(*project_run)

    written = await _dispatch(
        ctx, "write_file", {"path": "src/app.ts", "content": "export const x=1;"}
    )
    assert written["ok"] is True and written["path"] == "src/app.ts"

    read = await _dispatch(ctx, "read_file", {"path": "src/app.ts"})
    assert read["content"] == "export const x=1;"


async def test_list_dir_shows_written_files(project_run: tuple[Project, Run]) -> None:
    ctx = _ctx(*project_run)
    await _dispatch(ctx, "write_file", {"path": "a.ts", "content": "1"})
    await _dispatch(ctx, "write_file", {"path": "b.ts", "content": "2"})

    listed = await _dispatch(ctx, "list_dir", {"path": "."})
    names = {str(e["path"]).split("/")[-1] for e in listed["entries"]}
    assert {"a.ts", "b.ts"} <= names


async def test_git_commit_creates_then_noops(project_run: tuple[Project, Run]) -> None:
    project, run = project_run
    ctx = _ctx(project, run)
    await _dispatch(ctx, "write_file", {"path": "a.ts", "content": "1"})

    first = await _dispatch(ctx, "git_commit", {"message": "init"})
    assert first["committed"] is True and first["sha"]

    second = await _dispatch(ctx, "git_commit", {"message": "again"})
    assert second["committed"] is False  # nothing changed

    # The action trail is recorded on the Run.
    reloaded = await Run.get(run.id)
    assert reloaded is not None
    assert [c["tool"] for c in reloaded.tool_calls] == [
        "write_file",
        "git_commit",
        "git_commit",
    ]
    assert all(c["ok"] for c in reloaded.tool_calls)
