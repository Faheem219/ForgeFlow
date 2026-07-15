"""D9 under test: the SAME design flow runs under fake / figma / stitch with only config differing.

``run_design`` is byte-for-byte identical across providers — proof that switching the active design
backend is a config change, never a stage-handler change.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.db.models import Artifact, Project
from app.design.figma import FigmaDesignProvider
from app.design.registry import register_provider
from app.design.service import META_PROVIDER, DesignService
from app.design.stitch import StitchDesignProvider
from app.design.stitch_auth import StitchAuth
from tests.design.test_figma_mock import FakeFigmaClient
from tests.design.test_stitch_mock import FakeStitchClient

pytestmark = pytest.mark.usefixtures("mongo_db")


async def run_design(project: Project, prompt: str) -> Artifact:
    """The provider-agnostic call site. Identical for every provider — that is the whole point."""
    return await DesignService().generate_from_text(project, prompt)


async def _project() -> Project:
    # design_provider=None → the project follows the global DESIGN_PROVIDER config.
    return await Project(
        user_id=PydanticObjectId(), name="p", app_db_name="db", design_provider=None
    ).insert()


def _use_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESIGN_PROVIDER", "fake")
    reset_config()


def _use_figma(monkeypatch: pytest.MonkeyPatch) -> None:
    register_provider(FigmaDesignProvider(client=FakeFigmaClient()))
    monkeypatch.setenv("FIGMA_TOKEN", "figma-token")
    monkeypatch.setenv("FIGMA_MCP_URL", "https://figma.example/mcp")
    monkeypatch.setenv("DESIGN_PROVIDER", "figma")
    reset_config()


def _use_stitch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch_token() -> tuple[str, float]:
        return "access-xyz", 3600.0

    register_provider(
        StitchDesignProvider(client=FakeStitchClient(), auth=StitchAuth(token_fetcher=fetch_token))
    )
    monkeypatch.setenv("STITCH_CLIENT_ID", "id")
    monkeypatch.setenv("STITCH_CLIENT_SECRET", "secret")
    monkeypatch.setenv("STITCH_TOKEN_URL", "https://stitch.example/token")
    monkeypatch.setenv("STITCH_MCP_URL", "https://stitch.example/mcp")
    monkeypatch.setenv("DESIGN_PROVIDER", "stitch")
    reset_config()


async def test_same_flow_runs_under_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_fake(monkeypatch)
    artifact = await run_design(await _project(), "a todo app")
    assert artifact.meta[META_PROVIDER] == "fake"


async def test_same_flow_runs_under_figma(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_figma(monkeypatch)
    artifact = await run_design(await _project(), "a todo app")
    assert artifact.meta[META_PROVIDER] == "figma"


async def test_same_flow_runs_under_stitch(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_stitch(monkeypatch)
    artifact = await run_design(await _project(), "a todo app")
    assert artifact.meta[META_PROVIDER] == "stitch"


async def test_flipping_provider_is_config_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact same project + call site yields different providers purely by flipping config."""
    project = await _project()

    _use_figma(monkeypatch)
    figma_artifact = await run_design(project, "same idea")
    assert figma_artifact.meta[META_PROVIDER] == "figma"

    _use_stitch(monkeypatch)
    stitch_artifact = await run_design(project, "same idea")
    assert stitch_artifact.meta[META_PROVIDER] == "stitch"

    # Versioned, not overwritten: the figma design (v1) survives the switch to stitch (v2).
    assert (figma_artifact.version, stitch_artifact.version) == (1, 2)
