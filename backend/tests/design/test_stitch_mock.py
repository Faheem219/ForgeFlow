"""Stitch provider against a mocked MCP transport: the four flows + result→artifact + health."""

from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from beanie import PydanticObjectId

from app.core.errors import ProviderError
from app.db.models import Project
from app.db.models.enums import ArtifactType, Stage
from app.design.base import DesignImage, DesignScreenRef, ProviderHealth
from app.design.service import META_EXTERNAL_REF, META_PROVIDER, DesignService
from app.design.stitch import StitchDesignPayload, StitchDesignProvider
from app.design.stitch_auth import StitchAuth
from app.orchestrator.artifacts import ArtifactService

pytestmark = pytest.mark.usefixtures("mongo_db")


class FakeStitchClient:
    """Canned MCP transport that records the headers it was handed (to prove auth is wired)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.headers: list[dict[str, str]] = []
        #: Which container each generate was pointed at (None = "make a fresh one").
        self.workspaces: list[str | None] = []
        self.titles: list[str | None] = []

    async def text_to_ui(
        self,
        prompt: str,
        *,
        headers: Mapping[str, str],
        workspace: str | None = None,
        title: str | None = None,
    ) -> StitchDesignPayload:
        self.calls.append("text_to_ui")
        self.headers.append(dict(headers))
        self.workspaces.append(workspace)
        self.titles.append(title)
        return StitchDesignPayload(
            workspace=workspace or "proj-1",
            external_ref="proj-1/stitch-abc",
            html=f"<main>{prompt}</main>",
            css="main{color:red}",
            preview_image="https://stitch.example/p.png",
            meta={"stitch_project": "proj-1"},
        )

    async def refine(
        self, external_ref: str, instruction: str, *, headers: Mapping[str, str]
    ) -> StitchDesignPayload:
        self.calls.append("refine")
        self.headers.append(dict(headers))
        return StitchDesignPayload(
            external_ref=f"{external_ref}-r1", html=f"<main>{instruction}</main>", css=""
        )

    async def fetch_code(
        self, external_ref: str, *, headers: Mapping[str, str]
    ) -> StitchDesignPayload:
        self.calls.append("fetch_code")
        self.headers.append(dict(headers))
        return StitchDesignPayload(html="<main/>", css="body{}", assets={"logo": "ref://logo"})

    async def list_screens(
        self, *, headers: Mapping[str, str], workspace: str | None = None
    ) -> list[DesignScreenRef]:
        self.calls.append("list_screens")
        self.headers.append(dict(headers))
        self.workspaces.append(workspace)
        return [DesignScreenRef(ref=f"{workspace or 'proj-1'}/stitch-abc", title="Landing")]


@pytest.fixture
def with_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import reset_config

    monkeypatch.setenv("STITCH_CLIENT_ID", "id")
    monkeypatch.setenv("STITCH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("STITCH_TOKEN_URL", "https://stitch.example/token")
    monkeypatch.setenv("STITCH_MCP_URL", "https://stitch.example/mcp")
    reset_config()


def _provider(client: FakeStitchClient) -> StitchDesignProvider:
    async def fetch_token() -> tuple[str, float]:
        return "access-xyz", 3600.0

    return StitchDesignProvider(client=client, auth=StitchAuth(token_fetcher=fetch_token))


async def test_text_to_ui_maps_to_design_result(with_creds: None) -> None:
    client = FakeStitchClient()
    result = await _provider(client).generate_from_text("a todo app")

    assert client.calls == ["text_to_ui"]
    # The resolved credential reached the transport as a header, never as a bare token.
    assert client.headers == [{"Authorization": "Bearer access-xyz"}]
    assert result.provider == "stitch"
    assert result.external_ref == "proj-1/stitch-abc"
    assert "a todo app" in result.html
    assert result.preview_image == "https://stitch.example/p.png"
    assert result.meta["source"] == "text"


async def test_screenshot_intake_is_refused_as_unsupported(with_creds: None) -> None:
    """Stitch's API is text-prompt only, so images degrade to the fallback chain rather than
    being silently dropped into a text generation."""
    provider = _provider(FakeStitchClient())
    assert provider.capabilities().from_image is False

    images = [DesignImage(filename="a.png", media_type="image/png", data=b"\x89PNG")]
    with pytest.raises(ProviderError) as exc:
        await provider.generate_from_image(images, prompt="match this")
    assert exc.value.detail == {"kind": "unsupported"}


async def test_refine_and_fetch_code(with_creds: None) -> None:
    client = FakeStitchClient()
    provider = _provider(client)

    refined = await provider.refine("proj-1/stitch-abc", "make it dark")
    assert refined.external_ref == "proj-1/stitch-abc-r1"
    assert "make it dark" in refined.html

    code = await provider.fetch_code("proj-1/stitch-abc")
    assert code.assets == {"logo": "ref://logo"}
    assert "fetch_code" in client.calls


async def test_result_persists_as_a_design_artifact(with_creds: None) -> None:
    project = await Project(user_id=PydanticObjectId(), name="p", app_db_name="db").insert()
    assert project.id is not None

    result = await _provider(FakeStitchClient()).generate_from_text("landing page")
    artifact = await DesignService().save_result(project.id, result, source="text")

    assert artifact.type is ArtifactType.design
    assert artifact.stage is Stage.design
    assert artifact.meta[META_PROVIDER] == "stitch"
    assert artifact.meta[META_EXTERNAL_REF] == "proj-1/stitch-abc"

    payload = await ArtifactService().get_content(artifact)
    assert payload is not None
    assert "landing page" in json.loads(payload)["html"]

    # The bearer token must never be persisted anywhere on the artifact.
    assert "access-xyz" not in json.dumps({"meta": artifact.meta, "payload": payload})


async def test_health_ok_with_creds_and_available_quota(with_creds: None) -> None:
    assert await _provider(FakeStitchClient()).health() is ProviderHealth.ok


async def test_health_down_without_credentials() -> None:
    # No STITCH_* creds → the provider self-reports down (stage falls back) rather than raising.
    provider = StitchDesignProvider(client=FakeStitchClient())
    assert await provider.health() is ProviderHealth.down

    with pytest.raises(ProviderError):
        await provider.generate_from_text("x")  # generation also refuses without creds
