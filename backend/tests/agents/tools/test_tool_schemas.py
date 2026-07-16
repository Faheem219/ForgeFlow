"""Tool schemas: Anthropic tool list, arg validation, unknown tool, catalog (phase-21)."""

from __future__ import annotations

import json

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


def test_anthropic_tools_expose_every_tool() -> None:
    tools = default_registry().anthropic_tools()
    names = {t["name"] for t in tools}
    assert {
        "read_file",
        "write_file",
        "list_dir",
        "run_command",
        "install_deps",
        "run_tests",
        "start_preview",
        "restart_preview",
        "git_commit",
        "instantiate_skeleton",
    } <= names
    for tool in tools:
        assert tool["input_schema"]["type"] == "object"
        assert "properties" in tool["input_schema"]


async def test_missing_required_arg_is_rejected(project_run: tuple[Project, Run]) -> None:
    project, run = project_run
    result = json.loads(await default_registry().dispatch(_ctx(project, run), "read_file", {}))
    assert result["ok"] is False
    assert result["error"] == "Invalid arguments"
    assert any("path" in issue for issue in result["issues"])


async def test_wrong_type_is_rejected(project_run: tuple[Project, Run]) -> None:
    project, run = project_run
    # cmd must be a list of strings, not a string.
    result = json.loads(
        await default_registry().dispatch(_ctx(project, run), "run_command", {"cmd": "ls -la"})
    )
    assert result["ok"] is False


async def test_unknown_tool_is_rejected(project_run: tuple[Project, Run]) -> None:
    project, run = project_run
    result = json.loads(
        await default_registry().dispatch(_ctx(project, run), "delete_everything", {})
    )
    assert result["ok"] is False
    assert "read_file" in result["available"]


def test_catalog_lists_all_tools() -> None:
    catalog = default_registry().catalog_markdown()
    for name in default_registry().names():
        assert f"`{name}`" in catalog
