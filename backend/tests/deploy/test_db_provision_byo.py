"""BYO DB provisioning (phase-36, D8): a user-supplied MONGODB_URI is validated, used verbatim, and
**never** lifecycle-managed — ForgeFlow does not create or drop a database it doesn't own."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.errors import UserError
from app.db.models import Project
from app.db.models.enums import CredentialKind, CredentialScope
from app.deploy.db_provision import DbMode, DbProvisioner
from app.deploy.secrets import SecretVault

pytestmark = pytest.mark.usefixtures("mongo_db", "fernet_key")

_BYO_URI = "mongodb+srv://user:secret@byo.example.net/mydata?retryWrites=true"


async def _project_with_byo(uri: str) -> Project:
    user_id = PydanticObjectId()
    project = await Project(
        user_id=user_id, name="p", app_db_name="forgeflow_app_platform"
    ).insert()
    await SecretVault().put_credential(user_id, CredentialKind.mongo_uri, uri, CredentialScope.byo)
    return project


async def test_byo_uri_is_used_verbatim() -> None:
    project = await _project_with_byo(_BYO_URI)
    uri = await DbProvisioner().get_app_mongodb_uri(project)
    # Used exactly as supplied — the platform app_db_name is NOT appended to a BYO URI.
    assert uri == _BYO_URI
    assert "forgeflow_app_platform" not in uri


async def test_byo_info_is_unmanaged() -> None:
    project = await _project_with_byo(_BYO_URI)
    info = await DbProvisioner().info(project)
    assert info.mode is DbMode.byo
    assert info.db_name == "mydata"  # the database pinned in the BYO URI
    assert info.managed is False  # ForgeFlow never manages a BYO database


async def test_malformed_byo_uri_is_rejected() -> None:
    project = await _project_with_byo("postgres://nope")
    with pytest.raises(UserError):
        await DbProvisioner().get_app_mongodb_uri(project)


async def test_teardown_never_drops_a_byo_database() -> None:
    project = await _project_with_byo(_BYO_URI)
    dropped: list[tuple[str, str]] = []

    async def recording_drop(uri: str, db_name: str) -> None:
        dropped.append((uri, db_name))

    prov = DbProvisioner(dropper=recording_drop)
    # Even with the destructive opt-in, a BYO database is left untouched.
    assert await prov.teardown(project, drop_data=True) is False
    assert dropped == []
