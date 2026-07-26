"""A build that runs out of tool turns must still hand back everything it built.

Observed in a real run: `UserError: Agent tool loop exceeded 16 turns without finishing` failed the
whole Build stage, so files that were written *and committed* had no report, no summary and no
resume hint — the user saw only a failed stage. The bound stays; the reporting changes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.agents.anthropic_client import (
    AnthropicClient,
    MessageRequest,
    StreamEvent,
    TextDelta,
    ToolUse,
    TurnComplete,
)
from app.agents.codegen import BuildReport, CodegenAgent
from app.agents.cost import Usage
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.core.config import reset_config
from tests.agents.codegen_fakes import configure_env, make_project_run, make_skeleton
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")


class NeverFinishes:
    """Plans once, then writes a new file every turn and never says it is done."""

    def __init__(self) -> None:
        self.turns = 0

    async def stream(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        index = self.turns
        self.turns += 1
        if index == 0:  # the plan call (no tools)
            yield TextDelta("Plan: build the app.")
            yield TurnComplete(text="Plan: build the app.", usage=Usage(50, 20))
            return
        path = f"backend/src/features/todo/step{index}.ts"
        yield TextDelta(f"writing {path}")
        yield TurnComplete(
            text=f"writing {path}",
            tool_uses=[
                ToolUse(id=f"t{index}", name="write_file", input={"path": path, "content": "x"})
            ],
            usage=Usage(200, 60),
            stop_reason="tool_use",
        )


async def _build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, turns: str = "4"
) -> tuple[BuildReport, NeverFinishes]:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)
    monkeypatch.setenv("ANTHROPIC_MAX_TOOL_TURNS", turns)
    reset_config()

    project, run = await make_project_run()
    ctx = ToolContext.build(
        project, run, workspace=FakeWorkspace(), exec_service=FakeExec(), preview=FakePreview()
    )
    transport = NeverFinishes()
    report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx
    )
    return report, transport


async def test_an_unfinished_build_still_reports_what_it_wrote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report, transport = await _build(monkeypatch, tmp_path)

    # It did not raise…
    assert report is not None
    # …the files it wrote are in the report (they are on disk and committed either way)…
    assert any("step" in path for path in report.files_changed)
    # …and it says plainly that it is not done, with the way forward.
    assert "did not finish" in report.follow_ups[0]
    assert "Run Build again" in report.follow_ups[0]
    assert any("unfinished" in note.lower() for note in report.notes)
    assert report.summary  # never an empty summary — the UI shows this line
    assert transport.turns > 1


async def test_the_unfinished_report_carries_the_plan_for_the_next_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The next build reads the previous report; an unfinished one must still be complete enough."""
    report, _ = await _build(monkeypatch, tmp_path)

    assert report.plan  # what it set out to do
    assert report.boot_status  # …and how far it got
    assert report.features_built is not None


async def test_a_finished_build_carries_no_unfinished_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The note must be conditional — a normal build must not be labelled incomplete."""
    from tests.agents.codegen_fakes import build_transport, tool_use

    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)
    reset_config()

    project, run = await make_project_run()
    ctx = ToolContext.build(
        project, run, workspace=FakeWorkspace(), exec_service=FakeExec(), preview=FakePreview()
    )
    transport = build_transport(
        "Plan: ship it.",
        [tool_use("write_file", {"path": "backend/src/features/todo/todo.ts", "content": "x"})],
        final_text="All done.",
    )

    report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx
    )

    assert all("did not finish" not in follow_up for follow_up in report.follow_ups)
    assert all("unfinished" not in note.lower() for note in report.notes)
    assert report.summary == "All done."
