"""The registry covers the **whole** environment.

The user directive is that *every* env setting is configurable from the admin panel, so this is the
guard that keeps it true: adding a field to ``Settings`` without a registry entry (or an
``.env.example`` fallback) fails the build rather than quietly creating a setting only reachable by
editing env files.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings
from app.core.config_admin import _label
from app.core.config_registry import REGISTRY
from app.db.models.enums import SettingCategory

ENV_EXAMPLE = Path(__file__).resolve().parents[3] / ".env.example"


def test_every_settings_field_is_admin_editable() -> None:
    missing = sorted(set(Settings.model_fields) - set(REGISTRY))
    assert not missing, f"Settings fields with no admin registry entry: {missing}"


def test_the_registry_has_no_keys_the_code_does_not_read() -> None:
    stray = sorted(set(REGISTRY) - set(Settings.model_fields))
    assert not stray, f"registry keys that are not Settings fields: {stray}"


def test_the_phase_53_provider_keys_are_admin_editable() -> None:
    """The OpenAI-compatible provider (phase-53) is switchable from the panel, keys included."""
    for key in ("llm_provider", "openai_api_key", "openai_base_url", "openai_max_retries"):
        assert key in REGISTRY
    assert REGISTRY["llm_provider"].choices == ("anthropic", "openai")
    assert REGISTRY["openai_api_key"].sensitive is True
    assert REGISTRY["openai_api_key"].locked is False  # settable, unlike the bootstrap keys


def test_every_key_carries_the_metadata_the_dashboard_renders() -> None:
    for key, entry in REGISTRY.items():
        assert entry.description.strip(), f"{key} has no description"
        assert entry.type in ("str", "int", "float", "bool", "enum", "json"), key
        assert entry.env_var == key.upper()
        assert _label(key).strip(), f"{key} produces an empty label"
        if entry.type == "enum":
            assert entry.choices, f"{key} is an enum with no choices"
        if entry.locked:
            assert entry.locked_reason.strip(), f"{key} is locked without a reason"


def test_every_category_is_populated() -> None:
    """An empty section would render as a dead link in the dashboard nav."""
    used = {entry.category for entry in REGISTRY.values()}
    assert used == set(SettingCategory)


def test_env_example_documents_every_key() -> None:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    declared = {m.group(1) for m in re.finditer(r"^([A-Z0-9_]+)=", text, re.M)}
    missing = sorted(e.env_var for e in REGISTRY.values() if e.env_var not in declared)
    assert not missing, f"registry keys with no .env.example fallback: {missing}"


def test_a_defaults_only_view_reports_the_code_default_not_the_env_value() -> None:
    """`default` must be the *code* default — the layer beneath env — or the dashboard would show
    the env value in both the 'value' and 'default' columns and hide the real fallback."""
    assert REGISTRY["repair_max_iterations"].default() == 5
    assert REGISTRY["budget_cap_inr_per_project"].default() is None
