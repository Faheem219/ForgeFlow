"""A database the deployed app cannot reach must stop the deploy *before* anything is billed.

`APP_DB_CLUSTER_URI` defaults to the control plane's own Mongo, which on a laptop is
`mongodb://localhost:27017`. A Vercel function handed that boots cleanly and then times out every
query — mongoose's "buffering timed out after 10000ms" — which reads as a code bug and sends the
user into the repair loop chasing nothing. The guard is string inspection only: no DNS, no
connection attempt, because it runs on the deploy path.
"""

from __future__ import annotations

import pytest

from app.core.config import reset_config
from app.core.errors import UserError
from app.db.models.enums import CredentialKind, CredentialScope, DeployMode
from app.deploy.db_provision import unreachable_from_internet
from app.deploy.orchestrate import DeployOrchestrator
from app.deploy.providers.base import DeployTarget
from app.deploy.secrets import SecretVault
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

pytestmark = pytest.mark.usefixtures("mongo_db", "fernet_key", "blob_env")

_ATLAS = "mongodb+srv://user:pw@cluster0.abcde.mongodb.net"


# ------------------------------------------------------------------ the pure classifier


@pytest.mark.parametrize(
    "uri",
    [
        "mongodb://localhost:27017",
        "mongodb://127.0.0.1:27017",
        "mongodb://user:pw@localhost:27017/db",
        "mongodb://[::1]:27017",
        "mongodb://host.docker.internal:27017",
    ],
)
def test_local_addresses_are_unreachable(uri: str) -> None:
    assert unreachable_from_internet(uri) is not None


def test_a_bare_docker_service_name_is_unreachable() -> None:
    """Correct for the *sandbox* (APP_DB_SANDBOX_URI), never for a deployed function — which is
    exactly why the two settings get mixed up."""
    reason = unreachable_from_internet("mongodb://forgeflow-appdb:27017")
    assert reason is not None and "forgeflow-appdb" in reason


@pytest.mark.parametrize(
    "uri",
    [
        _ATLAS,
        "mongodb+srv://cluster0.abcde.mongodb.net/app?retryWrites=true",
        "mongodb://db1.example.com:27017,db2.example.com:27017/app",
    ],
)
def test_public_addresses_pass(uri: str) -> None:
    assert unreachable_from_internet(uri) is None


def test_one_local_host_in_a_replica_set_is_enough_to_refuse() -> None:
    assert unreachable_from_internet("mongodb://db1.example.com:27017,localhost:27017") is not None


# ------------------------------------------------------------------ the deploy path


def _orchestrator(fe: FakeDeployProvider, be: FakeDeployProvider) -> DeployOrchestrator:
    return DeployOrchestrator(
        plan_source=static_plan_source(full_plan()),
        frontend_builder=FakeFrontendBuilder(),
        backend_source=FakeBackendSource(),
        provider_factory=factory_of(fe, be),
        emitter=RecordingEmitter(),
    )


async def test_a_local_cluster_refuses_before_any_provider_is_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: no billed deployment is created for a database that could never work."""
    monkeypatch.setenv("APP_DB_CLUSTER_URI", "mongodb://localhost:27017")
    reset_config()
    fe = FakeDeployProvider("vercel", DeployTarget.fe, "https://web.vercel.app")
    be = FakeDeployProvider("vercel", DeployTarget.be, "https://api.vercel.app")

    with pytest.raises(UserError, match="APP_DB_CLUSTER_URI"):
        await _orchestrator(fe, be).deploy(await project_with_build(), mode=DeployMode.seamless)

    assert be.deploy_specs == []
    assert fe.deploy_specs == []


async def test_the_message_points_at_the_right_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sandbox URI is *supposed* to be local, so the error has to say which one to change."""
    monkeypatch.setenv("APP_DB_CLUSTER_URI", "mongodb://forgeflow-appdb:27017")
    reset_config()
    fe = FakeDeployProvider("vercel", DeployTarget.fe, "https://web.vercel.app")
    be = FakeDeployProvider("vercel", DeployTarget.be, "https://api.vercel.app")

    with pytest.raises(UserError) as excinfo:
        await _orchestrator(fe, be).deploy(await project_with_build(), mode=DeployMode.seamless)

    message = str(excinfo.value)
    assert "APP_DB_SANDBOX_URI stays local" in message


async def test_an_atlas_cluster_deploys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DB_CLUSTER_URI", _ATLAS)
    reset_config()
    fe = FakeDeployProvider("vercel", DeployTarget.fe, "https://web.vercel.app")
    be = FakeDeployProvider("vercel", DeployTarget.be, "https://api.vercel.app")

    deployment = await _orchestrator(fe, be).deploy(
        await project_with_build(), mode=DeployMode.seamless
    )

    assert deployment.status == "live"


async def test_a_byo_uri_is_exempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The user supplied it and it is theirs to get right — it may be reachable in ways a string
    check cannot see (a tunnel, a private peering, a hosts entry on the platform)."""
    monkeypatch.setenv("APP_DB_CLUSTER_URI", "mongodb://localhost:27017")
    reset_config()
    project = await project_with_build()
    await SecretVault().put_credential(
        project.user_id,
        CredentialKind.mongo_uri,
        "mongodb://localhost:27017/mine",
        CredentialScope.byo,
    )
    fe = FakeDeployProvider("vercel", DeployTarget.fe, "https://web.vercel.app")
    be = FakeDeployProvider("vercel", DeployTarget.be, "https://api.vercel.app")

    deployment = await _orchestrator(fe, be).deploy(project, mode=DeployMode.seamless)

    assert deployment.status == "live"
