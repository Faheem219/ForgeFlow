"""Figma provider against a mocked MCP transport: supported ops → result/artifact + health."""

from __future__ import annotations

import json

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.core.errors import ProviderError
from app.db.models import Project
from app.db.models.enums import ArtifactType, Stage
from app.design.base import ProviderHealth
from app.design.figma import FigmaDesignPayload, FigmaDesignProvider
from app.design.service import META_EXTERNAL_REF, META_PROVIDER, DesignService
from app.orchestrator.artifacts import ArtifactService

pytestmark = pytest.mark.usefixtures("mongo_db")


class FakeFigmaClient:
    """Canned MCP transport that records the token it was handed (proves auth is wired)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.tokens: list[str] = []

    async def text_to_ui(self, prompt: str, *, token: str) -> FigmaDesignPayload:
        self.calls.append("text_to_ui")
        self.tokens.append(token)
        return FigmaDesignPayload(
            external_ref="figma-file-1",
            html=f"<section>{prompt}</section>",
            css="section{padding:1rem}",
            preview_image="https://figma.example/p.png",
            meta={"figma_node": "1:23"},
        )

    async def refine(
        self, external_ref: str, instruction: str, *, token: str
    ) -> FigmaDesignPayload:
        self.calls.append("refine")
        self.tokens.append(token)
        return FigmaDesignPayload(
            external_ref=f"{external_ref}-r1", html=f"<section>{instruction}</section>", css=""
        )

    async def fetch_code(self, external_ref: str, *, token: str) -> FigmaDesignPayload:
        self.calls.append("fetch_code")
        self.tokens.append(token)
        return FigmaDesignPayload(html="<section/>", css="body{}", assets={"logo": "ref://logo"})


@pytest.fixture
def with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIGMA_TOKEN", "figma-secret-token")
    monkeypatch.setenv("FIGMA_MCP_URL", "https://figma.example/mcp")
    reset_config()


async def test_text_to_ui_maps_to_design_result(with_token: None) -> None:
    client = FakeFigmaClient()
    result = await FigmaDesignProvider(client=client).generate_from_text("a dashboard")

    assert client.calls == ["text_to_ui"]
    assert client.tokens == ["figma-secret-token"]  # token reached the transport
    assert result.provider == "figma"
    assert result.external_ref == "figma-file-1"
    assert "a dashboard" in result.html
    assert result.meta["source"] == "text"


async def test_refine_and_fetch_code(with_token: None) -> None:
    client = FakeFigmaClient()
    provider = FigmaDesignProvider(client=client)

    refined = await provider.refine("figma-file-1", "tighten spacing")
    assert refined.external_ref == "figma-file-1-r1"
    assert "tighten spacing" in refined.html

    code = await provider.fetch_code("figma-file-1")
    assert code.assets == {"logo": "ref://logo"}
    assert "fetch_code" in client.calls


async def test_result_persists_as_a_design_artifact(with_token: None) -> None:
    project = await Project(user_id=PydanticObjectId(), name="p", app_db_name="db").insert()
    assert project.id is not None

    result = await FigmaDesignProvider(client=FakeFigmaClient()).generate_from_text("pricing page")
    artifact = await DesignService().save_result(project.id, result, source="text")

    assert artifact.type is ArtifactType.design
    assert artifact.stage is Stage.design
    assert artifact.meta[META_PROVIDER] == "figma"
    assert artifact.meta[META_EXTERNAL_REF] == "figma-file-1"

    payload = await ArtifactService().get_content(artifact)
    assert payload is not None
    assert "pricing page" in json.loads(payload)["html"]
    # The token must never be persisted anywhere on the artifact.
    assert "figma-secret-token" not in json.dumps({"meta": artifact.meta, "payload": payload})


async def test_health_down_without_token_and_refuses_generation() -> None:
    provider = FigmaDesignProvider(client=FakeFigmaClient())
    assert await provider.health() is ProviderHealth.down  # no token configured

    with pytest.raises(ProviderError):
        await provider.generate_from_text("x")  # generation refuses without a token


async def test_health_ok_with_token(with_token: None) -> None:
    assert await FigmaDesignProvider(client=FakeFigmaClient()).health() is ProviderHealth.ok
