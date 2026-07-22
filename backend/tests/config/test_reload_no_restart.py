"""A write is visible immediately, no restart — the cache is invalidated on write (phase-51)."""

from __future__ import annotations

import pytest

from app.agents.models import TaskKind, route
from app.core.config import get_config, reset_config
from app.core.config_admin import ConfigAdminService
from app.core.config_db import DbSettingProvider, install_db_provider

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_a_write_is_reflected_by_the_resolver_immediately(
    db_provider: DbSettingProvider,
) -> None:
    service = ConfigAdminService()
    assert get_config().get("model_routing") == "claude-haiku-4-5-20251001"  # default

    await service.set("model_routing", "claude-haiku-next", admin_id=None)

    # Same process, no restart — the very next read sees it.
    assert get_config().get("model_routing") == "claude-haiku-next"


async def test_the_model_router_uses_the_new_id_live(db_provider: DbSettingProvider) -> None:
    """The consumer that actually matters: routing must resolve to the freshly-set model."""
    assert route(TaskKind.summarize) == get_config().get("model_routing")

    await ConfigAdminService().set("model_routing", "claude-haiku-swapped", admin_id=None)

    assert route(TaskKind.summarize) == "claude-haiku-swapped"
    # Codegen still resolves independently — the two model keys don't bleed into each other.
    assert route(TaskKind.codegen) == get_config().get("model_codegen")


async def test_a_write_survives_a_provider_reload(db_provider: DbSettingProvider) -> None:
    """The override is persisted, not just cached — a fresh load from Mongo still sees it."""
    await ConfigAdminService().set("design_provider", "figma", admin_id=None)

    # Simulate a new process: fresh resolver + a fresh provider load from the DB.
    reset_config()
    await install_db_provider()

    assert get_config().get("design_provider") == "figma"
    assert get_config().source_of("design_provider") == "db"


async def test_delete_is_also_immediate(db_provider: DbSettingProvider) -> None:
    service = ConfigAdminService()
    await service.set("repair_stall_threshold", 4, admin_id=None)
    assert get_config().get("repair_stall_threshold") == 4

    await service.delete("repair_stall_threshold", admin_id=None)
    assert get_config().get("repair_stall_threshold") == 2  # default, immediately
