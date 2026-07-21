"""Isolation (phase-41) — the security-critical invariant of Epic 9.

A project must reach **its own database and nothing else**: not another project's, and never
``forgeflow_meta``. These run against real MongoDB, because the claim being tested is about where a
query actually lands, which a fake cannot demonstrate.

The strongest guarantee is structural rather than defensive: there is no parameter anywhere in the
service or the router that names a database, so a request has no way to ask for one. The tests below
verify both that structural property and the belt-and-braces guards behind it.
"""

from __future__ import annotations

import inspect

import pytest

from app.core.errors import ForbiddenError
from app.data_browser.router import (
    create_document,
    delete_document,
    get_document,
    list_collections,
    list_documents,
    update_document,
)
from app.data_browser.service import DataBrowserService
from app.deploy.db_provision import DbProvisioner
from tests.data_browser.conftest import make_project

pytestmark = pytest.mark.usefixtures("mongo_db")

_DB_WORDS = ("db_name", "database", "db", "uri", "connection")


def test_no_endpoint_accepts_a_database_name() -> None:
    """The structural guarantee: a client cannot even express "use that database"."""
    for endpoint in (
        list_collections,
        list_documents,
        create_document,
        get_document,
        update_document,
        delete_document,
    ):
        params = set(inspect.signature(endpoint).parameters)
        assert not (params & set(_DB_WORDS)), f"{endpoint.__name__} exposes a database parameter"


def test_no_service_method_accepts_a_database_name() -> None:
    for name, method in inspect.getmembers(DataBrowserService, inspect.isfunction):
        if name.startswith("_"):
            continue
        params = set(inspect.signature(method).parameters)
        assert not (params & set(_DB_WORDS)), f"{name} exposes a database parameter"


async def test_two_projects_cannot_see_each_other_s_data(cleanup_app_dbs: list[str]) -> None:
    alice = await make_project("alice")
    bob = await make_project("bob")
    cleanup_app_dbs.extend([alice.app_db_name, bob.app_db_name])
    service = DataBrowserService()

    await service.insert(alice, "todos", {"title": "alice's secret"})
    await service.insert(bob, "todos", {"title": "bob's secret"})

    alice_docs = (await service.find(alice, "todos")).documents
    bob_docs = (await service.find(bob, "todos")).documents

    assert [d["title"] for d in alice_docs] == ["alice's secret"]
    assert [d["title"] for d in bob_docs] == ["bob's secret"]


async def test_a_projects_collections_are_its_own(cleanup_app_dbs: list[str]) -> None:
    alice = await make_project("alice")
    bob = await make_project("bob")
    cleanup_app_dbs.extend([alice.app_db_name, bob.app_db_name])
    service = DataBrowserService()

    await service.insert(alice, "invoices", {"n": 1})
    await service.insert(bob, "customers", {"n": 2})

    assert [c.name for c in await service.list_collections(alice)] == ["invoices"]
    assert [c.name for c in await service.list_collections(bob)] == ["customers"]


async def test_the_control_plane_database_is_refused(
    monkeypatch: pytest.MonkeyPatch, cleanup_app_dbs: list[str]
) -> None:
    """Local dev runs the app data and the control plane on one server — so this really matters."""
    from app.core.config import reset_config

    project = await make_project("meta-probe")
    cleanup_app_dbs.append(project.app_db_name)

    # Simulate the misconfiguration: the project's app DB *is* the control-plane DB.
    monkeypatch.setenv("FORGEFLOW_META_DB", project.app_db_name)
    reset_config()

    with pytest.raises(ForbiddenError, match="control-plane"):
        await DataBrowserService().list_collections(project)


async def test_a_mismatched_platform_database_is_refused(cleanup_app_dbs: list[str]) -> None:
    """If a URI ever resolved somewhere else, the ownership guard stops it before any read."""
    project = await make_project("guarded")
    cleanup_app_dbs.append(project.app_db_name)
    other = await make_project("other")

    with pytest.raises(ForbiddenError, match="own database"):
        DbProvisioner().assert_owns_db(project, other.app_db_name)


async def test_system_collections_are_never_listed(cleanup_app_dbs: list[str]) -> None:
    """MongoDB's own namespace would leak database internals."""
    project = await make_project("sys")
    cleanup_app_dbs.append(project.app_db_name)
    service = DataBrowserService()
    await service.insert(project, "todos", {"title": "t"})

    names = [c.name for c in await service.list_collections(project)]
    assert not any(n.startswith("system.") for n in names)


async def test_a_system_collection_cannot_be_queried_directly(cleanup_app_dbs: list[str]) -> None:
    from app.core.errors import UserError

    project = await make_project("sysquery")
    cleanup_app_dbs.append(project.app_db_name)

    with pytest.raises(UserError, match="System collections"):
        await DataBrowserService().find(project, "system.indexes")
