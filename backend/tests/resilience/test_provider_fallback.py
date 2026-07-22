"""Automatic design-provider fallback (phase-48): Stitch quota → Figma/fake + a user message."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.core.config import reset_config
from app.core.errors import ProviderError
from app.db.models import Project
from app.design.registry import register_provider
from app.design.resilient import (
    DesignCapability,
    candidate_keys,
    invoke_with_fallback,
)
from tests.resilience.conftest import ScriptedDesignProvider

pytestmark = pytest.mark.usefixtures("mongo_db")


def _install(**providers: ScriptedDesignProvider) -> dict[str, ScriptedDesignProvider]:
    for provider in providers.values():
        register_provider(provider)
    return providers


async def _project() -> Project:
    return await Project(user_id=PydanticObjectId(), name="p", app_db_name="db").insert()


# ---------------------------------------------------------------- unit: the invoker


async def test_quota_exhaustion_falls_back_to_the_next_provider() -> None:
    _install(
        stitch=ScriptedDesignProvider("stitch", fail_text="quota"),
        figma=ScriptedDesignProvider("figma"),
        fake=ScriptedDesignProvider("fake"),
    )
    project = await _project()

    outcome = await invoke_with_fallback(
        project, DesignCapability.from_text, lambda p: p.generate_from_text("a todo app")
    )

    assert outcome.provider_key == "figma"
    assert outcome.result.provider == "figma"
    assert outcome.fell_back is True
    assert outcome.attempted == ["stitch", "figma"]


async def test_the_fallback_note_names_the_reason_and_the_substitute() -> None:
    _install(
        stitch=ScriptedDesignProvider("stitch", fail_text="quota"),
        figma=ScriptedDesignProvider("figma"),
        fake=ScriptedDesignProvider("fake"),
    )
    project = await _project()

    outcome = await invoke_with_fallback(
        project, DesignCapability.from_text, lambda p: p.generate_from_text("x")
    )

    note = outcome.note()
    assert note is not None
    assert "stitch" in note and "quota" in note and "figma" in note


async def test_it_walks_the_whole_chain_until_something_serves() -> None:
    _install(
        stitch=ScriptedDesignProvider("stitch", fail_text="quota"),
        figma=ScriptedDesignProvider("figma", fail_text="transient"),
        fake=ScriptedDesignProvider("fake"),
    )
    project = await _project()

    outcome = await invoke_with_fallback(
        project, DesignCapability.from_text, lambda p: p.generate_from_text("x")
    )

    assert outcome.provider_key == "fake"  # the always-available backstop
    assert outcome.attempted == ["stitch", "figma", "fake"]


async def test_no_fallback_when_the_active_provider_succeeds() -> None:
    providers = _install(
        stitch=ScriptedDesignProvider("stitch"),
        figma=ScriptedDesignProvider("figma"),
        fake=ScriptedDesignProvider("fake"),
    )
    project = await _project()

    outcome = await invoke_with_fallback(
        project, DesignCapability.from_text, lambda p: p.generate_from_text("x")
    )

    assert outcome.provider_key == "stitch"
    assert outcome.fell_back is False
    assert outcome.note() is None
    assert providers["figma"].text_calls == 0  # never touched


async def test_a_fatal_error_does_not_fall_back() -> None:
    """A malformed request is the caller's problem; another provider would reject it too."""
    providers = _install(
        stitch=ScriptedDesignProvider("stitch", fail_text="fatal"),
        figma=ScriptedDesignProvider("figma"),
        fake=ScriptedDesignProvider("fake"),
    )
    project = await _project()

    with pytest.raises(ProviderError):
        await invoke_with_fallback(
            project, DesignCapability.from_text, lambda p: p.generate_from_text("x")
        )
    assert providers["figma"].text_calls == 0


async def test_the_last_error_propagates_when_every_provider_fails() -> None:
    _install(
        stitch=ScriptedDesignProvider("stitch", fail_text="quota"),
        figma=ScriptedDesignProvider("figma", fail_text="auth"),
        fake=ScriptedDesignProvider("fake", fail_text="transient"),
    )
    project = await _project()

    with pytest.raises(ProviderError) as exc:
        await invoke_with_fallback(
            project, DesignCapability.from_text, lambda p: p.generate_from_text("x")
        )
    assert exc.value.fallback_hint  # still actionable at the dead end


