"""Optional AI-assist for requirements (phase-26): parse Haiku proposals; endpoint authz/degrade."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.agents.anthropic_client import AnthropicClient, TurnComplete
from app.agents.cost import Usage
from app.api.app import create_app
from app.db.models import Project
from app.db.models.enums import CriterionKind
from app.orchestrator.requirements_suggest import suggest_criteria
from tests.agents.test_client_tool_loop import FakeTransport, ScriptedTurn

pytestmark = pytest.mark.usefixtures("mongo_db")


def _client(text: str) -> AnthropicClient:
    return AnthropicClient(
        FakeTransport([ScriptedTurn(deltas=[], turn=TurnComplete(text=text, usage=Usage(50, 20)))])
    )


async def _project() -> Project:
    return await Project(user_id=PydanticObjectId(), name="p", app_db_name="db").insert()


async def test_parses_proposals_from_json() -> None:
    project = await _project()
    text = '[{"text": "can add a todo", "kind": "unit"}, {"text": "shows in list", "kind": "e2e"}]'
    out = await suggest_criteria(project, "Todos", "manage todos", client=_client(text))

    assert [c.text for c in out] == ["can add a todo", "shows in list"]
    assert [c.kind for c in out] == [CriterionKind.unit, CriterionKind.e2e]


async def test_tolerates_prose_wrapped_json_and_bad_kinds() -> None:
    project = await _project()
    text = (
        "Sure! Here you go:\n"
        '[{"text": "x", "kind": "banana"}, {"text": "  ", "kind": "unit"}]\n'
        "Hope that helps."
    )
    out = await suggest_criteria(project, "F", "d", client=_client(text))

    assert len(out) == 1  # the blank-text item is dropped
    assert out[0].kind == CriterionKind.either  # unknown kind → either


async def test_unparseable_reply_yields_no_suggestions() -> None:
    project = await _project()
    out = await suggest_criteria(project, "F", "d", client=_client("I couldn't do that."))
    assert out == []


async def test_blank_feature_name_short_circuits() -> None:
    project = await _project()
    # No model call needed; a fake that would explode proves we never reach it.
    out = await suggest_criteria(project, "   ", "d", client=_client("[]"))
    assert out == []


# ---------------------------------------------------------------- endpoint


@pytest_asyncio.fixture
async def http() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/auth/register", json={"email": email, "password": "password123"})
    return str(resp.json()["access_token"])


async def test_suggest_endpoint_requires_auth(http: AsyncClient) -> None:
    resp = await http.post("/projects/whatever/requirements/suggest", json={"name": "x"})
    assert resp.status_code == 401


async def test_suggest_endpoint_is_ownership_checked(http: AsyncClient) -> None:
    owner = await _register(http, "owner@example.com")
    intruder = await _register(http, "intruder@example.com")
    created = await http.post(
        "/projects", json={"name": "p"}, headers={"Authorization": f"Bearer {owner}"}
    )
    pid = created.json()["id"]

    resp = await http.post(
        f"/projects/{pid}/requirements/suggest",
        json={"name": "Todos", "description": "d"},
        headers={"Authorization": f"Bearer {intruder}"},
    )
    assert resp.status_code == 404


async def test_suggest_endpoint_degrades_when_model_unavailable(http: AsyncClient) -> None:
    # No ANTHROPIC creds / SDK in the test env → the provider path fails soft with 502, never a 500.
    token = await _register(http, "owner2@example.com")
    created = await http.post(
        "/projects", json={"name": "p"}, headers={"Authorization": f"Bearer {token}"}
    )
    pid = created.json()["id"]

    resp = await http.post(
        f"/projects/{pid}/requirements/suggest",
        json={"name": "Todos", "description": "d"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 502
