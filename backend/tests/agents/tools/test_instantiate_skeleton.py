"""instantiate_skeleton copies the prebuilt scaffold into the workspace (phase-21)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.agents.tools.skeleton import copy_skeleton
from app.core.config import reset_config
from app.db.models import Project, Run
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")


def _make_skeleton(root: Path) -> None:
    (root / "frontend").mkdir(parents=True)
    (root / "backend" / "src").mkdir(parents=True)
    (root / "frontend" / "package.json").write_text('{"name":"fe"}', encoding="utf-8")
    (root / "backend" / "src" / "index.ts").write_text("export {};", encoding="utf-8")


async def test_tool_instantiates_the_skeleton(
    project_run: tuple[Project, Run], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skeleton = tmp_path / "skeleton"
    _make_skeleton(skeleton)
    monkeypatch.setenv("APP_SKELETON_DIR", str(skeleton))
    reset_config()

    project, run = project_run
    ws = FakeWorkspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )

    result = json.loads(await default_registry().dispatch(ctx, "instantiate_skeleton", {}))
    assert result["ok"] is True and result["files"] == 2
    assert "frontend/package.json" in result["paths"]

    # The files are really in the workspace.
    pkg = await ws.read(project, "frontend/package.json")
    assert pkg.content == '{"name":"fe"}'


async def test_copy_skeleton_direct_call(project_run: tuple[Project, Run], tmp_path: Path) -> None:
    skeleton = tmp_path / "skeleton"
    _make_skeleton(skeleton)
    project, _ = project_run
    ws = FakeWorkspace()

    written = await copy_skeleton(ws, project, source=skeleton)
    assert sorted(written) == ["backend/src/index.ts", "frontend/package.json"]
