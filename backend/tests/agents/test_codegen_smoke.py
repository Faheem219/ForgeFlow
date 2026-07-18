"""Codegen smoke (phase-23): from design + requirements the loop instantiates the skeleton, writes
feature files, boots the preview, runs tests, and emits a structured build report."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.anthropic_client import AnthropicClient
from app.agents.codegen import CodegenAgent
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.db.models import RequirementSpec, Run
from app.db.models.enums import ArtifactType, CriterionKind, Stage
from app.db.models.requirement import AcceptanceCriterion, Feature
from app.design.base import DesignResult
from app.design.service import DesignService
from app.orchestrator.artifacts import ArtifactService
from tests.agents.codegen_fakes import (
    build_transport,
    configure_env,
    make_project_run,
    make_skeleton,
    tool_use,
)
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_full_build_reports_healthy_and_persists_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    assert project.id is not None

    # Seed both upstream artifacts.
    await DesignService().save_result(
        project.id,
        DesignResult(provider="fake", external_ref="", html="<div>Hi</div>", css=".x{}"),
        source="text",
    )
    await RequirementSpec(
        project_id=project.id,
        features=[
            Feature(
                name="Todos",
                acceptance_criteria=[
                    AcceptanceCriterion(id="ac-1", text="can add a todo", kind=CriterionKind.unit)
                ],
            )
        ],
    ).insert()

    ws, exec_, preview = FakeWorkspace(), FakeExec(), FakePreview()
    ctx = ToolContext.build(project, run, workspace=ws, exec_service=exec_, preview=preview)

    transport = build_transport(
        "Plan: Todos CRUD (BE routes + FE page).",
        [
            tool_use(
                "write_file",
                {"path": "backend/src/features/todo/todo.routes.ts", "content": "export {};"},
            ),
            tool_use(
                "write_file",
                {"path": "frontend/src/features/todo/TodoPage.tsx", "content": "export {};"},
            ),
            tool_use("install_deps", {"manager": "pnpm", "packages": ["nanoid"]}),
            tool_use("start_preview", {}),
            tool_use("run_tests", {"scope": "all"}),
            tool_use("git_commit", {"message": "todos"}),
        ],
        final_text="Built the Todos feature.",
    )

    report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx
    )

    # Boot verified healthy, tests passed, feature captured, only feature files changed.
    assert report.boot_status == "healthy"
    assert report.tests_passed is True
    assert report.features_built == ["Todos"]
    assert set(report.files_changed) == {
        "backend/src/features/todo/todo.routes.ts",
        "frontend/src/features/todo/TodoPage.tsx",
    }
    assert report.summary == "Built the Todos feature."
    assert report.commit is not None
    assert report.follow_ups == []  # healthy + passing → nothing outstanding
    assert report.notes == []  # both upstream artifacts present and fresh

    # The FE-calls-BE path was exercised via the booted preview.
    assert preview.started == 1

    # Persisted as a build_report code_change artifact.
    latest = await ArtifactService().get_latest(project.id, Stage.build, ArtifactType.code_change)
    assert latest is not None
    assert latest.meta.get("kind") == "build_report"
    assert latest.meta.get("boot_status") == "healthy"

    # Skeleton-first is visible in the trace; costs accrued across plan + implement turns.
    reloaded = await Run.get(run.id)
    assert reloaded is not None
    assert [c["tool"] for c in reloaded.tool_calls][0] == "instantiate_skeleton"
    assert reloaded.cost.tokens > 0
    assert reloaded.cost.inr > 0