# ---------------------------------------------------------------- candidate selection


async def test_a_provider_lacking_the_capability_is_skipped() -> None:
    """Figma can't do screenshots, so an image flow must skip straight to fake."""
    _install(
        stitch=ScriptedDesignProvider("stitch", fail_image="transient"),
        figma=ScriptedDesignProvider("figma", from_image=False),
        fake=ScriptedDesignProvider("fake"),
    )
    project = await _project()

    keys = candidate_keys(project, DesignCapability.from_image)
    assert keys == ["stitch", "fake"]  # figma filtered out

    outcome = await invoke_with_fallback(
        project, DesignCapability.from_image, lambda p: p.generate_from_image([], None)
    )
    assert outcome.provider_key == "fake"


async def test_candidates_are_deduped_and_active_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        stitch=ScriptedDesignProvider("stitch"),
        figma=ScriptedDesignProvider("figma"),
        fake=ScriptedDesignProvider("fake"),
    )
    # Active == figma; the chain still lists figma — it must appear once, first.
    monkeypatch.setenv("DESIGN_PROVIDER", "figma")
    monkeypatch.setenv("DESIGN_FALLBACK_CHAIN", "figma,fake,stitch")
    reset_config()
    project = await _project()

    assert candidate_keys(project, DesignCapability.from_text) == ["figma", "fake", "stitch"]


async def test_a_project_override_leads_the_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        stitch=ScriptedDesignProvider("stitch"),
        figma=ScriptedDesignProvider("figma"),
        fake=ScriptedDesignProvider("fake"),
    )
    monkeypatch.setenv("DESIGN_PROVIDER", "stitch")
    monkeypatch.setenv("DESIGN_FALLBACK_CHAIN", "figma,fake")
    reset_config()
    project = await Project(
        user_id=PydanticObjectId(), name="p", app_db_name="db", design_provider="figma"
    ).insert()

    # The per-project override wins the lead spot over the platform default.
    assert candidate_keys(project, DesignCapability.from_text)[0] == "figma"


async def test_disabling_the_chain_leaves_only_the_active_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(stitch=ScriptedDesignProvider("stitch"), fake=ScriptedDesignProvider("fake"))
    monkeypatch.setenv("DESIGN_PROVIDER", "stitch")
    monkeypatch.setenv("DESIGN_FALLBACK_CHAIN", "")
    reset_config()
    project = await _project()

    assert candidate_keys(project, DesignCapability.from_text) == ["stitch"]


# ---------------------------------------------------------------- end-to-end via the conductor


@pytest_asyncio.fixture
async def http() -> AsyncIterator[AsyncClient]:
    from app.api.app import create_app

    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_a_design_intent_falls_back_and_tells_the_user(
    http: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance criterion, end to end: Stitch quota → Figma, with a message to the user."""
    monkeypatch.setenv("DESIGN_PROVIDER", "stitch")
    monkeypatch.setenv("DESIGN_FALLBACK_CHAIN", "figma,fake")
    reset_config()
    _install(
        stitch=ScriptedDesignProvider("stitch", fail_text="quota"),
        figma=ScriptedDesignProvider("figma"),
        fake=ScriptedDesignProvider("fake"),
    )

    token = (
        await http.post("/auth/register", json={"email": "d@e.com", "password": "password123"})
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    project_id = (await http.post("/projects", json={"name": "p"}, headers=headers)).json()["id"]

    resp = await http.post(
        f"/projects/{project_id}/intent",
        json={"stage": "design", "action": "refine", "payload": {"text": "a todo app"}},
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    # An artifact was produced despite Stitch being down...
    assert body["artifacts"], "a design should still have been generated via the fallback"
    # ...and the assistant reply names the substitution.
    assistant = " ".join(m["content"] for m in body["messages"] if m["role"] == "assistant")
    assert "figma" in assistant.lower()
    assert "quota" in assistant.lower()
