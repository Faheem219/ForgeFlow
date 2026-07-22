"""Fail-soft: if the DB layer is down, config still resolves from env/default (phase-51, §7)."""

from __future__ import annotations

import pytest

from app.core.config import MISSING, get_config, reset_config
from app.core.config_db import DbSettingProvider, install_db_provider

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_load_never_raises_when_the_db_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DbSettingProvider()

    async def boom() -> list[object]:
        raise RuntimeError("mongo is down")

    # Make the repo's read explode; load() must swallow it and keep serving.
    monkeypatch.setattr("app.db.repos.PlatformSettingRepo.all", lambda self: boom())

    await provider.load()  # must not raise
    assert provider.get("model_routing") is MISSING  # empty cache → resolver falls through


async def test_config_still_resolves_from_env_when_the_db_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_ROUTING", "claude-from-env")
    reset_config()

    async def boom() -> list[object]:
        raise RuntimeError("mongo is down")

    monkeypatch.setattr("app.db.repos.PlatformSettingRepo.all", lambda self: boom())
    await install_db_provider()  # loads against a broken DB — degrades, does not crash

    assert get_config().get("model_routing") == "claude-from-env"
    assert get_config().source_of("model_routing") == "env"


async def test_a_failed_reload_keeps_the_previous_cache(
    db_provider: DbSettingProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient DB blip must not wipe good, already-loaded overrides."""
    from app.core.config_admin import ConfigAdminService

    await ConfigAdminService().set("model_routing", "claude-cached", admin_id=None)
    assert db_provider.get("model_routing") == "claude-cached"

    async def boom() -> list[object]:
        raise RuntimeError("mongo blipped")

    monkeypatch.setattr("app.db.repos.PlatformSettingRepo.all", lambda self: boom())
    await db_provider.load()  # fails internally

    # The last-known-good value is still served — degradation, not data loss.
    assert db_provider.get("model_routing") == "claude-cached"


async def test_install_is_safe_before_the_db_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Startup ordering: installing the provider before Mongo is up must not fail boot."""
    reset_config()

    async def boom() -> list[object]:
        raise RuntimeError("db not ready yet")

    monkeypatch.setattr("app.db.repos.PlatformSettingRepo.all", lambda self: boom())
    provider = await install_db_provider()  # no exception

    assert get_config().has_provider(provider)
    assert get_config().get("model_routing") == "claude-haiku-4-5-20251001"  # default
