"""The diff is anchored on the **last-passing** ref (phase-29 design note): "what changed since it
worked", scoped to the files the failures implicate."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.repair_context import RepairContextAnalyzer
from tests.agents.repair_fakes import (
    FakeContextWorkspace,
    configure_blobs,
    failing_result,
    make_project,
    make_test_run,
)

pytestmark = pytest.mark.usefixtures("mongo_db")

TEST_FILE = "backend/src/features/todos/todos.test.ts"
SOURCE_FILE = "backend/src/features/todos/todos.controller.ts"
UNRELATED = "backend/src/features/billing/invoices.service.ts"

DIFF = (
    "diff --git a/todos.controller.ts b/todos.controller.ts\n"
    "-  if (!title) return res.status(400).json({ error: 'title required' });\n"
    "+  // TODO validate\n"
)


def _files() -> dict[str, str]:
    return {TEST_FILE: "it('…')", SOURCE_FILE: "export const create = …", UNRELATED: "unrelated"}


async def test_diff_runs_from_last_passing_to_head_scoped_to_implicated_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    workspace = FakeContextWorkspace(
        _files(), current_sha="sha-head", last_passing="sha-green", diff_text=DIFF
    )
    project = await make_project()
    run = await make_test_run(
        project, [failing_result("t", file=TEST_FILE, refs=[SOURCE_FILE, TEST_FILE])]
    )

    context = await RepairContextAnalyzer(workspace).analyze(project, run)

    assert context.diff == DIFF
    assert context.base_sha == "sha-green" and context.head_sha == "sha-head"

    call = workspace.diff_calls[0]
    assert call["a"] == "sha-green" and call["b"] == "sha-head"  # since it last worked
    # Scoped to what the failures implicate — the unrelated file is never diffed.
    assert set(call["paths"]) == {SOURCE_FILE, TEST_FILE}
    assert UNRELATED not in call["paths"]


async def test_no_last_passing_ref_yields_an_empty_diff_with_a_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    workspace = FakeContextWorkspace(_files(), last_passing=None, diff_text=DIFF)
    project = await make_project()
    run = await make_test_run(project, [failing_result("t", file=TEST_FILE, refs=[SOURCE_FILE])])

    context = await RepairContextAnalyzer(workspace).analyze(project, run)

    # Never diffed at all — a first-ever red run still repairs from the failures alone.
    assert context.diff == "" and workspace.diff_calls == []
    assert any("last-passing" in note for note in context.notes)
    assert context.target_files, "files are still gathered without a diff"


async def test_head_equal_to_last_passing_skips_the_diff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    workspace = FakeContextWorkspace(
        _files(), current_sha="sha-same", last_passing="sha-same", diff_text=DIFF
    )
    project = await make_project()
    run = await make_test_run(project, [failing_result("t", file=TEST_FILE, refs=[SOURCE_FILE])])

    context = await RepairContextAnalyzer(workspace).analyze(project, run)

    assert context.diff == "" and workspace.diff_calls == []
    assert any("last-passing commit" in note for note in context.notes)


async def test_oversized_diff_is_truncated_and_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    monkeypatch.setenv("REPAIR_CONTEXT_DIFF_MAX_CHARS", "120")
    from app.core.config import reset_config

    reset_config()

    workspace = FakeContextWorkspace(_files(), diff_text="+" * 5000)
    project = await make_project()
    run = await make_test_run(project, [failing_result("t", file=TEST_FILE, refs=[SOURCE_FILE])])

    context = await RepairContextAnalyzer(workspace).analyze(project, run)

    assert len(context.diff) < 5000
    assert any("diff truncated" in entry for entry in context.trimmed)
