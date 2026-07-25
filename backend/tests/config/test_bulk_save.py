"""Bulk save: a whole dashboard section is written in one request, per-key.

A section can hold dozens of edits. One rejected value must not discard the others, and the operator
has to be told exactly which key failed and why — otherwise a "Save all" is unsafe to use.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config import get_config
from app.core.config_admin import ConfigAdminService
from app.core.config_db import DbSettingProvider

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_a_bulk_save_applies_every_valid_key(db_provider: DbSettingProvider) -> None:
    result = await ConfigAdminService().set_many(
        {"model_routing": "claude-haiku-4-5-20251001", "repair_max_iterations": 7},
        admin_id=None,
    )

    assert result.errors == {}
    assert {v.key for v in result.updated} == {"model_routing", "repair_max_iterations"}
    assert get_config().get("repair_max_iterations") == 7


async def test_one_bad_value_does_not_discard_the_rest(db_provider: DbSettingProvider) -> None:
    result = await ConfigAdminService().set_many(
        {"repair_max_iterations": 999, "repair_stall_threshold": 3},
        admin_id=None,
    )

    assert "repair_max_iterations" in result.errors
    assert "≤ 20" in result.errors["repair_max_iterations"]
    assert get_config().get("repair_stall_threshold") == 3  # the good one still landed
    assert get_config().source_of("repair_max_iterations") != "db"


async def test_the_bulk_route_reports_results_per_key(
    admin_client: tuple[AsyncClient, dict[str, str]],
) -> None:
    http, headers = admin_client
    resp = await http.put(
        "/admin/config",
        json={"updates": {"llm_provider": "openai", "design_provider": "sketch"}},
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert [v["key"] for v in body["updated"]] == ["llm_provider"]
    assert "design_provider" in body["errors"]


async def test_the_bulk_route_is_admin_only(http: AsyncClient) -> None:
    resp = await http.put("/admin/config", json={"updates": {"llm_provider": "openai"}})
    assert resp.status_code == 401
