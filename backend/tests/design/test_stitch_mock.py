"""Stitch provider against a mocked MCP transport: the four flows + result→artifact + health."""

from __future__ import annotations

import json

import pytest
from beanie import PydanticObjectId

from app.core.errors import ProviderError
from app.db.models import Project
from app.db.models.enums import ArtifactType, Stage
from app.design.base import DesignImage, ProviderHealth
from app.design.service import META_EXTERNAL_REF, META_PROVIDER, DesignService
from app.design.stitch import StitchDesignPayload, StitchDesignProvider
from app.design.stitch_auth import StitchAuth
from app.orchestrator.artifacts import ArtifactService

pytestmark = pytest.mark.usefixtures("mongo_db")


class FakeStitchClient:
    """Canned MCP transport that records the token it was handed (to prove auth is wired)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.tokens: list[str] = []

    async def text_to_ui(self, prompt: str, *, token: str) -> StitchDesignPayload:
        self.calls.append("text_to_ui")
        self.tokens.append(token)
        return StitchDesignPayload(
            external_ref="stitch-abc",
            html=f"<main>{prompt}</main>",
            css="main{color:red}",
            preview_image="https://stitch.example/p.png",
            meta={"stitch_project": "proj-1"},
        )

    async def image_to_ui(
        self, images: list[DesignImage], prompt: str | None, *, token: str
    ) -> StitchDesignPayload:
        self.calls.append("image_to_ui")
        self.tokens.append(token)
        return StitchDesignPayload(
            external_ref="stitch-img", html="<img/>", css="", meta={"n": len(images)}
        )

    async def refine(
        self, external_ref: str, instruction: str, *, token: str
    ) -> StitchDesignPayload:
        self.calls.append("refine")
        self.tokens.append(token)
        return StitchDesignPayload(
            external_ref=f"{external_ref}-r1", html=f"<main>{instruction}</main>", css=""
        )

    async def fetch_code(self, external_ref: str, *, token: str) -> StitchDesignPayload:
        self.calls.append("fetch_code")
        self.tokens.append(token)
        return StitchDesignPayload(html="<main/>", css="body{}", assets={"logo": "ref://logo"})


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
    assert client.tokens == ["access-xyz"]  # the access token reached the transport
    assert result.provider == "stitch"
    assert result.external_ref == "stitch-abc"
    assert "a todo app" in result.html
    assert result.preview_image == "https://stitch.example/p.png"
    assert result.meta["source"] == "text"


async def test_image_to_ui_validates_and_maps(with_creds: None) -> None:
    client = FakeStitchClient()
    provider = _provider(client)
    images = [DesignImage(filename="a.png", media_type="image/png", data=b"\x89PNG")]

    result = await provider.generate_from_image(images, prompt="match this")
    assert result.external_ref == "stitch-img"
    assert result.meta["source"] == "image"

    with pytest.raises(ProviderError):
        await provider.generate_from_image([], prompt=None)  # no images


async def test_refine_and_fetch_code(with_creds: None) -> None:
    client = FakeStitchClient()
    provider = _provider(client)

    refined = await provider.refine("stitch-abc", "make it dark")
    assert refined.external_ref == "stitch-abc-r1"
    assert "make it dark" in refined.html

    code = await provider.fetch_code("stitch-abc")
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
    assert artifact.meta[META_EXTERNAL_REF] == "stitch-abc"

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
