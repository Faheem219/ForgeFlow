"""Write validation: bad values rejected with detail; sensitive keys protected; flags surfaced."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.config_admin import ConfigAdminService
from app.core.config_db import DbSettingProvider
from app.core.config_registry import REGISTRY, coerce_and_validate
from app.core.errors import UserError

pytestmark = pytest.mark.usefixtures("mongo_db")


# ---------------------------------------------------------------- pure validation


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(UserError, match="not an admin-editable setting"):
        coerce_and_validate("totally_made_up_key", "x")


def test_an_int_field_rejects_a_non_number() -> None:
    with pytest.raises(UserError, match="valid int"):
        coerce_and_validate("repair_max_iterations", "not-a-number")


def test_a_number_out_of_range_is_rejected() -> None:
    with pytest.raises(UserError, match="≤"):
        coerce_and_validate("repair_max_iterations", 999)
    with pytest.raises(UserError, match="≥"):
        coerce_and_validate("repair_max_iterations", 0)


def test_an_enum_field_rejects_a_value_not_in_the_set() -> None:
    with pytest.raises(UserError, match="one of"):
        coerce_and_validate("design_provider", "sketch")
    # ...and accepts a valid one, unchanged.
    assert coerce_and_validate("design_provider", "figma") == "figma"


def test_a_bool_field_coerces_common_spellings() -> None:
    assert coerce_and_validate("log_json", "false") is False
    assert coerce_and_validate("log_json", "on") is True
    with pytest.raises(UserError, match="valid bool"):
        coerce_and_validate("log_json", "maybe")


def test_int_coercion_returns_a_real_int() -> None:
    value = coerce_and_validate("repair_max_iterations", "3")
    assert value == 3 and isinstance(value, int)


def test_a_nullable_budget_accepts_none_but_rejects_non_positive() -> None:
    assert coerce_and_validate("budget_cap_inr_per_project", None) is None  # "uncapped"
    with pytest.raises(UserError, match="greater than zero"):
        coerce_and_validate("budget_cap_inr_per_project", -5)


def test_a_non_nullable_key_rejects_none() -> None:
    with pytest.raises(UserError, match="must not be empty"):
        coerce_and_validate("repair_max_iterations", None)


def test_model_pricing_json_must_be_a_json_object() -> None:
    assert coerce_and_validate("model_pricing_json", "") == ""  # blank = built-in defaults
    assert coerce_and_validate("model_pricing_json", '{"m":{"input":1}}')
    with pytest.raises(UserError, match="valid JSON"):
        coerce_and_validate("model_pricing_json", "{not json")
    with pytest.raises(UserError, match="JSON object"):
        coerce_and_validate("model_pricing_json", "[1, 2, 3]")


# ---------------------------------------------------------------- sensitive keys


def test_a_sensitive_key_cannot_be_written_as_plain_config() -> None:
    with pytest.raises(UserError, match="vault"):
        coerce_and_validate("anthropic_api_key", "sk-ant-whatever")


async def test_setting_a_sensitive_key_via_the_service_is_refused(
    db_provider: DbSettingProvider,
) -> None:
    with pytest.raises(UserError) as exc:
        await ConfigAdminService().set("anthropic_api_key", "sk-ant-x", admin_id=None)
    assert exc.value.detail == {"key": "anthropic_api_key", "sensitive": True}


# ---------------------------------------------------------------- restart-required flag


def test_restart_required_keys_exist_and_are_flagged() -> None:
    """The dashboard needs to warn on keys that only take effect for new sandboxes / on restart."""
    restart_keys = {k for k, entry in REGISTRY.items() if entry.restart_required}
    assert "sandbox_cpu_limit" in restart_keys
    assert "log_json" in restart_keys
    # A call-time key must NOT be flagged.
    assert REGISTRY["model_routing"].restart_required is False


async def test_the_api_surfaces_the_restart_required_flag(
    admin_client: tuple[AsyncClient, dict[str, str]],
) -> None:
    http, headers = admin_client
    resp = await http.get("/admin/config/sandbox_cpu_limit", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["restart_required"] is True


async def test_the_api_rejects_an_invalid_write_with_detail(
    admin_client: tuple[AsyncClient, dict[str, str]],
) -> None:
    http, headers = admin_client
    resp = await http.put(
        "/admin/config/repair_max_iterations", json={"value": 999}, headers=headers
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "user_error"
