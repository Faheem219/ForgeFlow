"""FE + BE + DB — the full fixed-stack shape, and the versioned artifact it produces (phase-33)."""

from __future__ import annotations

import json

import pytest

from app.db.models.enums import ArtifactType, Stage
from app.deploy.analyzer import INFRA_PLAN_KIND, TARGET_DB, InfraAnalyzer
from app.orchestrator.artifacts import ArtifactService
from tests.deploy.conftest import (
    FakeAnalyzerWorkspace,
    be_package,
    fe_be_files,
    make_project,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_full_stack_plan_covers_all_three_tiers(fe_be_db: FakeAnalyzerWorkspace) -> None:
    project = await make_project()

    plan = await InfraAnalyzer(fe_be_db).analyze(project, persist=False)

    assert plan.fe is not None and plan.fe.target == "vercel"
    # phase-58: the default backend route is Vercel; DEPLOY_BE_PROVIDER=render overrides it at
    # deploy time, and the recorded Deployment carries whichever provider actually ran.
    assert plan.be is not None and plan.be.target == "vercel"
    assert plan.db is not None
    assert plan.db.type == "mongo" and plan.db.provider == TARGET_DB
    assert plan.db.env_var == "MONGODB_URI"

    # The connection string is the one thing that must come from the vault (phase-34).
    assert plan.required_secrets == ["MONGODB_URI"]
    assert plan.confidence == "high"
    assert plan.needs_confirmation is False


async def test_plan_is_persisted_as_a_versioned_artifact(fe_be_db: FakeAnalyzerWorkspace) -> None:
    project = await make_project()
    assert project.id is not None
    analyzer = InfraAnalyzer(fe_be_db)

    await analyzer.analyze(project)
    await analyzer.analyze(project)  # re-analysis versions, never overwrites

    artifacts = ArtifactService()
    versions = await artifacts.list_versions(project.id, Stage.deploy, ArtifactType.infra_plan)
    assert [a.version for a in versions] == [1, 2]

    latest = versions[-1]
    assert latest.meta["kind"] == INFRA_PLAN_KIND
    assert latest.meta["confidence"] == "high"
    assert latest.meta["targets"] == {"fe": "vercel", "be": "vercel", "db": "atlas"}
    assert latest.meta["required_secrets"] == ["MONGODB_URI"]

    body = json.loads(await artifacts.get_content(latest) or "{}")
    assert body["fe"]["output_dir"] == "dist"
    assert body["be"]["port"] == 3001
    assert body["db"]["provider"] == "atlas"


async def test_mongoose_without_a_connection_string_is_flagged() -> None:
    """Conflicting evidence is surfaced, not silently resolved (D12)."""
    files = fe_be_files(mongoose=True)
    files["backend/src/config.ts"] = "const port = Number(process.env.PORT ?? 3001)"
    files["backend/.env.example"] = "PORT=3001\n"
    files[".env.example"] = "PORT=3001\nVITE_API_BASE_URL=http://localhost:3001\n"
    project = await make_project()

    plan = await InfraAnalyzer(FakeAnalyzerWorkspace(files)).analyze(project, persist=False)

    assert plan.db is not None  # mongoose is present, so a database is still planned
    assert any("MONGODB_URI is never read" in w for w in plan.warnings)
    assert plan.confidence == "medium"


async def test_a_connection_string_without_mongoose_still_plans_a_database() -> None:
    files = fe_be_files(mongoose=False)  # no mongoose dep, but MONGODB_URI is referenced
    project = await make_project()

    plan = await InfraAnalyzer(FakeAnalyzerWorkspace(files)).analyze(project, persist=False)

    assert plan.db is not None
    assert any("without Mongoose" in n for n in plan.notes)


async def test_no_database_when_nothing_references_one() -> None:
    files = fe_be_files(mongoose=False)
    files["backend/package.json"] = be_package(mongoose=False)
    files["backend/src/config.ts"] = "const port = Number(process.env.PORT ?? 3001)"
    files["backend/.env.example"] = "PORT=3001\n"
    files[".env.example"] = "PORT=3001\nVITE_API_BASE_URL=http://localhost:3001\n"
    project = await make_project()

    plan = await InfraAnalyzer(FakeAnalyzerWorkspace(files)).analyze(project, persist=False)

    assert plan.db is None
    assert plan.required_secrets == []
