"""Minimality is enforced, not merely requested (phase-30): the agent can only write inside the
repair context's editable set, and the tests — the oracle — are never writable."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from beanie import PydanticObjectId

from app.agents.anthropic_client import AnthropicClient
from app.agents.repair import RepairAgent, is_test_file
from app.agents.tools.context import ToolContext
from app.db.models import Project, Run, TestRun
from app.testing.models import TestStatus
from tests.agents.repair_agent_fakes import (
    FakeRepairWorkspace,
    ScriptedRunner,
    build_repair_transport,
    make_project,
    make_run,
    result,
    tool_use,
)
from tests.agents.repair_fakes import configure_blobs
from tests.agents.tools.conftest import FakeExec, FakePreview

pytestmark = pytest.mark.usefixtures("mongo_db")

SRC = "backend/src/features/todos/todos.controller.ts"
TEST = "backend/src/features/todos/todos.test.ts"
UNRELATED = "backend/src/features/billing/invoices.service.ts"


async def _setup(
    project: Project, workspace: FakeRepairWorkspace
) -> tuple[Run, ToolContext, TestRun]:
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair").insert()
    ctx = ToolContext.build(
        project, run, workspace=workspace, exec_service=FakeExec(), preview=FakePreview()
    )
    red = await make_run(
        project,
        [
            result(
                "todos [ac-empty] rejects empty",
                TestStatus.failed,
                file=TEST,
                criterion_id="ac-empty",
                refs=[SRC, TEST],
            )
        ],
    )
    return run, ctx, red


async def test_writes_outside_the_context_are_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    workspace = FakeRepairWorkspace(
        {SRC: "buggy", TEST: "tests", UNRELATED: "untouched billing logic"}
    )
    run, ctx, red = await _setup(project, workspace)
    after = await make_run(
        project, [result("todos [ac-empty] rejects empty", TestStatus.passed, file=TEST)]
    )

    transport = build_repair_transport(
        [
            tool_use("write_file", {"path": UNRELATED, "content": "SABOTAGE"}),
            tool_use("write_file", {"path": SRC, "content": "fixed"}),
        ]
    )
    out = await RepairAgent(
        AnthropicClient(transport), runner=ScriptedRunner([after]), workspace=workspace
    ).run(project, run, red, ctx=ctx)

    # The out-of-context write was refused and recorded; the file is untouched.
    assert out.blocked_writes == [UNRELATED]
    assert workspace.files[UNRELATED] == "untouched billing logic"
    # The in-context write still went through.
    assert out.files_written == [SRC]
    assert workspace.files[SRC] == "fixed"


async def test_the_test_oracle_is_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The classic failure mode — weakening the test to force green — is structurally impossible."""
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    workspace = FakeRepairWorkspace({SRC: "buggy", TEST: "it('[ac-empty] rejects empty', …)"})
    run, ctx, red = await _setup(project, workspace)
    after = await make_run(
        project, [result("todos [ac-empty] rejects empty", TestStatus.passed, file=TEST)]
    )

    transport = build_repair_transport(
        [tool_use("write_file", {"path": TEST, "content": "it.skip('rejects empty', …)"})]
    )
    out = await RepairAgent(
        AnthropicClient(transport), runner=ScriptedRunner([after]), workspace=workspace
    ).run(project, run, red, ctx=ctx)

    assert out.blocked_writes == [TEST]
    assert workspace.files[TEST] == "it('[ac-empty] rejects empty', …)"  # oracle intact
    assert out.files_written == []


async def test_a_context_with_no_editable_source_escalates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only test files implicated → nothing safe to patch, so hand it to a human (phase-31)."""
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    workspace = FakeRepairWorkspace({TEST: "tests"})
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair").insert()
    ctx = ToolContext.build(
        project, run, workspace=workspace, exec_service=FakeExec(), preview=FakePreview()
    )
    red = await make_run(project, [result("t", TestStatus.failed, file=TEST, refs=[TEST])])

    out = await RepairAgent(
        AnthropicClient(build_repair_transport([])),
        runner=ScriptedRunner([red]),
        workspace=workspace,
    ).run(project, run, red, ctx=ctx)

    assert out.attempt is None
    assert out.note is not None and "human" in out.note.lower()
    assert workspace.commits == []


def test_refusal_payload_explains_why() -> None:
    """The refusal is a normal tool result, so the model can self-correct rather than crash."""
    assert is_test_file(TEST) is True
    assert is_test_file("frontend/e2e/todos.spec.ts") is True
    assert is_test_file(SRC) is False
    assert is_test_file("frontend/src/features/todos/TodoPage.tsx") is False


def test_blocked_write_result_is_a_structured_tool_error() -> None:
    from app.agents.tools.registry import tool_err

    payload = json.loads(tool_err("Refusing to write x: it is a test file.", editable=[SRC]))
    assert payload["ok"] is False
    assert "Refusing" in payload["error"]
    assert payload["editable"] == [SRC]
