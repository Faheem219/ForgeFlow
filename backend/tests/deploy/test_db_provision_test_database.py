"""The test stage gets its own database, on the same route as the preview (D8).

Before this, the test stage injected no ``MONGODB_URI`` at all, so the skeleton's config fell back
to ``mongodb://localhost:27017/forgeflow_app`` — which, inside a sandbox on an internal network, is
the sandbox itself. Any suite that really touched Mongo hung until mongoose's buffering timeout.

The obvious repair — hand tests the same URI the preview uses — would have been worse: generated
backend suites routinely clear collections between cases, so a green test run would have wiped
whatever the user had built up in their preview. Hence a sibling database.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.db.models import Project
from app.db.models.enums import CredentialKind, CredentialScope
from app.deploy.db_provision import DbProvisioner, db_name_for_test_stage
from app.deploy.secrets import SecretVault

pytestmark = pytest.mark.usefixtures("mongo_db", "fernet_key")

_SANDBOX_BASE = "mongodb://forgeflow-appdb:27017"


async def _project(db_name: str = "forgeflow_app_abc") -> Project:
    return await Project(user_id=PydanticObjectId(), name="p", app_db_name=db_name).insert()


async def _project_with_byo(uri: str) -> Project:
    user_id = PydanticObjectId()
    project = await Project(user_id=user_id, name="p", app_db_name="forgeflow_app_abc").insert()
    await SecretVault().put_credential(user_id, CredentialKind.mongo_uri, uri, CredentialScope.byo)
    return project


@pytest.fixture
def sandbox_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """How a sandbox addresses the shared app-data cluster (the preview's route)."""
    monkeypatch.setenv("APP_DB_SANDBOX_URI", _SANDBOX_BASE)
    reset_config()


async def test_tests_never_share_the_database_the_preview_is_serving(sandbox_route: None) -> None:
    """The property the whole design exists for: a test run cannot destroy the user's app data."""
    project = await _project()
    prov = DbProvisioner()

    app_uri = await prov.get_sandbox_mongodb_uri(project)
    test_uri = await prov.get_sandbox_test_mongodb_uri(project)

    assert app_uri != test_uri
    assert app_uri.endswith("/forgeflow_app_abc")
    assert test_uri.endswith("/forgeflow_app_abc_test")


async def test_the_test_database_uses_the_sandbox_route(sandbox_route: None) -> None:
    """Same cluster, same container-addressable host — only the database name differs."""
    project = await _project()

    uri = await DbProvisioner().get_sandbox_test_mongodb_uri(project)

    assert uri == f"{_SANDBOX_BASE}/forgeflow_app_abc_test"


async def test_it_falls_back_to_the_cluster_uri_when_no_sandbox_route_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A control plane running inside docker needs no separate sandbox address."""
    monkeypatch.setenv("APP_DB_SANDBOX_URI", "")
    monkeypatch.setenv("APP_DB_CLUSTER_URI", "mongodb+srv://svc:pw@apps.example.net")
    reset_config()
    project = await _project()

    uri = await DbProvisioner().get_sandbox_test_mongodb_uri(project)

    assert uri == "mongodb+srv://svc:pw@apps.example.net/forgeflow_app_abc_test"


async def test_a_byo_database_is_redirected_too(sandbox_route: None) -> None:
    """ForgeFlow never manages a BYO database — which is exactly why tests must not wipe one."""
    project = await _project_with_byo("mongodb+srv://u:pw@byo.example.net/mydata?retryWrites=true")

    uri = await DbProvisioner().get_sandbox_test_mongodb_uri(project)

    assert uri == "mongodb+srv://u:pw@byo.example.net/mydata_test?retryWrites=true"


async def test_a_byo_uri_pinning_no_database_still_gets_one(sandbox_route: None) -> None:
    project = await _project_with_byo("mongodb+srv://u:pw@byo.example.net")

    uri = await DbProvisioner().get_sandbox_test_mongodb_uri(project)

    assert uri == "mongodb+srv://u:pw@byo.example.net/forgeflow_app_abc_test"


def test_the_test_database_name_is_a_sibling() -> None:
    assert db_name_for_test_stage("forgeflow_app_abc") == "forgeflow_app_abc_test"
