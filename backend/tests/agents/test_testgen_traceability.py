"""Traceability (phase-27, D6/§9): every acceptance criterion yields ≥1 test tagged with its id,
and the persisted ``TestSuite`` per kind carries the exact criterion ids it was generated from —
the join key the repair loop (phase-29) relies on."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.anthropic_client import AnthropicClient
from app.agents.testgen import (
    STATUS_GENERATED,
    TESTGEN_REPORT_KIND,
    TestGenAgent,
    TestGenReport,
)
from app.agents.tools.context import ToolContext
from app.db.models import Project, Run
from app.db.models.enums import ArtifactType, CriterionKind, Stage, TestKind
from app.db.repos import TestSuiteRepo
from app.orchestrator.artifacts import ArtifactService
from tests.agents.codegen_fakes import configure_env, make_project_run, make_skeleton, tool_use
from tests.agents.testgen_fakes import (
    build_testgen_transport,
    e2e_test_content,
    feature,
    seed_spec,
    unit_test_content,
)
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")

_UNIT_FILE = "backend/src/features/todos/todos.test.ts"
_E2E_FILE = "frontend/e2e/todos.spec.ts"


async def _seed_two_feature_spec(project: Project) -> None:
    assert project.id is not None
    await seed_spec(
        project.id,
        [
            feature(
                "Todos",
                [
                    ("ac-add", "adding a todo persists it", CriterionKind.unit),
                    ("ac-list", "the list shows todos", CriterionKind.e2e),
                ],
            ),
            feature(
                "Validation",
                [("ac-empty", "an empty title is rejected", CriterionKind.either)],
            ),
        ],
    )


async def _run(project: Project, run: Run) -> TestGenReport:
    ws = FakeWorkspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    # The scripted author writes one unit file (ac-add + the `either` ac-empty) and one e2e file
    # (ac-list), each carrying the exact criterion ids, then commits.
    transport = build_testgen_transport(
        "Plan: unit for ac-add/ac-empty, e2e for ac-list.",
        [
            tool_use(
                "write_file",
                {"path": _UNIT_FILE, "content": unit_test_content(["ac-add", "ac-empty"])},
            ),
            tool_use("write_file", {"path": _E2E_FILE, "content": e2e_test_content(["ac-list"])}),
            tool_use("git_commit", {"message": "tests"}),
        ],
    )
    return await TestGenAgent(AnthropicClient(transport)).run(project, run, ctx=ctx)


async def test_every_criterion_gets_a_tagged_test(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    await _seed_two_feature_spec(project)

    report = await _run(project, run)

    assert report.status == STATUS_GENERATED
    # Every acceptance criterion is covered by ≥1 test; none left uncovered.
    assert sorted(report.criteria_covered) == ["ac-add", "ac-empty", "ac-list"]
    assert report.criteria_uncovered == []
    assert report.warning is None


async def test_suites_persist_with_exact_criterion_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    assert project.id is not None
    await _seed_two_feature_spec(project)

    report = await _run(project, run)

    # Report groups files by kind with the criterion ids each kind was generated from.
    by_kind = {s["kind"]: s for s in report.suites}
    assert by_kind[str(TestKind.unit)]["files"] == [_UNIT_FILE]
    assert sorted(by_kind[str(TestKind.unit)]["generated_from"]) == ["ac-add", "ac-empty"]
    assert by_kind[str(TestKind.e2e)]["files"] == [_E2E_FILE]
    assert by_kind[str(TestKind.e2e)]["generated_from"] == ["ac-list"]

    # A versioned TestSuite per kind is persisted with the same join keys.
    suites = await TestSuiteRepo().list_for_project(project.id)
    persisted = {s.kind: s for s in suites}
    assert persisted[TestKind.unit].files == [_UNIT_FILE]
    assert sorted(persisted[TestKind.unit].generated_from) == ["ac-add", "ac-empty"]
    assert persisted[TestKind.e2e].generated_from == ["ac-list"]
    assert persisted[TestKind.unit].version == 1

    # A `test` report artifact surfaces the run in the stage's artifact list.
    artifact = await ArtifactService().get_latest(project.id, Stage.test, ArtifactType.test)
    assert artifact is not None
    assert artifact.meta["kind"] == TESTGEN_REPORT_KIND
    assert artifact.meta["inferred"] is False


async def test_rerun_bumps_suite_version(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    assert project.id is not None
    await _seed_two_feature_spec(project)

    await _run(project, run)
    await _run(
        project, run
    )  # a second generation appends a new version per kind (never overwrites)

    suites = await TestSuiteRepo().list_for_project(project.id)
    unit_versions = sorted(s.version for s in suites if s.kind == TestKind.unit)
    assert unit_versions == [1, 2]
