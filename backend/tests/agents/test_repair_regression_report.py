"""Regression awareness (phase-30 task 2): a patch that fixes one test but breaks another reports
``newly_failing`` — the guard the loop controller (phase-31) stops on."""

from __future__ import annotations

from pathlib import Path

import pytest
from beanie import PydanticObjectId

from app.agents.anthropic_client import AnthropicClient
from app.agents.repair import RepairAgent, compare_runs
from app.agents.tools.context import ToolContext
from app.db.models import Run
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
TARGET = "todos [ac-empty] rejects an empty title"
NEIGHBOUR = "todos [ac-add] adds a todo"


async def test_a_patch_that_breaks_a_passing_test_is_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()

    before = await make_run(
        project,
        [
            result(NEIGHBOUR, TestStatus.passed, file=TEST),
            result(TARGET, TestStatus.failed, file=TEST, criterion_id="ac-empty", refs=[SRC, TEST]),
        ],
    )
    # The patch fixes the target but breaks its neighbour — a classic over-broad repair.
    after = await make_run(
        project,
        [
            result(NEIGHBOUR, TestStatus.failed, file=TEST, refs=[SRC]),
            result(TARGET, TestStatus.passed, file=TEST),
        ],
    )

    workspace = FakeRepairWorkspace({SRC: "buggy", TEST: "tests"})
    run = await Run(project_id=project.id or PydanticObjectId(), kind="repair").insert()
    ctx = ToolContext.build(
        project, run, workspace=workspace, exec_service=FakeExec(), preview=FakePreview()
    )
    transport = build_repair_transport(
        [tool_use("write_file", {"path": SRC, "content": "over-broad fix"})]
    )

    out = await RepairAgent(
        AnthropicClient(transport), runner=ScriptedRunner([after]), workspace=workspace
    ).run(project, run, before, ctx=ctx)

    assert out.newly_failing == [f"{TEST}::{NEIGHBOUR}"]  # the regression is surfaced
    assert out.newly_passing == [f"{TEST}::{TARGET}"]  # the intended fix still counted
    assert out.green is False  # a run with a regression is not green


def test_compare_runs_classifies_every_transition() -> None:
    before = [
        result("stays green", TestStatus.passed, file="a.ts"),
        result("gets fixed", TestStatus.failed, file="a.ts"),
        result("stays red", TestStatus.failed, file="a.ts"),
        result("breaks", TestStatus.passed, file="a.ts"),
    ]
    after = [
        result("stays green", TestStatus.passed, file="a.ts"),
        result("gets fixed", TestStatus.passed, file="a.ts"),
        result("stays red", TestStatus.failed, file="a.ts"),
        result("breaks", TestStatus.failed, file="a.ts"),
    ]

    delta = compare_runs(before, after)

    assert delta.newly_passing == ["a.ts::gets fixed"]
    assert delta.newly_failing == ["a.ts::breaks"]
    assert delta.still_failing == ["a.ts::stays red"]


def test_a_brand_new_failing_test_is_not_counted_as_a_regression() -> None:
    """Adding coverage must never look like breakage: a regression is 'was passing, now failing'."""
    before = [result("existing", TestStatus.passed, file="a.ts")]
    after = [
        result("existing", TestStatus.passed, file="a.ts"),
        result("brand new", TestStatus.failed, file="a.ts"),
    ]

    delta = compare_runs(before, after)

    assert delta.newly_failing == []
    assert delta.still_failing == []


def test_same_test_name_in_different_files_is_not_conflated() -> None:
    before = [result("renders", TestStatus.passed, file="a.tsx")]
    after = [result("renders", TestStatus.failed, file="b.tsx")]

    delta = compare_runs(before, after)

    # b.tsx::renders has no prior status, so it is new — not a regression of a.tsx::renders.
    assert delta.newly_failing == []
