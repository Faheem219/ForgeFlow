"""Workspace FS + git routes end-to-end over HTTP, backed by a real (local) filesystem + git.

The runtime provider is swapped for a per-project local backend, so the routes exercise genuine
round-trip + git behaviour without a Docker daemon.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.db.models import Project
from app.sandbox.runtime import LocalRuntime, WorkspaceRuntime
from app.sandbox.workspace import set_runtime_provider
from tests.sandbox.conftest import requires_posix_runtime

pytestmark = [
    pytest.mark.usefixtures("mongo_db"),
    pytest.mark.skipif(shutil.which("git") is None, reason="git not installed"),
    requires_posix_runtime,
]


@pytest.fixture(autouse=True)
def local_runtime(tmp_path: object) -> Iterator[None]:
    base = os.path.join(str(tmp_path), "workspaces")

    async def provider(project: Project) -> WorkspaceRuntime:
        return LocalRuntime(os.path.join(base, str(project.id)))

    set_runtime_provider(provider)
    yield
    # conftest's _reset_state clears the provider after the test.


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/auth/register", json={"email": email, "password": "password123"})
    token: str = resp.json()["access_token"]
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _new_project(client: AsyncClient, token: str) -> str:
    resp = await client.post("/projects", json={"name": "p"}, headers=_auth(token))
    return str(resp.json()["id"])


async def test_file_roundtrip_over_http(client: AsyncClient) -> None:
    token = await _register(client, "fs-owner@example.com")
    pid = await _new_project(client, token)
    h = _auth(token)

    put = await client.put(
        f"/projects/{pid}/fs/file",
        json={"path": "src/app.ts", "content": "export const x = 1;\n"},
        headers=h,
    )
    assert put.status_code == 200
    assert put.json()["path"] == "src/app.ts"

    got = await client.get(f"/projects/{pid}/fs/file", params={"path": "src/app.ts"}, headers=h)
    assert got.status_code == 200
    assert got.json()["content"] == "export const x = 1;\n"

    tree = await client.get(f"/projects/{pid}/fs/tree", headers=h)
    assert tree.status_code == 200
    assert any(n["path"] == "src/app.ts" for n in tree.json())

    deleted = await client.delete(
        f"/projects/{pid}/fs/file", params={"path": "src/app.ts"}, headers=h
    )
    assert deleted.status_code == 204

    missing = await client.get(f"/projects/{pid}/fs/file", params={"path": "src/app.ts"}, headers=h)
    assert missing.status_code == 404


async def test_traversal_rejected_over_http(client: AsyncClient) -> None:
    token = await _register(client, "fs-evil@example.com")
    pid = await _new_project(client, token)
    h = _auth(token)

    resp = await client.put(
        f"/projects/{pid}/fs/file",
        json={"path": "../../etc/passwd", "content": "x"},
        headers=h,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "user_error"


async def test_git_commit_and_diff_over_http(client: AsyncClient) -> None:
    token = await _register(client, "fs-git@example.com")
    pid = await _new_project(client, token)
    h = _auth(token)

    await client.put(
        f"/projects/{pid}/fs/file", json={"path": "a.txt", "content": "one\n"}, headers=h
    )
    first = await client.post(
        f"/projects/{pid}/sandbox/git/commit", json={"message": "first"}, headers=h
    )
    assert first.status_code == 200
    assert first.json()["committed"] is True
    sha1 = first.json()["sha"]
    assert sha1

    # No changes → committed False, same sha.
    noop = await client.post(
        f"/projects/{pid}/sandbox/git/commit", json={"message": "again"}, headers=h
    )
    assert noop.json()["committed"] is False
    assert noop.json()["sha"] == sha1

    info = await client.get(f"/projects/{pid}/sandbox/git", headers=h)
    assert info.status_code == 200
    assert info.json()["current_sha"] == sha1
    assert info.json()["last_passing"] is None


async def test_foreign_project_is_not_found(client: AsyncClient) -> None:
    owner = await _register(client, "fs-a@example.com")
    intruder = await _register(client, "fs-b@example.com")
    pid = await _new_project(client, owner)
    h = _auth(intruder)

    assert (await client.get(f"/projects/{pid}/fs/tree", headers=h)).status_code == 404
    assert (
        await client.put(
            f"/projects/{pid}/fs/file", json={"path": "x.ts", "content": "x"}, headers=h
        )
    ).status_code == 404
    assert (
        await client.post(f"/projects/{pid}/sandbox/git/commit", json={"message": "m"}, headers=h)
    ).status_code == 404


async def test_requires_authentication(client: AsyncClient) -> None:
    fake_id = "0" * 24
    assert (await client.get(f"/projects/{fake_id}/fs/tree")).status_code == 401
    assert (
        await client.put(f"/projects/{fake_id}/fs/file", json={"path": "x", "content": "y"})
    ).status_code == 401
