"""The bounded escape hatch (phase-63): the agent may name ONE more file — within limits.

The reported failure was an agent that correctly said *"I cannot fix this from `main.tsx`; the fix
has to live in the component that renders the form"* and then, having no way to reach that
component, wrote nothing. The import walk resolves that deterministically for the common case;
`request_file` covers the residue — a subject that is registered rather than imported.

What must stay true while it does: the oracle is never granted, the grant is capped and recorded,
and nothing outside the workspace becomes writable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from beanie import PydanticObjectId

from app.agents.anthropic_client import AnthropicClient
from app.agents.repair import RepairAgent
from app.agents.tools.context import ToolContext
from app.core.config import reset_config
from app.db.blobs import get_blob_store
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

SPEC = "frontend/src/App.test.tsx"
SHELL = "frontend/src/main.tsx"  # in context, and useless — the file the report was handed
FORM = "frontend/src/components/TodoForm.tsx"  # where the fix actually belongs
OTHER = "frontend/src/components/Nav.tsx"
FAILING = "renders one label for the new-task input"


async def _setup(
    project: Project, workspace: FakeRepairWorkspace
) -> tuple[Run, ToolContext, TestRun]:
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair").insert()
    ctx = ToolContext.build(
        project, run, workspace=workspace, exec_service=FakeExec(), preview=FakePreview()
    )
    red = await make_run(
        project,
        # The stack names the shell and the spec — the shape that leaves the agent stuck.
        [result(FAILING, TestStatus.failed, file=SPEC, refs=[SHELL, SPEC])],
    )
    return run, ctx, red


def _files() -> dict[str, str]:
    return {
        SPEC: "render(<HomePage />)",
        SHELL: "ReactDOM.createRoot(root).render(<RouterProvider router={router} />)",
        FORM: "<form aria-label='Add a new task'>",
        OTHER: "<nav />",
    }


async def test_a_requested_source_file_is_returned_and_becomes_writable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    workspace = FakeRepairWorkspace(_files())
    run, ctx, red = await _setup(project, workspace)
    green = await make_run(project, [result(FAILING, TestStatus.passed, file=SPEC)])

    transport = build_repair_transport(
        [
            tool_use("request_file", {"path": FORM, "reason": "it renders the form and the label"}),
            tool_use("write_file", {"path": FORM, "content": "<form>"}),
        ]
    )
    out = await RepairAgent(
        AnthropicClient(transport), runner=ScriptedRunner([green]), workspace=workspace
    ).run(project, run, red, ctx=ctx)

    assert out.requested_files == [FORM]
    assert out.files_written == [FORM]  # the grant made it writable, not merely readable
    assert workspace.files[FORM] == "<form>"
    assert out.blocked_writes == []

    # The grant is auditable: the persisted context records the file it widened to.
    reloaded = await TestRun.get(red.id)
    assert reloaded is not None and reloaded.repair_context_ref
    stored = json.loads((await get_blob_store().get(reloaded.repair_context_ref)).decode())
    assert FORM in [f["path"] for f in stored["target_files"]]
    assert any("Agent requested" in note for note in stored["notes"])


async def test_the_oracle_is_never_granted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    workspace = FakeRepairWorkspace(_files())
    run, ctx, red = await _setup(project, workspace)
    after = await make_run(project, [result(FAILING, TestStatus.failed, file=SPEC, refs=[SPEC])])

    transport = build_repair_transport(
        [
            tool_use("request_file", {"path": SPEC, "reason": "the assertion is wrong"}),
            tool_use("write_file", {"path": SPEC, "content": "expect(true).toBe(true)"}),
        ]
    )
    out = await RepairAgent(
        AnthropicClient(transport), runner=ScriptedRunner([after]), workspace=workspace
    ).run(project, run, red, ctx=ctx)

    assert out.requested_files == []
    assert out.files_written == []
    assert SPEC in out.blocked_writes
    assert workspace.files[SPEC] == "render(<HomePage />)"  # the test is untouched


async def test_a_path_that_is_not_in_the_workspace_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    workspace = FakeRepairWorkspace(_files())
    run, ctx, red = await _setup(project, workspace)
    after = await make_run(project, [result(FAILING, TestStatus.failed, file=SPEC, refs=[SPEC])])

    transport = build_repair_transport(
        [tool_use("request_file", {"path": "frontend/src/components/Guess.tsx", "reason": "hunch"})]
    )
    out = await RepairAgent(
        AnthropicClient(transport), runner=ScriptedRunner([after]), workspace=workspace
    ).run(project, run, red, ctx=ctx)

    assert out.requested_files == []
    assert out.files_written == []


async def test_the_grant_is_capped_per_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    monkeypatch.setenv("REPAIR_REQUEST_FILE_MAX", "1")
    reset_config()
    project = await make_project()
    workspace = FakeRepairWorkspace(_files())
    run, ctx, red = await _setup(project, workspace)
    after = await make_run(project, [result(FAILING, TestStatus.failed, file=SPEC, refs=[SPEC])])

    transport = build_repair_transport(
        [
            tool_use("request_file", {"path": FORM, "reason": "the form"}),
            tool_use("request_file", {"path": OTHER, "reason": "and this one too"}),
            tool_use("write_file", {"path": OTHER, "content": "SABOTAGE"}),
        ]
    )
    out = await RepairAgent(
        AnthropicClient(transport), runner=ScriptedRunner([after]), workspace=workspace
    ).run(project, run, red, ctx=ctx)

    assert out.requested_files == [FORM]  # the second request was over budget
    assert OTHER in out.blocked_writes
    assert workspace.files[OTHER] == "<nav />"


async def test_the_hatch_can_be_switched_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The rollback path in the phase plan: 0 removes the tool from the surface entirely."""
    configure_blobs(monkeypatch, tmp_path)
    monkeypatch.setenv("REPAIR_REQUEST_FILE_MAX", "0")
    reset_config()
    project = await make_project()
    workspace = FakeRepairWorkspace(_files())
    run, ctx, red = await _setup(project, workspace)
    after = await make_run(project, [result(FAILING, TestStatus.failed, file=SPEC, refs=[SPEC])])

    transport = build_repair_transport(
        [tool_use("request_file", {"path": FORM, "reason": "the form"})]
    )
    out = await RepairAgent(
        AnthropicClient(transport), runner=ScriptedRunner([after]), workspace=workspace
    ).run(project, run, red, ctx=ctx)

    assert out.requested_files == []
    offered = {tool["name"] for request in transport.requests for tool in request.tools}
    assert "request_file" not in offered
