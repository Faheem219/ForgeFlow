"""Minimality (phase-29, §9): the context carries only the failing test, the files it exercises,
the diff, and the matching acceptance text — irrelevant files are excluded."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.repair_context import RepairContextAnalyzer
from app.db.blobs import get_blob_store
from app.db.models import RequirementSpec, TestRun
from app.db.models.enums import CriterionKind
from app.db.models.requirement import AcceptanceCriterion, Feature
from tests.agents.repair_fakes import (
    FakeContextWorkspace,
    configure_blobs,
    failing_result,
    make_project,
    make_test_run,
    passing_result,
)

pytestmark = pytest.mark.usefixtures("mongo_db")

TEST_FILE = "backend/src/features/todos/todos.test.ts"
SOURCE_FILE = "backend/src/features/todos/todos.controller.ts"
UNRELATED = "backend/src/features/billing/invoices.service.ts"


def _workspace() -> FakeContextWorkspace:
    return FakeContextWorkspace(
        {
            TEST_FILE: "it('[ac-empty] rejects an empty title', () => {…})",
            SOURCE_FILE: "export function create(req, res) {…}",
            UNRELATED: "x" * 5000,  # a big file nothing in the failure mentions
        },
        diff_text="--- a/todos.controller.ts\n+++ b/todos.controller.ts\n",
    )


async def test_context_holds_only_the_implicated_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    run = await make_test_run(
        project,
        [
            passing_result("todos [ac-add] adds a todo", file=TEST_FILE),
            failing_result(
                "todos [ac-empty] rejects an empty title",
                criterion_id="ac-empty",
                file=TEST_FILE,
                refs=[SOURCE_FILE, TEST_FILE],
                stack=f"at Object.<anonymous> ({SOURCE_FILE}:22:18)",
            ),
        ],
    )

    context = await RepairContextAnalyzer(_workspace()).analyze(project, run)

    # Only the failing test is carried — the passing one is irrelevant to the repair.
    assert [t.name for t in context.failing_tests] == ["todos [ac-empty] rejects an empty title"]
    failing = context.failing_tests[0]
    assert failing.criterion_id == "ac-empty"
    assert failing.assertion == "todos [ac-empty] rejects an empty title"
    assert failing.stack is not None and SOURCE_FILE in failing.stack

    # Exactly the exercised files — the unrelated (and much larger) file is absent.
    assert set(context.file_paths()) == {SOURCE_FILE, TEST_FILE}
    assert UNRELATED not in context.file_paths()
    assert all(UNRELATED not in f.content for f in context.target_files)


async def test_requirement_snippets_join_on_criterion_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    assert project.id is not None
    await RequirementSpec(
        project_id=project.id,
        features=[
            Feature(
                name="Todos",
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="ac-empty", text="rejects an empty title", kind=CriterionKind.unit
                    ),
                    AcceptanceCriterion(
                        id="ac-other", text="unrelated criterion", kind=CriterionKind.unit
                    ),
                ],
            )
        ],
    ).insert()
    run = await make_test_run(
        project,
        [failing_result("t", criterion_id="ac-empty", file=TEST_FILE, refs=[SOURCE_FILE])],
    )

    context = await RepairContextAnalyzer(_workspace()).analyze(project, run)

    # Only the failing criterion's acceptance text — traceability without dragging in the spec.
    assert [s.criterion_id for s in context.requirement_snippets] == ["ac-empty"]
    assert context.requirement_snippets[0].text == "rejects an empty title"
    assert context.requirement_snippets[0].feature == "Todos"


async def test_context_is_persisted_and_linked_to_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    run = await make_test_run(project, [failing_result("t", file=TEST_FILE, refs=[SOURCE_FILE])])

    context = await RepairContextAnalyzer(_workspace()).analyze(project, run)

    assert context.ref is not None
    reloaded = await TestRun.get(run.id)
    assert reloaded is not None and reloaded.repair_context_ref == context.ref

    stored = json.loads((await get_blob_store().get(context.ref)).decode("utf-8"))
    assert [f["path"] for f in stored["target_files"]] == context.file_paths()
    assert stored["diff"] == context.diff


async def test_a_green_run_yields_an_empty_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    run = await make_test_run(project, [passing_result("all good", file=TEST_FILE)])

    context = await RepairContextAnalyzer(_workspace()).analyze(project, run)

    assert context.failing_tests == []
    assert context.target_files == []
    assert any("nothing to repair" in note.lower() for note in context.notes)


async def test_missing_referenced_file_is_noted_not_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    run = await make_test_run(
        project,
        [failing_result("t", file=TEST_FILE, refs=["backend/src/gone.ts", SOURCE_FILE])],
    )

    context = await RepairContextAnalyzer(_workspace()).analyze(project, run)

    assert set(context.file_paths()) == {SOURCE_FILE, TEST_FILE}
    assert any("gone.ts" in note for note in context.notes)
