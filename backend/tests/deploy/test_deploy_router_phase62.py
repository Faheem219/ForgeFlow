"""Provider logs, deploy config and deployment deletion over HTTP (phase-62).

The read side must be **total**: every topology node the UI can click is answerable, and no provider
problem is an error status — a revoked token or a deployment removed in the provider's own dashboard
are normal states of the world, and neither may break the panel.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.db.models import STATUS_DELETED, Deployment, DeploymentRef
from app.db.models.enums import DeployMode, Stage, StageStatus
from app.db.repos import StageStateRepo
from app.deploy.providers.base import DeployRef, DeployTarget, LogLine

pytestmark = pytest.mark.usefixtures("mongo_db", "blob_env", "fernet_key")


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


async def _project(client: AsyncClient, token: str) -> str:
    resp = await client.post("/projects", json={"name": "p"}, headers=_auth(token))
    pid: str = resp.json()["id"]
    return pid


async def _deployment(project_id: str, *, refs: bool = True) -> Deployment:
    return await Deployment(
        project_id=PydanticObjectId(project_id),
        mode=DeployMode.seamless,
        fe_target="vercel",
        be_target="vercel",
        db_target="platform",
        urls={"fe": "https://web", "be": "https://api"},
        refs=(
            {
                "be": DeploymentRef(provider="vercel", id="dpl_be", project="forgeflow-api-1"),
                "fe": DeploymentRef(provider="vercel", id="dpl_fe", project="forgeflow-web-1"),
            }
            if refs
            else {}
        ),
        status="live",
        topology_snapshot={"nodes": [], "edges": [], "status": "live"},
    ).insert()


class StubProvider:
    """A provider whose logs/destroy are scripted — installed over ``provider_for``."""

    key = "vercel"
    target = DeployTarget.fe
    credential_kind = "vercel"

    def __init__(self, lines: list[str] | None = None, error: Exception | None = None) -> None:
        self.lines = lines or []
        self.error = error
        self.log_refs: list[DeployRef] = []
        self.destroyed: list[DeployRef] = []

    async def logs(self, ref: DeployRef, **_: Any) -> list[LogLine]:
        self.log_refs.append(ref)
        if self.error is not None:
            raise self.error
        return [LogLine(message=line, ts="1") for line in self.lines]

    async def destroy(self, ref: DeployRef, **_: Any) -> bool:
        self.destroyed.append(ref)
        if self.error is not None:
            raise self.error
        return True


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch) -> StubProvider:
    """Route every provider lookup the router makes to one recording stub."""
    stub = StubProvider()
    from app.deploy import deploy_router as router_mod
    from app.deploy import teardown as teardown_mod

    # Both lookups: the router resolves the provider for logs, the teardown service for destroy.
    monkeypatch.setattr(router_mod, "provider_for", lambda _target: stub)
    monkeypatch.setattr(teardown_mod, "provider_for", lambda _target: stub)
    return stub


# ------------------------------------------------------------------------ provider logs


async def test_provider_logs_come_from_the_provider(
    client: AsyncClient, stub_provider: StubProvider
) -> None:
    token = await _register(client, "plogs@deployed.test")
    pid = await _project(client, token)
    await _deployment(pid)
    stub_provider.lines = ["Cloning...", "Build completed"]

    resp = await client.get(f"/projects/{pid}/deploy/latest/logs/be", headers=_auth(token))
    body = resp.json()

    assert resp.status_code == 200
    assert [line["message"] for line in body["lines"]] == ["Cloning...", "Build completed"]
    assert body["provider"] == "vercel"
    assert body["error"] is None
    # Keyed off the stored ref for that target, not the other one.
    assert stub_provider.log_refs[0].id == "dpl_be"


async def test_a_provider_failure_is_an_empty_log_not_an_error_status(
    client: AsyncClient, stub_provider: StubProvider
) -> None:
    """A revoked token must not break the topology panel."""
    token = await _register(client, "plogsfail@deployed.test")
    pid = await _project(client, token)
    await _deployment(pid)
    stub_provider.error = RuntimeError("401 unauthorized")

    resp = await client.get(f"/projects/{pid}/deploy/latest/logs/fe", headers=_auth(token))
    body = resp.json()

    assert resp.status_code == 200
    assert body["lines"] == []
    assert "401" in body["error"]


async def test_a_record_without_refs_explains_itself(
    client: AsyncClient, stub_provider: StubProvider
) -> None:
    token = await _register(client, "norefs@deployed.test")
    pid = await _project(client, token)
    await _deployment(pid, refs=False)

    resp = await client.get(f"/projects/{pid}/deploy/latest/logs/be", headers=_auth(token))
    body = resp.json()

    assert body["lines"] == []
    assert "Redeploy" in body["error"]
    assert stub_provider.log_refs == []  # nothing to ask about


async def test_the_database_node_is_answered_not_rejected(
    client: AsyncClient, stub_provider: StubProvider
) -> None:
    """`db` is a node the UI can legitimately click; it deserves a sentence, not a 422."""
    token = await _register(client, "dbnode@deployed.test")
    pid = await _project(client, token)
    await _deployment(pid)

    resp = await client.get(f"/projects/{pid}/deploy/latest/logs/db", headers=_auth(token))

    assert resp.status_code == 200
    assert resp.json()["error"] == (
        "The database is not a provider deployment — it has no build log."
    )


async def test_provider_logs_are_null_before_any_deploy(client: AsyncClient) -> None:
    token = await _register(client, "nodeploy@deployed.test")
    pid = await _project(client, token)

    resp = await client.get(f"/projects/{pid}/deploy/latest/logs/be", headers=_auth(token))

    assert resp.status_code == 200
    assert resp.json() is None


async def test_provider_logs_are_owner_only(client: AsyncClient) -> None:
    owner = await _register(client, "owner@deployed.test")
    pid = await _project(client, owner)
    await _deployment(pid)
    intruder = await _register(client, "intruder@deployed.test")

    resp = await client.get(f"/projects/{pid}/deploy/latest/logs/be", headers=_auth(intruder))

    assert resp.status_code == 404


# ------------------------------------------------------------------------ deploy config


async def test_deploy_config_names_the_credentials_a_byo_deploy_needs(
    client: AsyncClient,
) -> None:
    token = await _register(client, "cfg@deployed.test")
    pid = await _project(client, token)

    resp = await client.get(f"/projects/{pid}/deploy/config", headers=_auth(token))
    body = resp.json()

    assert body["be_provider"] == "vercel"
    assert body["byo_required_credentials"] == ["vercel"]
    assert body["byo_optional_credentials"] == ["mongo_uri"]


async def test_deploy_config_follows_the_configured_backend_provider(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asking a Render-backed instance for a Vercel token is exactly the mistake this prevents."""
    from app.core.config import reset_config

    monkeypatch.setenv("DEPLOY_BE_PROVIDER", "render")
    reset_config()
    token = await _register(client, "cfgrender@deployed.test")
    pid = await _project(client, token)

    resp = await client.get(f"/projects/{pid}/deploy/config", headers=_auth(token))
    body = resp.json()

    assert body["be_provider"] == "render"
    assert body["byo_required_credentials"] == ["render", "vercel"]
    reset_config()


