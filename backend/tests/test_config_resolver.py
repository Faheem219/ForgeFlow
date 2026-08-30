from __future__ import annotations

import pytest

from app.core.config import MISSING, ConfigResolver, Settings
from app.core.errors import SystemError


def _resolver() -> ConfigResolver:
    # `_env_file=None` keeps tests deterministic (env + defaults only, no local .env).
    return ConfigResolver(settings=Settings(_env_file=None))  # type: ignore[call-arg]


def test_default_is_returned_when_no_override() -> None:
    resolver = _resolver()
    assert resolver.get("forgeflow_env") == "local"
    assert resolver.source_of("forgeflow_env") == "default"


def test_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://example:27017/db")
    resolver = _resolver()
    assert resolver.get("mongodb_uri") == "mongodb://example:27017/db"
    assert resolver.source_of("mongodb_uri") == "env"


def test_unknown_key_raises_system_error() -> None:
    resolver = _resolver()
    with pytest.raises(SystemError):
        resolver.get("does_not_exist")
    with pytest.raises(SystemError):
        resolver.source_of("does_not_exist")


def test_cast_is_applied() -> None:
    resolver = _resolver()
    assert resolver.get("repair_max_iterations", int) == 5
    assert resolver.get("repair_max_iterations", str) == "5"


class _FakeProvider:
    """Stand-in for the phase-51 DB/admin provider to prove the precedence seam."""

    def __init__(self, values: dict[str, str]) -> None:
        self.name = "fake-db"
        self._values = values

    def get(self, key: str) -> object:
        return self._values.get(key, MISSING)


def test_provider_wins_over_env_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_CODEGEN", "from-env")
    resolver = _resolver()
    # Sanity: without a provider, env wins over default.
    assert resolver.get("model_codegen") == "from-env"

    resolver.add_provider(_FakeProvider({"model_codegen": "from-admin"}))

    # A higher-priority provider (admin panel > env > default) now wins.
    assert resolver.get("model_codegen") == "from-admin"
    assert resolver.source_of("model_codegen") == "fake-db"

    # Keys the provider does not supply still fall through to env/default.
    assert resolver.get("forgeflow_env") == "local"
    assert resolver.source_of("forgeflow_env") == "default"


# --- blank budget caps ------------------------------------------------------------------------
# `.env.example` ships the caps blank, and docker-compose's `env_file` passes a blank key through as
# a real empty-string env var (it does not omit it). A `float | None` would choke on `""`, so a
# validator remaps blank -> None (uncapped). Regression guard for the `make dev` boot crash.


@pytest.mark.parametrize("field", ["BUDGET_CAP_INR_PER_PROJECT", "BUDGET_CAP_INR_GLOBAL"])
def test_blank_budget_cap_env_means_uncapped(field: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(field, "")  # exactly what docker-compose injects for a blank key
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert getattr(settings, field.lower()) is None


def test_numeric_budget_cap_still_parses() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        # A string is the point: the env always hands Pydantic one, and this pins the coercion.
        budget_cap_inr_per_project="100",  # type: ignore[arg-type]
    )
    assert settings.budget_cap_inr_per_project == 100.0


def test_a_non_numeric_cap_still_fails_loudly() -> None:
    """The blank exemption must not swallow a genuinely wrong value (e.g. a stray comment)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            budget_cap_inr_global="# blank = uncapped",  # type: ignore[arg-type]
        )
