"""Whole-spec AI-assist: parse a Haiku draft, never persist it; endpoint authz/degrade."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.agents.anthropic_client import AnthropicClient, TurnComplete
from app.agents.cost import Usage
from app.api.app import create_app
from app.db.models import Project, Run
from app.db.models.enums import CriterionKind
from app.orchestrator.requirements import (
    CriterionInput,
    FeatureInput,
    RequirementSpecInput,
    RequirementsService,
)
from app.orchestrator.requirements_draft import (
    MAX_APP_NAME_CHARS,
    MAX_CRITERIA_PER_FEATURE,
    MAX_FEATURES,
    draft_spec,
)
from tests.agents.test_client_tool_loop import FakeTransport, ScriptedTurn

pytestmark = pytest.mark.usefixtures("mongo_db")


def _client(text: str) -> AnthropicClient:
    return AnthropicClient(
        FakeTransport([ScriptedTurn(deltas=[], turn=TurnComplete(text=text, usage=Usage(50, 20)))])
    )


async def _project() -> Project:
    return await Project(user_id=PydanticObjectId(), name="p", app_db_name="db").insert()


def _feature(name: str, **extra: object) -> dict[str, object]:
    return {
        "name": name,
        "description": f"{name} description",
        "inputs": ["title"],
        "expected_behaviors": ["it appears in the list"],
        "acceptance_criteria": [{"text": f"{name} works", "kind": "e2e"}],
        **extra,
    }


# ---------------------------------------------------------------- parsing


async def test_drafts_a_whole_spec_from_a_freeform_description() -> None:
    project = await _project()
    text = (
        '[{"name": "Todos", "description": "manage todos", "inputs": ["title"], '
        '"expected_behaviors": ["new todo appears"], "acceptance_criteria": '
        '[{"text": "can add a todo", "kind": "unit"}, {"text": "shows in list", "kind": "e2e"}]}, '
        '{"name": "Auth", "acceptance_criteria": [{"text": "a user can log in", "kind": "e2e"}]}]'
    )

    out = (await draft_spec(project, "a todo app with login", client=_client(text))).features

    assert [f.name for f in out] == ["Todos", "Auth"]
    assert out[0].description == "manage todos"
    assert out[0].inputs == ["title"]
    assert out[0].expected_behaviors == ["new todo appears"]
    assert [c.text for c in out[0].acceptance_criteria] == ["can add a todo", "shows in list"]
    assert [c.kind for c in out[0].acceptance_criteria] == [CriterionKind.unit, CriterionKind.e2e]
    # Omitted optional fields degrade to empty, never to None.
    assert out[1].description == "" and out[1].inputs == []


async def test_tolerates_prose_wrapped_json_and_bad_kinds() -> None:
    project = await _project()
    text = (
        "Sure! Here's a draft:\n"
        '[{"name": "Todos", "acceptance_criteria": [{"text": "x", "kind": "banana"}]}]\n'
        "Edit as you like."
    )
    out = (await draft_spec(project, "a todo app", client=_client(text))).features

    assert len(out) == 1
    assert out[0].acceptance_criteria[0].kind is CriterionKind.either  # unknown kind → either


async def test_drops_features_the_save_endpoint_would_reject() -> None:
    """Blank names, duplicates and criterion-less features never reach the form as unsavable."""
    project = await _project()
    text = (
        '[{"name": "  ", "acceptance_criteria": [{"text": "x"}]},'
        ' {"name": "Todos", "acceptance_criteria": []},'
        ' {"name": "Auth", "acceptance_criteria": [{"text": "logs in"}, {"text": "  "}]},'
        ' {"name": "auth", "acceptance_criteria": [{"text": "dupe"}]}]'
    )
    out = (await draft_spec(project, "an app", client=_client(text))).features

    assert [f.name for f in out] == ["Auth"]
    assert [c.text for c in out[0].acceptance_criteria] == ["logs in"]  # the blank one is dropped


async def test_caps_the_draft_size() -> None:
    project = await _project()
    crit = [{"text": f"c{i}"} for i in range(MAX_CRITERIA_PER_FEATURE + 5)]
    features = [_feature(f"F{i}", acceptance_criteria=crit) for i in range(MAX_FEATURES + 4)]

    out = (await draft_spec(project, "an app", client=_client(json.dumps(features)))).features

    assert len(out) == MAX_FEATURES
    assert len(out[0].acceptance_criteria) == MAX_CRITERIA_PER_FEATURE


async def test_unparseable_reply_yields_no_draft() -> None:
    project = await _project()
    out = (await draft_spec(project, "an app", client=_client("I couldn't do that."))).features
    assert out == []


async def test_an_object_without_features_yields_no_draft() -> None:
    project = await _project()
    out = (await draft_spec(project, "an app", client=_client('{"name": "Todos"}'))).features
    assert out == []


async def test_blank_description_short_circuits() -> None:
    project = await _project()
    # No model call needed; a fake that would explode proves we never reach it.
    out = (await draft_spec(project, "   ", client=_client("[]"))).features
    assert out == []


# ---------------------------------------------------------------- never persisted / costed


async def test_a_draft_is_never_saved_as_a_requirement_spec() -> None:
    """The human owns the spec: only an explicit save mints a version (and criterion ids)."""
    project = await _project()
    assert project.id is not None
    text = '[{"name": "Todos", "acceptance_criteria": [{"text": "can add a todo"}]}]'

    out = (await draft_spec(project, "a todo app", client=_client(text))).features

    assert len(out) == 1
    assert await RequirementsService().latest(project.id) is None


async def test_the_draft_call_is_recorded_as_a_closed_run() -> None:
    project = await _project()
    await draft_spec(project, "a todo app", client=_client("[]"))

    runs = await Run.find(Run.project_id == project.id).to_list()
    assert [r.kind for r in runs] == ["requirements:draft"]
    assert runs[0].finished_at is not None


# ---------------------------------------------------------------- endpoint


@pytest_asyncio.fixture
async def http() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _register(client: AsyncClient, email: str) -> str:
    resp = await client.post("/auth/register", json={"email": email, "password": "password123"})
    return str(resp.json()["access_token"])


async def _create_project(client: AsyncClient, token: str) -> str:
    created = await client.post(
        "/projects", json={"name": "p"}, headers={"Authorization": f"Bearer {token}"}
    )
    return str(created.json()["id"])


async def test_draft_endpoint_requires_auth(http: AsyncClient) -> None:
    resp = await http.post("/projects/whatever/requirements/draft", json={"description": "x"})
    assert resp.status_code == 401


async def test_draft_endpoint_is_ownership_checked(http: AsyncClient) -> None:
    owner = await _register(http, "draft-owner@example.com")
    intruder = await _register(http, "draft-intruder@example.com")
    pid = await _create_project(http, owner)

    resp = await http.post(
        f"/projects/{pid}/requirements/draft",
        json={"description": "a todo app"},
        headers={"Authorization": f"Bearer {intruder}"},
    )
    assert resp.status_code == 404


async def test_draft_endpoint_degrades_when_model_unavailable(http: AsyncClient) -> None:
    # No ANTHROPIC creds / SDK in the test env → the provider path fails soft with 502, never a 500,
    # and the guided form stays usable.
    token = await _register(http, "draft-owner2@example.com")
    pid = await _create_project(http, token)

    resp = await http.post(
        f"/projects/{pid}/requirements/draft",
        json={"description": "a todo app"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 502


async def test_draft_endpoint_returns_an_empty_draft_for_a_blank_description(
    http: AsyncClient,
) -> None:
    """Short-circuited before any provider call — so this is a 200 with nothing to edit."""
    token = await _register(http, "draft-owner3@example.com")
    pid = await _create_project(http, token)

    resp = await http.post(
        f"/projects/{pid}/requirements/draft",
        json={"description": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"app_name": "", "features": []}


async def test_draft_endpoint_saves_the_original_prompt_even_when_the_model_is_unavailable(
    http: AsyncClient,
) -> None:
    """The description is worth keeping regardless of whether the draft call itself succeeds —
    Design (and anything else that wants it later) can use it even if this attempt 502s."""
    token = await _register(http, "draft-owner4@example.com")
    pid = await _create_project(http, token)

    resp = await http.post(
        f"/projects/{pid}/requirements/draft",
        json={"description": "A clean personal todo app"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 502  # unchanged — no provider creds in the test env

    project = await Project.get(pid)
    assert project is not None
    assert project.original_prompt == "A clean personal todo app"


async def test_draft_endpoint_never_overwrites_the_original_prompt(http: AsyncClient) -> None:
    """'Original' means original — a later, differently-worded draft call must not replace it."""
    token = await _register(http, "draft-owner5@example.com")
    pid = await _create_project(http, token)

    await http.post(
        f"/projects/{pid}/requirements/draft",
        json={"description": "first idea"},
        headers={"Authorization": f"Bearer {token}"},
    )
    await http.post(
        f"/projects/{pid}/requirements/draft",
        json={"description": "a completely different idea"},
        headers={"Authorization": f"Bearer {token}"},
    )

    project = await Project.get(pid)
    assert project is not None
    assert project.original_prompt == "first idea"


# ---------------------------------------------------------------- the proposed app name


async def test_the_draft_proposes_a_name_for_the_app() -> None:
    """A name the user can accept or edit — it is what a Stitch project ends up called."""
    project = await _project()
    text = json.dumps({"app_name": "Focus", "features": [_feature("Todos")]})

    draft = await draft_spec(project, "a todo app", client=_client(text))

    assert draft.app_name == "Focus"
    assert [f.name for f in draft.features] == ["Todos"]


async def test_a_bare_feature_array_still_drafts_without_a_name() -> None:
    """The name is a bonus, never a reason to throw away a usable feature list."""
    project = await _project()
    text = json.dumps([_feature("Todos")])

    draft = await draft_spec(project, "a todo app", client=_client(text))

    assert draft.app_name == ""
    assert [f.name for f in draft.features] == ["Todos"]


@pytest.mark.parametrize("proposed", ["My App", "  web app  ", "", "Untitled"])
async def test_placeholder_names_are_refused(proposed: str) -> None:
    """A name the user would only have to delete is worse than none at all."""
    project = await _project()
    text = json.dumps({"app_name": proposed, "features": [_feature("Todos")]})

    draft = await draft_spec(project, "an app", client=_client(text))

    assert draft.app_name == ""


async def test_a_long_name_is_capped() -> None:
    project = await _project()
    text = json.dumps({"app_name": "N" * 200, "features": [_feature("Todos")]})

    draft = await draft_spec(project, "an app", client=_client(text))

    assert len(draft.app_name) == MAX_APP_NAME_CHARS


async def test_the_name_survives_a_save_and_is_editable() -> None:
    """It is stored on the spec, not as a criterion-less feature, and the user's edit wins."""
    project = await _project()
    assert project.id is not None
    service = RequirementsService()

    saved = await service.save(
        project.id,
        RequirementSpecInput(
            app_name="Focus",
            features=[
                FeatureInput(
                    name="Todos",
                    acceptance_criteria=[CriterionInput(text="can add a todo")],
                )
            ],
        ),
    )
    assert saved.app_name == "Focus"
    assert [f.name for f in saved.features] == ["Todos"]  # the name is NOT a feature

    renamed = await service.save(
        project.id,
        RequirementSpecInput(
            app_name="Momentum",
            features=[
                FeatureInput(
                    name="Todos",
                    acceptance_criteria=[CriterionInput(text="can add a todo")],
                )
            ],
        ),
    )
    assert renamed.app_name == "Momentum"
    assert renamed.version == saved.version + 1  # a rename is a real change


async def test_omitting_the_name_on_save_keeps_the_stored_one() -> None:
    """An older client saving a feature edit must not silently wipe the name."""
    project = await _project()
    assert project.id is not None
    service = RequirementsService()
    features = [
        FeatureInput(name="Todos", acceptance_criteria=[CriterionInput(text="can add a todo")])
    ]
    await service.save(project.id, RequirementSpecInput(app_name="Focus", features=features))

    again = await service.save(project.id, RequirementSpecInput(features=features))

    assert again.app_name == "Focus"
