"""Hard prereq (phase-37, §8): deploy is blocked with a clear message unless a passing build
exists — the only mandatory ordering the state machine enforces (deploy⇐build)."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.errors import UserError
from app.db.models import Deployment
from app.db.models.enums import DeployMode, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.deploy.orchestrate import DeployOrchestrator
from app.deploy.providers.base import DeployTarget
from app.projects.service import ProjectService
from tests.deploy.orchestrate_fakes import (
    FakeBackendSource,
    FakeDeployProvider,
    FakeFrontendBuilder,
    RecordingEmitter,
    factory_of,
    full_plan,
    static_plan_source,
)

pytestmark = pytest.mark.usefixtures("mongo_db", "fernet_key", "blob_env", "deployable_db")


def _orchestrator() -> DeployOrchestrator:
    fe = FakeDeployProvider("vercel", DeployTarget.fe, "https://web")
    be = FakeDeployProvider("render", DeployTarget.be, "https://api")
    return DeployOrchestrator(
        plan_source=static_plan_source(full_plan()),
        frontend_builder=FakeFrontendBuilder(),
        backend_source=FakeBackendSource(),
        provider_factory=factory_of(fe, be),
        emitter=RecordingEmitter(),
    )


async def test_deploy_without_a_build_is_blocked() -> None:
    project = await ProjectService().create_project(PydanticObjectId(), "app")  # build still empty
    assert project.id is not None

    with pytest.raises(UserError, match="build"):
        await _orchestrator().deploy(project, mode=DeployMode.seamless)

    # Nothing was deployed or recorded.
    assert await Deployment.find({"project_id": project.id}).to_list() == []


async def test_deploy_proceeds_once_the_build_is_complete() -> None:
    project = await ProjectService().create_project(PydanticObjectId(), "app")
    assert project.id is not None
    await StageStateRepo().set_status(project.id, Stage.build, StageStatus.complete)

    deployment = await _orchestrator().deploy(project, mode=DeployMode.seamless)
    assert deployment.status == "live"
