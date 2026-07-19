"""An FE-only project deploys as static hosting — no backend, no database (phase-33)."""

from __future__ import annotations

import pytest

from app.deploy.analyzer import TARGET_FE, InfraAnalyzer
from tests.deploy.conftest import FakeAnalyzerWorkspace, fe_only_files, make_project

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_fe_only_plan_omits_backend_and_database(fe_only: FakeAnalyzerWorkspace) -> None:
    project = await make_project()

    plan = await InfraAnalyzer(fe_only).analyze(project, persist=False)

    assert plan.fe is not None
    assert plan.fe.type == "static"
    assert plan.fe.target == TARGET_FE
    assert plan.fe.dir == ""  # a single-package app at the workspace root
    assert plan.fe.build_cmd == "pnpm build"
    assert plan.fe.output_dir == "dist"
    assert plan.fe.env == ["VITE_API_BASE_URL"]

    # Nothing persistent to run and nothing to store.
    assert plan.be is None
    assert plan.db is None
    assert plan.required_secrets == []
    assert plan.confidence == "high"
    assert plan.needs_confirmation is False


async def test_custom_vite_out_dir_is_honoured() -> None:
    files = fe_only_files()
    files["vite.config.ts"] = "export default defineConfig({ build: { outDir: 'build' } })"
    project = await make_project()

    plan = await InfraAnalyzer(FakeAnalyzerWorkspace(files)).analyze(project, persist=False)

    assert plan.fe is not None and plan.fe.output_dir == "build"


async def test_an_unrecognizable_workspace_is_flagged_not_guessed() -> None:
    """Nothing deployable found → low confidence + a flag, never a silent default."""
    workspace = FakeAnalyzerWorkspace({"README.md": "# just docs"})
    project = await make_project()

    plan = await InfraAnalyzer(workspace).analyze(project, persist=False)

    assert plan.fe is None and plan.be is None and plan.db is None
    assert plan.confidence == "low"
    assert plan.needs_confirmation is True
    assert any("No frontend detected" in w for w in plan.warnings)
