"""Design HTTP surface (phase-19): screenshot upload + active-provider info, ownership-checked."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app

pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    from app.core.config import reset_config

    # Filesystem blobs so uploaded screenshots never touch the shared meta DB; fake provider so the
    # provider-info endpoint reports a healthy provider without creds.
    monkeypatch.setenv("BLOB_BACKEND", "filesystem")
    monkeypatch.setenv("BLOB_FS_DIR", str(tmp_path))
    monkeypatch.setenv("DESIGN_PROVIDER", "fake")
    reset_config()
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/auth/register", json={"email": email, "password": "password123"})
    token: str = resp.json()["access_token"]
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _project(client: AsyncClient, token: str) -> str:
    resp = await client.post("/projects", json={"name": "p"}, headers=_auth(token))
    pid: str = resp.json()["id"]
    return pid


async def test_upload_images_returns_refs(client: AsyncClient) -> None:
    token = await _register(client, "up@example.com")
    pid = await _project(client, token)

    data_b64 = base64.b64encode(b"\x89PNG screenshot").decode("ascii")
    resp = await client.post(
        f"/projects/{pid}/design/images",
        json={
            "images": [{"filename": "a.png", "media_type": "image/png", "data_base64": data_b64}]
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200
    images = resp.json()["images"]
    assert len(images) == 1
    assert images[0]["ref"] and images[0]["filename"] == "a.png"


async def test_upload_rejects_bad_base64(client: AsyncClient) -> None:
    token = await _register(client, "bad@example.com")
    pid = await _project(client, token)

    resp = await client.post(
        f"/projects/{pid}/design/images",
        json={"images": [{"filename": "x.png", "media_type": "image/png", "data_base64": "!!!"}]},
        headers=_auth(token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "user_error"


async def test_provider_info_reports_health_and_capabilities(client: AsyncClient) -> None:
    token = await _register(client, "prov@example.com")
    pid = await _project(client, token)

    resp = await client.get(f"/projects/{pid}/design/provider", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"] == "fake"
    assert body["health"] == "ok"
    assert body["capabilities"]["from_text"] is True


async def test_design_endpoints_are_ownership_checked(client: AsyncClient) -> None:
    owner = await _register(client, "owner@example.com")
    intruder = await _register(client, "intruder@example.com")
    pid = await _project(client, owner)

    ih = _auth(intruder)
    assert (await client.get(f"/projects/{pid}/design/provider", headers=ih)).status_code == 404
    assert (
        await client.post(f"/projects/{pid}/design/images", json={"images": []}, headers=ih)
    ).status_code == 404


async def test_design_endpoints_require_auth(client: AsyncClient) -> None:
    assert (await client.get("/projects/whatever/design/provider")).status_code == 401


async def test_screen_list_is_empty_for_a_provider_that_cannot_enumerate(
    client: AsyncClient,
) -> None:
    """`fake` has no project of screens, so the UI simply gets nothing to put in the picker —
    the endpoint must not 404 or error, or the design panel would break for those providers."""
    token = await _register(client, "screens@example.com")
    pid = await _project(client, token)

    resp = await client.get(f"/projects/{pid}/design/screens", headers=_auth(token))

    assert resp.status_code == 200
    assert resp.json() == {"screens": []}


async def test_screen_list_requires_ownership(client: AsyncClient) -> None:
    owner = await _register(client, "owner-screens@example.com")
    pid = await _project(client, owner)
    stranger = await _register(client, "stranger-screens@example.com")

    resp = await client.get(f"/projects/{pid}/design/screens", headers=_auth(stranger))
    assert resp.status_code == 404  # existence is not leaked
