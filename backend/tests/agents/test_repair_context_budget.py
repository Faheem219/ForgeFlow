"""Budgeting (phase-29 task 2): an oversized context is trimmed by failure frequency and what was
dropped is always recorded — a trimmed context is never silently lossy."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.repair_context import RepairContextAnalyzer
from app.core.config import reset_config
from tests.agents.repair_fakes import (
    FakeContextWorkspace,
    configure_blobs,
    failing_result,
    make_project,
    make_test_run,
)

pytestmark = pytest.mark.usefixtures("mongo_db")

HOT = "backend/src/features/todos/todos.controller.ts"  # implicated by BOTH failures
COLD = "backend/src/features/todos/todos.model.ts"  # implicated by one, and large


async def test_lowest_priority_file_is_dropped_and_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    monkeypatch.setenv("REPAIR_CONTEXT_MAX_CHARS", "1000")
    reset_config()

    workspace = FakeContextWorkspace(
        {HOT: "small source", COLD: "x" * 5000},
        last_passing=None,  # no diff, so the budget is spent on files alone
    )
    project = await make_project()
    run = await make_test_run(
        project,
        [
            failing_result("a", refs=[HOT], message="boom"),
            failing_result("b", refs=[HOT, COLD], message="boom"),
        ],
    )

    context = await RepairContextAnalyzer(workspace).analyze(project, run)

    # The file both failures implicate wins; the big single-reference one is dropped.
    assert context.file_paths() == [HOT]
    assert any(COLD in entry and "dropped" in entry for entry in context.trimmed)
    assert context.size() <= 1000


async def test_oversized_file_is_truncated_and_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    monkeypatch.setenv("REPAIR_CONTEXT_MAX_FILE_CHARS", "100")
    reset_config()

    workspace = FakeContextWorkspace({HOT: "y" * 900}, last_passing=None)
    project = await make_project()
    run = await make_test_run(project, [failing_result("a", refs=[HOT])])

    context = await RepairContextAnalyzer(workspace).analyze(project, run)

    assert context.file_paths() == [HOT]
    entry = context.target_files[0]
    assert entry.truncated is True
    assert len(entry.content) < 900
    assert any(HOT in note and "truncated" in note for note in context.trimmed)


async def test_generous_budget_keeps_everything_untrimmed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    workspace = FakeContextWorkspace({HOT: "small", COLD: "also small"}, last_passing=None)
    project = await make_project()
    run = await make_test_run(
        project, [failing_result("a", refs=[HOT]), failing_result("b", refs=[HOT, COLD])]
    )

    context = await RepairContextAnalyzer(workspace).analyze(project, run)

    assert context.file_paths() == [HOT, COLD]  # frequency order, nothing dropped
    assert context.trimmed == []
