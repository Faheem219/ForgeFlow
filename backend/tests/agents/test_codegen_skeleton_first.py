"""Skeleton-first (phase-23 user directive): the build's first tool action is instantiate_skeleton,
and the model never generates the frontend scaffold — only feature code."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.anthropic_client import AnthropicClient
from app.agents.codegen import CodegenAgent
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.db.models import Run
from tests.agents.codegen_fakes import (
    build_transport,
    configure_env,
    make_project_run,
    make_skeleton,
    tool_use,
)
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_first_action_is_skeleton_and_scaffold_is_not_model_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    ws = FakeWorkspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )

    # The model writes only a backend feature file, then commits — never the frontend scaffold.
    transport = build_transport(
        "Plan: add a todo feature.",
        [
            tool_use(
                "write_file",
                {"path": "backend/src/features/todo/todo.routes.ts", "content": "export {};"},
            ),
            tool_use("git_commit", {"message": "add todo feature"}),
        ],
    )

    report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx
    )

    reloaded = await Run.get(run.id)
    assert reloaded is not None
    tools_called = [c["tool"] for c in reloaded.tool_calls]
    # Step 0 is deterministic: instantiate the skeleton, then commit it — before any model tokens.
    assert tools_called[0] == "instantiate_skeleton"
    assert tools_called[1] == "git_commit"

    # The scaffold really landed in the workspace (from the deterministic copy)…
    assert "frontend/src/main.tsx" in ws.files
    assert "frontend/package.json" in ws.files
    # …but the model didn't write any of it — files_changed is only the feature file.
    assert report.files_changed == ["backend/src/features/todo/todo.routes.ts"]
    assert not any(path.startswith("frontend/") for path in report.files_changed)


async def test_existing_workspace_is_not_rescaffolded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Idempotency: re-running Build on an instantiated workspace skips the skeleton copy."""
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    ws = FakeWorkspace()
    # Pretend the workspace was already instantiated by a prior build.
    await ws.write(project, "package.json", '{"name":"forgeflow-app"}')
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )

    transport = build_transport(
        "Plan: extend the feature.",
        [tool_use("write_file", {"path": "backend/src/features/x/x.ts", "content": "export {};"})],
    )
    report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx
    )

    reloaded = await Run.get(run.id)
    assert reloaded is not None
    assert "instantiate_skeleton" not in [c["tool"] for c in reloaded.tool_calls]
    assert any("incrementally" in note for note in report.notes)
