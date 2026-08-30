"""Non-linearity (phase-23, D12): codegen tolerates any subset of upstream artifacts — design-only,
requirements-only, neither, or stale — and records a clear note in the build report."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.anthropic_client import AnthropicClient
from app.agents.codegen import BuildReport, CodegenAgent
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.db.models import Project, RequirementSpec, Run
from app.db.models.enums import Stage, StageStatus
from app.db.models.requirement import Feature
from app.db.repos import StageStateRepo
from app.design.base import DesignResult
from app.design.service import DesignService
from tests.agents.codegen_fakes import (
    build_transport,
    configure_env,
    make_project_run,
    make_skeleton,
    tool_use,
)
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")


async def _seed_design(project: Project) -> None:
    assert project.id is not None
    await DesignService().save_result(
        project.id,
        DesignResult(provider="fake", external_ref="", html="<div>Hi</div>", css=".x{}"),
        source="text",
    )


async def _seed_requirements(project: Project) -> None:
    assert project.id is not None
    await RequirementSpec(project_id=project.id, features=[Feature(name="Todos")]).insert()


async def _run_build(project: Project, run: Run, tmp_path: Path) -> BuildReport:
    ctx = ToolContext.build(
        project, run, workspace=FakeWorkspace(), exec_service=FakeExec(), preview=FakePreview()
    )
    transport = build_transport(
        "Plan: minimal feature.",
        [tool_use("write_file", {"path": "backend/src/features/x/x.ts", "content": "export {};"})],
    )
    return await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx
    )


async def test_design_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    await _seed_design(project)

    report = await _run_build(project, run, tmp_path)

    assert any(
        "requirements" in note.lower() for note in report.notes
    )  # missing requirements noted
    assert not any("design" in note.lower() for note in report.notes)  # design present
    assert report.features_built == []  # no structured features to name


async def test_requirements_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    await _seed_requirements(project)

    report = await _run_build(project, run, tmp_path)

    assert any("design" in note.lower() for note in report.notes)  # missing design noted
    assert report.features_built == ["Todos"]


async def test_neither_upstream_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()

    report = await _run_build(project, run, tmp_path)

    joined = " ".join(report.notes).lower()
    assert "design" in joined and "requirements" in joined
    # Tolerated: the build still completes and produces a report.
    assert report.boot_status  # a status is always reported


async def test_stale_upstream_is_used_with_a_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)

    project, run = await make_project_run()
    assert project.id is not None
    await _seed_design(project)
    await _seed_requirements(project)

    # Mark the design stage stale (as an upstream refine would).
    stages = StageStateRepo()
    await stages.get_or_create(project.id, Stage.design)
    await stages.set_status(project.id, Stage.design, StageStatus.stale)

    report = await _run_build(project, run, tmp_path)

    assert any("stale" in note.lower() for note in report.notes)
    assert report.features_built == ["Todos"]  # stale design didn't drop the requirements