# ------------------------------------------------------------------------ delete


async def test_delete_destroys_the_deployment_and_retires_the_record(
    client: AsyncClient, stub_provider: StubProvider
) -> None:
    token = await _register(client, "del@deployed.test")
    pid = await _project(client, token)
    deployment = await _deployment(pid)
    await StageStateRepo().set_status(PydanticObjectId(pid), Stage.deploy, StageStatus.complete)

    resp = await client.delete(f"/projects/{pid}/deploy/latest", headers=_auth(token))
    body = resp.json()

    assert resp.status_code == 200
    assert sorted(body["destroyed"]) == ["be", "fe"]
    assert body["record_deleted"] is True
    assert {r.id for r in stub_provider.destroyed} == {"dpl_be", "dpl_fe"}

    stored = await Deployment.get(deployment.id)
    assert stored is not None and stored.status == STATUS_DELETED

    # The stage no longer claims a live deployment, so validate's prereq is unsatisfied again.
    states = {
        s.stage: s.status for s in await StageStateRepo().list_for_project(PydanticObjectId(pid))
    }
    assert states[Stage.deploy] is StageStatus.empty


async def test_deleting_twice_is_refused(client: AsyncClient, stub_provider: StubProvider) -> None:
    token = await _register(client, "del2@deployed.test")
    pid = await _project(client, token)
    await _deployment(pid)

    first = await client.delete(f"/projects/{pid}/deploy/latest", headers=_auth(token))
    second = await client.delete(f"/projects/{pid}/deploy/latest", headers=_auth(token))

    assert first.status_code == 200
    assert second.status_code == 404


async def test_deleting_without_a_deployment_is_a_404(client: AsyncClient) -> None:
    token = await _register(client, "delnone@deployed.test")
    pid = await _project(client, token)

    resp = await client.delete(f"/projects/{pid}/deploy/latest", headers=_auth(token))

    assert resp.status_code == 404


async def test_delete_is_owner_only(client: AsyncClient, stub_provider: StubProvider) -> None:
    owner = await _register(client, "delowner@deployed.test")
    pid = await _project(client, owner)
    await _deployment(pid)
    intruder = await _register(client, "delintruder@deployed.test")

    resp = await client.delete(f"/projects/{pid}/deploy/latest", headers=_auth(intruder))

    assert resp.status_code == 404
    assert stub_provider.destroyed == []


async def test_a_refusing_provider_still_deletes_and_reports(
    client: AsyncClient, stub_provider: StubProvider
) -> None:
    token = await _register(client, "delfail@deployed.test")
    pid = await _project(client, token)
    await _deployment(pid)
    stub_provider.error = RuntimeError("deployment already removed")

    resp = await client.delete(f"/projects/{pid}/deploy/latest", headers=_auth(token))
    body = resp.json()

    assert resp.status_code == 200
    assert body["destroyed"] == []
    assert body["record_deleted"] is True
    assert len(body["warnings"]) == 2
