"""Precedence: admin(DB) > env > default; delete reverts (phase-51 core acceptance)."""

from __future__ import annotations

import pytest

from app.core.config import get_config, reset_config
from app.core.config_admin import ConfigAdminService
from app.core.config_db import DbSettingProvider, install_db_provider

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_default_when_nothing_overrides(db_provider: DbSettingProvider) -> None:
    # No env, no DB row → the code default from Settings.
    assert get_config().get("repair_max_iterations") == 5
    assert get_config().source_of("repair_max_iterations") == "default"


async def test_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPAIR_MAX_ITERATIONS", "7")
    reset_config()
    await install_db_provider()  # re-attach to the fresh resolver

    assert get_config().get("repair_max_iterations") == 7
    assert get_config().source_of("repair_max_iterations") == "env"


async def test_db_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPAIR_MAX_ITERATIONS", "7")
    reset_config()
    await install_db_provider()

    await ConfigAdminService().set("repair_max_iterations", 3, admin_id=None)

    assert get_config().get("repair_max_iterations") == 3  # DB wins over env
    assert get_config().source_of("repair_max_iterations") == "db"


async def test_delete_reverts_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPAIR_MAX_ITERATIONS", "7")
    reset_config()
    await install_db_provider()
    service = ConfigAdminService()

    await service.set("repair_max_iterations", 3, admin_id=None)
    assert get_config().get("repair_max_iterations") == 3

    await service.delete("repair_max_iterations", admin_id=None)

    assert get_config().get("repair_max_iterations") == 7  # back to env
    assert get_config().source_of("repair_max_iterations") == "env"


async def test_delete_reverts_all_the_way_to_default(db_provider: DbSettingProvider) -> None:
    service = ConfigAdminService()
    await service.set("repair_max_iterations", 9, admin_id=None)
    assert get_config().source_of("repair_max_iterations") == "db"

    await service.delete("repair_max_iterations", admin_id=None)

    assert get_config().get("repair_max_iterations") == 5  # the Settings default
    assert get_config().source_of("repair_max_iterations") == "default"


async def test_a_stored_value_keeps_its_type(db_provider: DbSettingProvider) -> None:
    """The DB layer must serve a typed value, not a string, so consumers don't re-parse."""
    await ConfigAdminService().set("repair_max_iterations", "4", admin_id=None)  # string input
    value = get_config().get("repair_max_iterations")
    assert value == 4 and isinstance(value, int)
