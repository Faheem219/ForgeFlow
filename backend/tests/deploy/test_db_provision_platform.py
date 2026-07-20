"""Platform DB provisioning (phase-36, D8): each project gets its own isolated database in the
shared cluster; two projects never share one, and the isolation guard blocks cross-access."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.core.errors import ForbiddenError
from app.db.models import Project
from app.deploy.db_provision import DbMode, DbProvisioner

pytestmark = pytest.mark.usefixtures("mongo_db", "fernet_key")


async def _project(db_name: str) -> Project:
    return await Project(user_id=PydanticObjectId(), name="p", app_db_name=db_name).insert()


async def test_each_project_gets_a_distinct_database() -> None:
    a = await _project("forgeflow_app_aaaa")
    b = await _project("forgeflow_app_bbbb")
    prov = DbProvisioner()

    uri_a = await prov.get_app_mongodb_uri(a)
    uri_b = await prov.get_app_mongodb_uri(b)

    # Both hang off the same shared cluster but point at their own, distinct database.
    assert uri_a.endswith("/forgeflow_app_aaaa")
    assert uri_b.endswith("/forgeflow_app_bbbb")
    assert uri_a != uri_b


async def test_info_reports_platform_mode_managed() -> None:
    project = await _project("forgeflow_app_cccc")
    info = await DbProvisioner().info(project)
    assert info.mode is DbMode.platform
    assert info.db_name == "forgeflow_app_cccc"
    assert info.managed is True  # ForgeFlow may drop a platform DB


async def test_provision_returns_platform_info() -> None:
    project = await _project("forgeflow_app_dddd")
    info = await DbProvisioner().provision(project)
    assert info.mode is DbMode.platform and info.managed is True


async def test_cluster_base_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_DB_CLUSTER_URI", "mongodb+srv://svc:pw@apps.example.net")
    reset_config()
    project = await _project("forgeflow_app_eeee")

    uri = await DbProvisioner().get_app_mongodb_uri(project)
    assert uri == "mongodb+srv://svc:pw@apps.example.net/forgeflow_app_eeee"


async def test_isolation_guard_blocks_cross_project_access() -> None:
    project = await _project("forgeflow_app_ffff")
    other = await _project("forgeflow_app_gggg")
    prov = DbProvisioner()

    prov.assert_owns_db(project, "forgeflow_app_ffff")  # its own DB — allowed
    with pytest.raises(ForbiddenError):
        prov.assert_owns_db(project, other.app_db_name)  # another project's DB — blocked


async def test_teardown_requires_the_destructive_opt_in() -> None:
    project = await _project("forgeflow_app_hhhh")
    dropped: list[tuple[str, str]] = []

    async def recording_drop(uri: str, db_name: str) -> None:
        dropped.append((uri, db_name))

    prov = DbProvisioner(dropper=recording_drop)

    # Without the opt-in, nothing is dropped (data loss is never implicit).
    assert await prov.teardown(project) is False
    assert dropped == []

    # With drop_data=True, the platform database is dropped.
    assert await prov.teardown(project, drop_data=True) is True
    assert dropped == [(await prov.get_app_mongodb_uri(project), "forgeflow_app_hhhh")]
