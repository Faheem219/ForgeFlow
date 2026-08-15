"""Provider selection: config-driven, per-project override, unknown keys rejected.

This is the D9 guarantee under test — swapping providers must be a *config* change, never a code
change in any consumer.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.core.errors import UserError
from app.db.models import Project
from app.design.base import (
    DesignCapabilities,
    DesignCode,
    DesignImage,
    DesignResult,
    ProviderHealth,
)
from app.design.registry import (
    available_providers,
    get_active,
    get_provider,
    register_provider,
    resolve_active_key,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


class StubProvider:
    """A second provider so selection is observable (stands in for stitch/figma)."""

    def __init__(self, key: str) -> None:
        self.key = key

    async def generate_from_text(
        self,
        prompt: str,
        *,
        workspace: str | None = None,
        workspace_title: str | None = None,
    ) -> DesignResult:
        return DesignResult(provider=self.key, external_ref="x", html="<h1/>", css="")

    async def generate_from_image(
        self,
        images: list[DesignImage],
        prompt: str | None = None,
        *,
        workspace: str | None = None,
        workspace_title: str | None = None,
    ) -> DesignResult:
        return DesignResult(provider=self.key, external_ref="x", html="<h1/>", css="")

    async def refine(self, design_ref: str, instruction: str) -> DesignResult:
        return DesignResult(provider=self.key, external_ref="x", html="<h1/>", css="")

    async def fetch_code(self, design_ref: str) -> DesignCode:
        return DesignCode(html="<h1/>", css="")

    def capabilities(self) -> DesignCapabilities:
        return DesignCapabilities(provider=self.key)

    async def health(self) -> ProviderHealth:
        return ProviderHealth.ok


async def _project(design_provider: str | None = None) -> Project:
    project = Project(
        user_id=PydanticObjectId(),
        name="p",
        app_db_name="db",
        design_provider=design_provider,
    )
    return await project.insert()


def test_fake_is_registered_out_of_the_box() -> None:
    assert "fake" in available_providers()
    assert get_provider("fake").key == "fake"


def test_unknown_provider_key_is_rejected_with_the_valid_options() -> None:
    with pytest.raises(UserError) as excinfo:
        get_provider("does-not-exist")
    assert "fake" in str(excinfo.value)  # the message lists what *is* available


def test_active_provider_follows_config(monkeypatch: pytest.MonkeyPatch) -> None:
    register_provider(StubProvider("stitch"))

    monkeypatch.setenv("DESIGN_PROVIDER", "fake")
    reset_config()
    assert get_active().key == "fake"

    # Switching providers is a config change only — no consumer code changes.
    monkeypatch.setenv("DESIGN_PROVIDER", "stitch")
    reset_config()
    assert get_active().key == "stitch"


def test_configured_but_unregistered_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESIGN_PROVIDER", "not-registered-yet")
    reset_config()
    with pytest.raises(UserError):
        get_active()


async def test_project_override_wins_over_config(monkeypatch: pytest.MonkeyPatch) -> None:
    register_provider(StubProvider("figma"))
    monkeypatch.setenv("DESIGN_PROVIDER", "fake")
    reset_config()

    # D10 fail-soft: one project pins figma while the platform default stays fake.
    pinned = await _project(design_provider="figma")
    assert get_active(pinned).key == "figma"
    assert resolve_active_key(pinned) == "figma"

    # Everyone else is unaffected.
    plain = await _project()
    assert get_active(plain).key == "fake"
    assert get_active().key == "fake"


async def test_empty_override_falls_back_to_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESIGN_PROVIDER", "fake")
    reset_config()
    project = await _project(design_provider=None)
    assert resolve_active_key(project) == "fake"


def test_register_provider_replaces_by_key() -> None:
    first = StubProvider("dup")
    second = StubProvider("dup")
    register_provider(first)
    register_provider(second)
    assert get_provider("dup") is second
    assert available_providers().count("dup") == 1
