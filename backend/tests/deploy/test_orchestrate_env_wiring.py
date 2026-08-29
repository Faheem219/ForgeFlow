"""Env wiring (phase-37): the backend gets the DB URI, and the frontend is *built and deployed* with
the backend's URL — the two directions that make a deployed app actually talk to itself."""

from __future__ import annotations

import pytest

from app.db.models.enums import DeployMode
from app.deploy.db_provision import DbProvisioner
from app.deploy.orchestrate import DeployOrchestrator
from app.deploy.providers.base import DeployTarget
from tests.deploy.orchestrate_fakes import (
    FakeBackendSource,
    FakeDeployProvider,
    FakeFrontendBuilder,
    RecordingEmitter,
    factory_of,
    full_plan,
    project_with_build,
    static_plan_source,
)

pytestmark = pytest.mark.usefixtures("mongo_db", "fernet_key", "blob_env", "deployable_db")

_BE_URL = "https://api.onrender.com"
_FE_URL = "https://web.vercel.app"


async def test_backend_gets_the_db_uri_and_frontend_gets_the_backend_url() -> None:
    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("render", DeployTarget.be, _BE_URL)
    builder = FakeFrontendBuilder()
    orch = DeployOrchestrator(
        plan_source=static_plan_source(full_plan()),
        frontend_builder=builder,
        backend_source=FakeBackendSource(),
        provider_factory=factory_of(fe, be),
        emitter=RecordingEmitter(),
    )
    project = await project_with_build()
    expected_uri = await DbProvisioner().get_app_mongodb_uri(project)

    await orch.deploy(project, mode=DeployMode.seamless)

    # BE ← DB: the isolated MONGODB_URI is injected into the Render service env.
    be_env = be.deploy_specs[0].env
    assert be_env["MONGODB_URI"] == expected_uri
    assert be_env["NODE_ENV"] == "production"
    assert expected_uri.endswith(f"/{project.app_db_name}")

    # FE ← BE: VITE_API_BASE_URL is present at BUILD time (baked into the SPA)…
    assert builder.calls[0]["env"]["VITE_API_BASE_URL"] == _BE_URL
    # …and also on the Vercel deploy env.
    assert fe.deploy_specs[0].env["VITE_API_BASE_URL"] == _BE_URL
    # The FE ships the built bundle, not source.
    assert "index.html" in (fe.deploy_specs[0].files or {})


async def test_frontend_is_built_after_the_backend_is_live() -> None:
    """The FE build must see the live BE URL, so it cannot start until the BE deploy returns."""
    order: list[str] = []

    fe = FakeDeployProvider("vercel", DeployTarget.fe, _FE_URL)
    be = FakeDeployProvider("render", DeployTarget.be, _BE_URL)

    class OrderedBuilder(FakeFrontendBuilder):
        async def __call__(self, project, fe_plan, build_env):  # type: ignore[no-untyped-def]
            order.append("fe-build")
            return await super().__call__(project, fe_plan, build_env)

    real_be_deploy = be.deploy

    async def tracked_be_deploy(spec, *, mode, user_id=None):  # type: ignore[no-untyped-def]
        order.append("be-deploy")
        return await real_be_deploy(spec, mode=mode, user_id=user_id)

    be.deploy = tracked_be_deploy  # type: ignore[method-assign]

    orch = DeployOrchestrator(
        plan_source=static_plan_source(full_plan()),
        frontend_builder=OrderedBuilder(),
        backend_source=FakeBackendSource(),
        provider_factory=factory_of(fe, be),
        emitter=RecordingEmitter(),
    )
    await orch.deploy(await project_with_build(), mode=DeployMode.seamless)

    assert order == ["be-deploy", "fe-build"]
