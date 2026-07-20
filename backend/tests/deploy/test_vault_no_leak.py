"""Non-leakage (phase-34): a stored secret never reaches a response body or a log line."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.core.logging import REDACTED, JsonFormatter
from app.db.models.enums import CredentialKind
from app.deploy.secrets import SecretVault

pytestmark = pytest.mark.usefixtures("mongo_db")

SECRET = "vercel_live_tok_supersecret_value_42"


@pytest_asyncio.fixture
async def http() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/auth/register", json={"email": email, "password": "password123"})
    return str(resp.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- API surface


async def test_put_response_carries_metadata_but_never_the_value(
    http: AsyncClient, fernet_key: str
) -> None:
    token = await _register(http, "vault-put@example.com")

    resp = await http.put("/credentials/vercel", json={"secret": SECRET}, headers=_auth(token))

    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "vercel"
    assert body["scope"] == "byo"
    assert body["last4"] == SECRET[-4:]
    assert SECRET not in resp.text


async def test_list_response_never_contains_the_value(http: AsyncClient, fernet_key: str) -> None:
    token = await _register(http, "vault-list@example.com")
    await http.put("/credentials/vercel", json={"secret": SECRET}, headers=_auth(token))

    resp = await http.get("/credentials", headers=_auth(token))

    assert resp.status_code == 200
    assert SECRET not in resp.text
    [entry] = resp.json()
    assert entry["last4"] == SECRET[-4:]
    assert "secret" not in entry and "encrypted_secret" not in entry


async def test_there_is_no_endpoint_that_reads_a_secret_back(
    http: AsyncClient, fernet_key: str
) -> None:
    token = await _register(http, "vault-read@example.com")
    await http.put("/credentials/vercel", json={"secret": SECRET}, headers=_auth(token))

    # A plausible "reveal" route must not exist (405/404 — never 200 with the value).
    resp = await http.get("/credentials/vercel", headers=_auth(token))
    assert resp.status_code in (404, 405)
    assert SECRET not in resp.text


async def test_credential_routes_require_auth(http: AsyncClient) -> None:
    assert (await http.get("/credentials")).status_code == 401
    assert (await http.put("/credentials/vercel", json={"secret": "x"})).status_code == 401
    assert (await http.delete("/credentials/vercel")).status_code == 401


async def test_one_users_credentials_are_invisible_to_another(
    http: AsyncClient, fernet_key: str
) -> None:
    owner = await _register(http, "vault-owner@example.com")
    intruder = await _register(http, "vault-intruder@example.com")
    await http.put("/credentials/vercel", json={"secret": SECRET}, headers=_auth(owner))

    resp = await http.get("/credentials", headers=_auth(intruder))
    assert resp.json() == []
    assert SECRET not in resp.text

    # ...and they cannot delete what they cannot see.
    assert (await http.delete("/credentials/vercel", headers=_auth(intruder))).status_code == 404


async def test_delete_removes_the_credential(http: AsyncClient, fernet_key: str) -> None:
    token = await _register(http, "vault-delete@example.com")
    await http.put("/credentials/vercel", json={"secret": SECRET}, headers=_auth(token))

    assert (await http.delete("/credentials/vercel", headers=_auth(token))).status_code == 200
    assert (await http.get("/credentials", headers=_auth(token))).json() == []


# ---------------------------------------------------------------- logs


async def test_no_log_line_emitted_during_a_full_cycle_contains_the_secret(
    http: AsyncClient, fernet_key: str, caplog: pytest.LogCaptureFixture
) -> None:
    token = await _register(http, "vault-logs@example.com")

    with caplog.at_level(logging.DEBUG):
        await http.put("/credentials/vercel", json={"secret": SECRET}, headers=_auth(token))
        await http.get("/credentials", headers=_auth(token))
        await http.delete("/credentials/vercel", headers=_auth(token))

    formatter = JsonFormatter()
    rendered = "\n".join(formatter.format(record) for record in caplog.records)
    assert SECRET not in rendered
    assert SECRET[-8:] not in rendered  # not even a long tail fragment


async def test_vault_operations_do_not_log_the_secret(
    fernet_key: str, caplog: pytest.LogCaptureFixture
) -> None:
    user_id = PydanticObjectId()

    with caplog.at_level(logging.DEBUG):
        vault = SecretVault()
        await vault.put_credential(user_id, CredentialKind.render, SECRET)
        await vault.get_credential(user_id, CredentialKind.render)
        await vault.list_kinds(user_id)
        await vault.delete(user_id, CredentialKind.render)

    formatter = JsonFormatter()
    rendered = "\n".join(formatter.format(record) for record in caplog.records)
    assert SECRET not in rendered


def test_formatter_redacts_credential_shaped_extras() -> None:
    """Defense-in-depth: an accidental `extra={"token": ...}` is scrubbed by the formatter."""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="calling provider",
        args=(),
        exc_info=None,
    )
    record.vercel_token = SECRET
    record.api_key = SECRET
    record.mongodb_uri = f"mongodb://user:{SECRET}@host/db"
    record.nested = {"authorization": f"Bearer {SECRET}", "safe": "keep-me"}
    record.project_id = "p1"

    payload = json.loads(JsonFormatter().format(record))

    assert SECRET not in json.dumps(payload)
    assert payload["vercel_token"] == REDACTED
    assert payload["api_key"] == REDACTED
    assert payload["mongodb_uri"] == REDACTED
    assert payload["nested"]["authorization"] == REDACTED
    assert payload["nested"]["safe"] == "keep-me"  # non-sensitive keys survive
    assert payload["project_id"] == "p1"
