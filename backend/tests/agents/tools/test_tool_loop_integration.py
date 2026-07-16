"""Acceptance: an agent scaffolds, modifies, installs, runs, tests, previews, and commits — purely
through tools, driven by a scripted mock model over the phase-20 client (phase-21)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.anthropic_client import AnthropicClient, ToolUse, TurnComplete
from app.agents.cost import Usage
from app.agents.models import TaskKind
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.agents.tools.registry import make_tool_dispatch
from app.core.config import reset_config
from app.db.models import Project, Run
from tests.agents.test_client_tool_loop import FakeTransport, ScriptedTurn
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db", "blob_env")


def _make_skeleton(root: Path) -> None:
    (root / "frontend").mkdir(parents=True)
    (root / "frontend" / "package.json").write_text('{"name":"fe"}', encoding="utf-8")


def _tool_use(name: str, args: dict[str, object]) -> ToolUse:
    return ToolUse(id=f"tu_{name}", name=name, input=args)


async def test_agent_builds_entirely_through_tools(
    project_run: tuple[Project, Run], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skeleton = tmp_path / "skeleton"
    _make_skeleton(skeleton)
    monkeypatch.setenv("APP_SKELETON_DIR", str(skeleton))
    reset_config()

    project, run = project_run
    fake_ws = FakeWorkspace()
    fake_exec = FakeExec()
    fake_preview = FakePreview()
    ctx = ToolContext.build(
        project, run, workspace=fake_ws, exec_service=fake_exec, preview=fake_preview
    )
    registry = default_registry()
    dispatch = make_tool_dispatch(registry, ctx)

    # One turn requesting the whole build sequence, then a final turn.
    transport = FakeTransport(
        [
            ScriptedTurn(
                deltas=["Building…"],
                turn=TurnComplete(
                    text="Building…",
                    tool_uses=[
                        _tool_use("instantiate_skeleton", {}),
                        _tool_use("write_file", {"path": "src/app.ts", "content": "export {};"}),
                        _tool_use("install_deps", {"manager": "pnpm", "packages": ["zod"]}),
                        _tool_use("run_command", {"cmd": ["pnpm", "build"]}),
                        _tool_use("run_tests", {"scope": "all"}),
                        _tool_use("start_preview", {}),
                        _tool_use("git_commit", {"message": "scaffold + app"}),
                    ],
                    usage=Usage(200, 40),
                    stop_reason="tool_use",
                ),
            ),
            ScriptedTurn(deltas=["Done."], turn=TurnComplete(text="Done.", usage=Usage(30, 5))),
        ]
    )

    result = await AnthropicClient(transport).run_tool_loop(
        task_kind=TaskKind.codegen,
        project_id=project.id,  # type: ignore[arg-type]
        run=run,
        messages=[{"role": "user", "content": "build the app"}],
        tools=registry.anthropic_tools(),
        tool_dispatch=dispatch,
    )

    assert result.text == "Done."

    # Scaffolded + modified files are really in the sandbox workspace.
    assert (await fake_ws.read(project, "frontend/package.json")).content == '{"name":"fe"}'
    assert (await fake_ws.read(project, "src/app.ts")).content == "export {};"
    # Committed.
    assert (await fake_ws.git_info(project)).current_sha is not None

    # Exec + preview happened through the tools.
    assert [c["cmd"] for c in fake_exec.calls] == [
        ["pnpm", "add", "zod"],
        ["pnpm", "build"],
        ["pnpm", "test"],
    ]
    assert fake_preview.started == 1

    # Every tool call is on the Run trace, in order, all successful.
    reloaded = await Run.get(run.id)
    assert reloaded is not None
    assert [c["tool"] for c in reloaded.tool_calls] == [
        "instantiate_skeleton",
        "write_file",
        "install_deps",
        "run_command",
        "run_tests",
        "start_preview",
        "git_commit",
    ]
    assert all(c["ok"] for c in reloaded.tool_calls)
