"""Pressing **Repair** on an ordinary assertion failure actually changes code.

The bug this pins down, end-to-end through the exact analyzer the endpoint wires
(``app/testing/repair_router.py``): a Jest/Vitest assertion failure's stack names only its own
spec, the spec is read-only during repair, so the editable set came back empty and the loop
escalated with ``blocked`` on iteration 1 — no patch, no commit, no code changed. The user-visible
symptom was a Repair button that ran and fixed nothing.

Note what the failures below carry: ``refs=[TEST]`` only. The pre-existing agent tests supply
``refs=[SRC, TEST]``, which hands the analyzer the answer and is why this never showed up there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from beanie import PydanticObjectId

from app.agents.anthropic_client import AnthropicClient
from app.agents.repair import RepairAgent
from app.agents.repair_context import RepairContextAnalyzer
from app.agents.suite_context import SuiteRepairContextAnalyzer
from app.agents.tools.context import ToolContext
from app.db.models import Run
from app.orchestrator.stages.repair import LOOP_ESCALATED, LOOP_FIXED, RepairLoopController
from app.sandbox.schemas import FileNode
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

TEST = "backend/src/features/todos/todos.test.ts"
SRC = "backend/src/features/todos/todos.controller.ts"
FAILING = "todos [ac-empty] rejects an empty title"

BUGGY = "export const create = (req, res) => res.status(201).json(req.body)"
FIXED = (
    "export const create = (req, res) => {\n"
    "  if (!req.body.title) return res.status(400).json({ error: 'title required' })\n"
    "  return res.status(201).json(req.body)\n"
    "}"
)


class _Workspace(FakeRepairWorkspace):
    """The shared repair fake plus the two reads the widening analyzer needs."""

    async def list_paths(self) -> list[FileNode]:
        return [FileNode(path=p, type="file", size=len(c)) for p, c in sorted(self.files.items())]

    async def tree(self, project: Any, path: str = ".", depth: int | None = None) -> list[FileNode]:
        return await self.list_paths()

    async def changed_paths(self, project: Any, sha_a: str, sha_b: str) -> list[str]:
        return []


def _assertion_only_failure() -> Any:
    """A failure whose stack names the spec and nothing else — the real-world shape."""
    return result(FAILING, TestStatus.failed, file=TEST, criterion_id="ac-empty", refs=[TEST])


async def _loop(
    workspace: _Workspace, *, analyzer: Any, green_run: Any, project: Any, run: Run
) -> Any:
    transport = build_repair_transport([tool_use("write_file", {"path": SRC, "content": FIXED})])
    agent = RepairAgent(
        AnthropicClient(transport), runner=ScriptedRunner([green_run]), workspace=workspace
    )
    ctx = ToolContext.build(
        project, run, workspace=workspace, exec_service=FakeExec(), preview=FakePreview()
    )
    return await RepairLoopController(agent=agent, analyzer=analyzer).run(
        project, await make_run(project, [_assertion_only_failure()]), run=run, ctx=ctx
    )


async def test_the_repair_button_patches_the_source_for_an_assertion_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    workspace = _Workspace({SRC: BUGGY, TEST: "it('[ac-empty] rejects empty', …)"})
    green = await make_run(project, [result(FAILING, TestStatus.passed, file=TEST)])

    outcome = await _loop(
        workspace,
        analyzer=SuiteRepairContextAnalyzer(
            RepairContextAnalyzer(workspace=cast(Any, workspace)), workspace=cast(Any, workspace)
        ),
        green_run=green,
        project=project,
        run=run,
    )

    # The whole point: code actually changed, and the failing test went green.
    assert workspace.files[SRC] == FIXED
    assert outcome.outcome == LOOP_FIXED
    assert outcome.metrics.iterations == 1
    assert [a.target_files for a in outcome.attempts] == [[SRC]]
    # The oracle was never touched — a green run still cannot be faked.
    assert workspace.files[TEST] == "it('[ac-empty] rejects empty', …)"


async def test_without_widening_the_same_click_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The regression guard: the bare analyzer is exactly the behaviour that was reported.

    If this ever starts passing with a patch applied, the widening tiers have been bypassed and
    the Repair button has silently gone back to doing nothing.
    """
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair:loop").insert()
    workspace = _Workspace({SRC: BUGGY, TEST: "it('[ac-empty] rejects empty', …)"})
    green = await make_run(project, [result(FAILING, TestStatus.passed, file=TEST)])

    outcome = await _loop(
        workspace,
        analyzer=RepairContextAnalyzer(workspace=cast(Any, workspace)),
        green_run=green,
        project=project,
        run=run,
    )

    assert workspace.files[SRC] == BUGGY  # nothing was patched
    assert outcome.outcome == LOOP_ESCALATED
    assert outcome.metrics.iterations == 0  # escalated before spending a single iteration
