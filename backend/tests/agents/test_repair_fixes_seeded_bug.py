"""A seeded bug is patched from the minimal context, committed, re-run (affected → full), and the
attempt is recorded (phase-30, §9 steps 2–3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from beanie import PydanticObjectId

from app.agents.anthropic_client import AnthropicClient
from app.agents.repair import RepairAgent
from app.agents.tools.context import ToolContext
from app.db.blobs import get_blob_store
from app.db.models import Project, RepairAttempt, Run
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
FAILING = "todos [ac-empty] rejects an empty title"
FAILING_KEY = f"{TEST}::{FAILING}"

BUGGY = "export const create = (req, res) => res.status(201).json(req.body)"
FIXED = (
    "export const create = (req, res) => {\n"
    "  if (!req.body.title) return res.status(400).json({ error: 'title required' })\n"
    "  return res.status(201).json(req.body)\n"
    "}"
)


async def _agent_run(project: Project) -> Run:
    """A persisted agent Run to carry the attempt's cost."""
    return await Run(project_id=project.id or PydanticObjectId(), kind="repair").insert()


def _ctx(project: Project, run: Run, workspace: FakeRepairWorkspace) -> ToolContext:
    return ToolContext.build(
        project, run, workspace=workspace, exec_service=FakeExec(), preview=FakePreview()
    )


async def test_seeded_bug_is_fixed_without_regressions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()

    red = await make_run(
        project,
        [
            result("todos [ac-add] adds a todo", TestStatus.passed, file=TEST),
            result(
                FAILING, TestStatus.failed, file=TEST, criterion_id="ac-empty", refs=[SRC, TEST]
            ),
        ],
    )
    green = await make_run(
        project,
        [
            result("todos [ac-add] adds a todo", TestStatus.passed, file=TEST),
            result(FAILING, TestStatus.passed, file=TEST),
        ],
    )

    workspace = FakeRepairWorkspace({SRC: BUGGY, TEST: "it('[ac-empty] rejects empty', …)"})
    runner = ScriptedRunner([green])
    run = await _agent_run(project)
    transport = build_repair_transport([tool_use("write_file", {"path": SRC, "content": FIXED})])

    out = await RepairAgent(AnthropicClient(transport), runner=runner, workspace=workspace).run(
        project, run, red, ctx=_ctx(project, run, workspace), iteration=1
    )

    # The failing test went green, with no regressions.
    assert out.green is True
    assert out.newly_passing == [FAILING_KEY]
    assert out.newly_failing == []
    assert out.still_failing == []

    # The patch landed in the source (never the test) and was committed.
    assert workspace.files[SRC] == FIXED
    assert out.files_written == [SRC]
    assert out.commit is not None
    assert workspace.commits and workspace.commits[0].startswith("repair: attempt 1")

    # Affected suite first (a unit-only failure), then the authoritative full run.
    assert runner.scopes == ["unit", "all"]

    # Auditable: diff blob + resulting run; the outcome is left for the controller.
    attempt = await RepairAttempt.find({"project_id": project.id}).first_or_none()
    assert attempt is not None
    assert attempt.iteration == 1
    assert attempt.target_files == [SRC]
    assert attempt.resulting_run_id == green.id
    assert attempt.outcome is None  # phase-31 judges this
    assert attempt.diff_ref is not None
    assert (await get_blob_store().get(attempt.diff_ref)).decode("utf-8") == out.diff


async def test_a_green_run_is_a_no_op(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    passing = await make_run(project, [result("all good", TestStatus.passed, file=TEST)])

    workspace = FakeRepairWorkspace({SRC: BUGGY})
    run = await _agent_run(project)
    out = await RepairAgent(
        AnthropicClient(build_repair_transport([])),
        runner=ScriptedRunner([passing]),
        workspace=workspace,
    ).run(project, run, passing, ctx=_ctx(project, run, workspace))

    assert out.attempt is None and out.note is not None
    assert "nothing to repair" in out.note.lower()
    assert workspace.commits == []  # nothing was touched


async def test_e2e_failure_uses_the_e2e_fast_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    page = "frontend/src/features/todos/TodoPage.tsx"
    spec = "frontend/e2e/todos.spec.ts"

    red = await make_run(
        project,
        [
            result(
                "[ac-ui] adds via the UI",
                TestStatus.failed,
                file=spec,
                framework="playwright",
                refs=[page, spec],
            )
        ],
    )
    green = await make_run(
        project, [result("[ac-ui] adds via the UI", TestStatus.passed, file=spec)]
    )

    workspace = FakeRepairWorkspace({page: "export const TodoPage = () => null", spec: "test(…)"})
    runner = ScriptedRunner([green])
    run = await _agent_run(project)
    transport = build_repair_transport([tool_use("write_file", {"path": page, "content": "fixed"})])

    await RepairAgent(AnthropicClient(transport), runner=runner, workspace=workspace).run(
        project, run, red, ctx=_ctx(project, run, workspace)
    )

    assert runner.scopes == ["e2e", "all"]
