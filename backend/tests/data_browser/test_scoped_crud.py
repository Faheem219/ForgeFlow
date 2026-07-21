"""Scoped CRUD (phase-41): list / find / get / insert / update / delete on the project's own DB.

Run against real MongoDB — these assert that documents genuinely land in, and come back from, the
project's isolated database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.core.errors import NotFoundError
from app.data_browser.service import DataBrowserService
from tests.data_browser.conftest import make_project

pytestmark = pytest.mark.usefixtures("mongo_db")


async def _seed(service: DataBrowserService, project: object, n: int = 3) -> None:
    for i in range(n):
        await service.insert(
            project,  # type: ignore[arg-type]
            "todos",
            {"title": f"todo {i}", "done": i % 2 == 0, "order": i},
        )


async def test_insert_returns_the_stored_document(cleanup_app_dbs: list[str]) -> None:
    project = await make_project()
    cleanup_app_dbs.append(project.app_db_name)

    created = await DataBrowserService().insert(project, "todos", {"title": "write tests"})

    assert created["title"] == "write tests"
    assert isinstance(created["_id"], str)  # ObjectId is serialized for the client


async def test_collections_are_listed_with_counts(cleanup_app_dbs: list[str]) -> None:
    project = await make_project()
    cleanup_app_dbs.append(project.app_db_name)
    service = DataBrowserService()
    await _seed(service, project, 3)
    await service.insert(project, "users", {"email": "a@b.test"})

    collections = {c.name: c.count for c in await service.list_collections(project)}
    assert collections == {"todos": 3, "users": 1}


async def test_find_pages_and_reports_the_total(cleanup_app_dbs: list[str]) -> None:
    project = await make_project()
    cleanup_app_dbs.append(project.app_db_name)
    service = DataBrowserService()
    await _seed(service, project, 5)

    first = await service.find(project, "todos", limit=2, page=1)
    second = await service.find(project, "todos", limit=2, page=2)

    assert first.total == 5 and first.pages == 3
    assert len(first.documents) == 2 and len(second.documents) == 2
    assert {d["_id"] for d in first.documents}.isdisjoint({d["_id"] for d in second.documents})


async def test_find_filters_and_sorts(cleanup_app_dbs: list[str]) -> None:
    project = await make_project()
    cleanup_app_dbs.append(project.app_db_name)
    service = DataBrowserService()
    await _seed(service, project, 5)

    page = await service.find(project, "todos", filter={"done": True}, sort={"order": -1})

    assert [d["order"] for d in page.documents] == [4, 2, 0]
    assert page.total == 3


async def test_get_reads_one_document(cleanup_app_dbs: list[str]) -> None:
    project = await make_project()
    cleanup_app_dbs.append(project.app_db_name)
    service = DataBrowserService()
    created = await service.insert(project, "todos", {"title": "find me"})

    fetched = await service.get(project, "todos", created["_id"])
    assert fetched["title"] == "find me"


async def test_get_a_missing_document_is_a_404(cleanup_app_dbs: list[str]) -> None:
    project = await make_project()
    cleanup_app_dbs.append(project.app_db_name)

    with pytest.raises(NotFoundError):
        await DataBrowserService().get(project, "todos", "6a5f7982785f9dd105f8f98d")


async def test_update_changes_fields_and_leaves_the_id_alone(cleanup_app_dbs: list[str]) -> None:
    project = await make_project()
    cleanup_app_dbs.append(project.app_db_name)
    service = DataBrowserService()
    created = await service.insert(project, "todos", {"title": "old", "done": False})

    # Round-tripping the whole document (including `_id`) must work — the UI will do exactly that.
    updated = await service.update(
        project, "todos", created["_id"], {**created, "title": "new", "done": True}
    )

    assert updated["title"] == "new" and updated["done"] is True
    assert updated["_id"] == created["_id"]


async def test_delete_removes_the_document(cleanup_app_dbs: list[str]) -> None:
    project = await make_project()
    cleanup_app_dbs.append(project.app_db_name)
    service = DataBrowserService()
    created = await service.insert(project, "todos", {"title": "temp"})

    assert await service.delete(project, "todos", created["_id"]) is True
    with pytest.raises(NotFoundError):
        await service.get(project, "todos", created["_id"])


async def test_documents_with_string_ids_are_supported(cleanup_app_dbs: list[str]) -> None:
    """Generated apps may use their own `_id` values, so the browser must handle non-ObjectIds."""
    project = await make_project()
    cleanup_app_dbs.append(project.app_db_name)
    service = DataBrowserService()
    await service.insert(project, "settings", {"_id": "theme", "value": "dark"})

    fetched = await service.get(project, "settings", "theme")
    assert fetched["value"] == "dark"

    await service.update(project, "settings", "theme", {"value": "light"})
    assert (await service.get(project, "settings", "theme"))["value"] == "light"


async def test_bson_values_are_serialized_for_the_client(cleanup_app_dbs: list[str]) -> None:
    from datetime import datetime

    project = await make_project()
    cleanup_app_dbs.append(project.app_db_name)
    created = await DataBrowserService().insert(
        project, "events", {"at": datetime(2026, 7, 21, 12, 0, tzinfo=UTC)}
    )

    assert isinstance(created["at"], str) and created["at"].startswith("2026-07-21")


# --------------------------------------------------------------------- through the API


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _auth_project(client: AsyncClient, email: str) -> tuple[str, dict[str, str], str]:
    reg = await client.post("/auth/register", json={"email": email, "password": "password123"})
    headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    created = await client.post("/projects", json={"name": "p"}, headers=headers)
    body = created.json()
    return body["id"], headers, body["app_db_name"]


async def test_the_api_round_trips_a_document(
    client: AsyncClient, cleanup_app_dbs: list[str]
) -> None:
    pid, headers, db_name = await _auth_project(client, "crud@data.test")
    cleanup_app_dbs.append(db_name)
    base = f"/projects/{pid}/data/collections/todos/docs"

    created = await client.post(base, json={"document": {"title": "via api"}}, headers=headers)
    assert created.status_code == 201
    doc_id = created.json()["_id"]

    listed = await client.get(base, headers=headers)
    assert listed.json()["total"] == 1

    updated = await client.put(
        f"{base}/{doc_id}", json={"document": {"title": "edited"}}, headers=headers
    )
    assert updated.json()["title"] == "edited"

    deleted = await client.delete(f"{base}/{doc_id}", headers=headers)
    assert deleted.json() == {"deleted": True}
    assert (await client.get(base, headers=headers)).json()["total"] == 0


async def test_the_api_exposes_where_the_data_lives_without_the_uri(
    client: AsyncClient, cleanup_app_dbs: list[str]
) -> None:
    pid, headers, db_name = await _auth_project(client, "info@data.test")
    cleanup_app_dbs.append(db_name)

    resp = await client.get(f"/projects/{pid}/data/info", headers=headers)
    body = resp.json()

    assert body == {"mode": "platform", "db_name": db_name, "managed": True}
    # The URI is a secret and must never appear on a response (§7).
    assert "mongodb://" not in resp.text and "mongodb+srv://" not in resp.text


async def test_another_users_data_is_not_reachable(
    client: AsyncClient, cleanup_app_dbs: list[str]
) -> None:
    pid, headers, db_name = await _auth_project(client, "owner@data.test")
    cleanup_app_dbs.append(db_name)
    await client.post(
        f"/projects/{pid}/data/collections/todos/docs",
        json={"document": {"title": "private"}},
        headers=headers,
    )

    reg = await client.post(
        "/auth/register", json={"email": "intruder@data.test", "password": "password123"}
    )
    intruder = {"Authorization": f"Bearer {reg.json()['access_token']}"}

    for path in ("data/collections", "data/collections/todos/docs", "data/info"):
        resp = await client.get(f"/projects/{pid}/{path}", headers=intruder)
        assert resp.status_code == 404, path  # existence is never leaked
