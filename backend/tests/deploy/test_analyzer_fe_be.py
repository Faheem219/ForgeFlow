"""FE + BE: a static frontend plus a persistent server, with no database (phase-33)."""

from __future__ import annotations

import json

import pytest

from app.deploy.analyzer import TARGET_BE, TARGET_FE, InfraAnalyzer
from tests.deploy.conftest import (
    FakeAnalyzerWorkspace,
    be_package,
    fe_be_files,
    make_project,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_fe_be_plan_splits_static_from_persistent(fe_be: FakeAnalyzerWorkspace) -> None:
    project = await make_project()

    plan = await InfraAnalyzer(fe_be).analyze(project, persist=False)

    # The frontend is static hosting…
    assert plan.fe is not None
    assert plan.fe.type == "static" and plan.fe.target == TARGET_FE
    assert plan.fe.dir == "frontend"
    assert plan.fe.output_dir == "dist"

    # …while the server needs a process that stays up (the D11 reason BE never goes to Vercel).
    assert plan.be is not None
    assert plan.be.type == "persistent" and plan.be.target == TARGET_BE
    assert plan.be.dir == "backend"
    assert plan.be.start_cmd == "pnpm start"
    assert plan.be.build_cmd == "pnpm build"
    assert plan.be.port == 3001  # read from `process.env.PORT ?? 3001`

    assert plan.db is None
    assert plan.confidence == "high"


async def test_backend_without_a_start_script_falls_back_to_dev() -> None:
    files = fe_be_files(mongoose=False)
    files["backend/package.json"] = be_package(mongoose=False, start=False)
    project = await make_project()

    plan = await InfraAnalyzer(FakeAnalyzerWorkspace(files)).analyze(project, persist=False)

    assert plan.be is not None and plan.be.start_cmd == "pnpm dev"
    assert any("no `start` script" in n.lower() for n in plan.notes)


async def test_backend_without_a_build_script_is_flagged() -> None:
    files = fe_be_files(mongoose=False)
    files["backend/package.json"] = be_package(mongoose=False, build=False)
    project = await make_project()

    plan = await InfraAnalyzer(FakeAnalyzerWorkspace(files)).analyze(project, persist=False)

    assert plan.be is not None and plan.be.build_cmd is None
    assert any("no build script" in w for w in plan.warnings)
    assert plan.confidence == "medium"  # detected, but needs confirmation


async def test_a_non_default_port_is_detected() -> None:
    files = fe_be_files(mongoose=False)
    files["backend/src/config.ts"] = "const port = Number(process.env.PORT ?? 8080)"
    project = await make_project()

    plan = await InfraAnalyzer(FakeAnalyzerWorkspace(files)).analyze(project, persist=False)

    assert plan.be is not None and plan.be.port == 8080


async def test_a_package_without_a_server_framework_is_not_a_backend() -> None:
    files = fe_be_files(mongoose=False)
    files["backend/package.json"] = json.dumps(
        {"name": "backend", "scripts": {"build": "tsc"}, "dependencies": {"zod": "^3.23.8"}}
    )
    project = await make_project()

    plan = await InfraAnalyzer(FakeAnalyzerWorkspace(files)).analyze(project, persist=False)

    assert plan.be is None  # a library package is not a deployable service
    assert plan.fe is not None
