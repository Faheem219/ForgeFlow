"""Secrets are settable from the admin panel, but only ever *write-only* (§7).

The directive is that every env setting — API keys included — is configurable from the dashboard.
The security contract that makes that acceptable: the value is Fernet-encrypted before it reaches
Mongo, decrypted only on the read that needs it, and never returned to a client or written to the
audit log in the clear.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient

from app.core.config import get_config, reset_config
from app.core.config_admin import MASK, ConfigAdminService
from app.core.config_db import DbSettingProvider, install_db_provider
from app.db.models import ConfigAudit, PlatformSetting
from app.deploy.secrets import reset_vault

pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest.fixture
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("FERNET_KEY", key)
    reset_config()
    reset_vault()
    yield key
    reset_vault()


async def test_a_secret_is_stored_encrypted_and_resolves_in_plaintext(fernet_key: str) -> None:
    await install_db_provider()
    await ConfigAdminService().set("openai_api_key", "sk-test-abc123", admin_id=None)

    stored = await PlatformSetting.find_one({"key": "openai_api_key"})
    assert stored is not None
    assert stored.encrypted is True
    assert "sk-test-abc123" not in str(stored.value)  # ciphertext, not the key

    # ...and the consumer (the agent transport) still reads the real value through the resolver.
    assert get_config().get("openai_api_key") == "sk-test-abc123"
    assert get_config().source_of("openai_api_key") == "db"


async def test_a_secret_is_never_read_back_to_a_client(fernet_key: str) -> None:
    await install_db_provider()
    service = ConfigAdminService()
    await service.set("anthropic_api_key", "sk-ant-secret", admin_id=None)

    view = await service.get_one("anthropic_api_key")
    assert view.value == MASK
    assert "sk-ant-secret" not in str(view.model_dump())

    snapshot = await service.effective()
    assert snapshot["anthropic_api_key"] == MASK


async def test_the_audit_records_the_change_but_not_the_secret(fernet_key: str) -> None:
    await install_db_provider()
    service = ConfigAdminService()
    await service.set("figma_token", "figd_supersecret", admin_id=None)
    await service.set("figma_token", "figd_rotated", admin_id=None)

    docs = await ConfigAudit.find({"key": "figma_token"}).to_list()
    assert len(docs) == 2
    for doc in docs:
        assert "figd_" not in str(doc.before) + str(doc.after)  # masked on both sides
    latest = await service.list_audit()
    assert latest[0].after == MASK


async def test_deleting_a_secret_override_reverts_to_env(
    fernet_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    reset_config()
    await install_db_provider()
    service = ConfigAdminService()

    await service.set("openai_api_key", "sk-from-admin", admin_id=None)
    assert get_config().get("openai_api_key") == "sk-from-admin"

    await service.delete("openai_api_key", admin_id=None)
    assert get_config().get("openai_api_key") == "sk-from-env"


async def test_an_undecryptable_secret_falls_back_instead_of_failing(
    fernet_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rotating FERNET_KEY must read as 'the override is unusable', not as a 500 on every call."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    reset_config()
    provider = await install_db_provider()
    await ConfigAdminService().set("openai_api_key", "sk-from-admin", admin_id=None)

    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode("ascii"))
    reset_config()
    reset_vault()
    get_config().add_provider(provider)

    assert get_config().get("openai_api_key") == "sk-from-env"


async def test_a_locked_key_in_the_database_is_ignored_by_the_resolver(
    db_provider: DbSettingProvider,
) -> None:
    """Defence in depth: the API refuses to write a bootstrap key, and a hand-inserted row for one
    is skipped when the cache loads, so it can never shadow the environment."""
    await PlatformSetting(key="fernet_key", value="tampered", category="core").insert()
    await db_provider.load()

    assert db_provider.is_overridden("fernet_key") is False
    assert get_config().source_of("fernet_key") != "db"


async def test_the_api_rejects_writing_a_locked_key(
    admin_client: tuple[AsyncClient, dict[str, str]],
) -> None:
    http, headers = admin_client
    resp = await http.put(
        "/admin/config/mongodb_uri", json={"value": "mongodb://evil"}, headers=headers
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["detail"]["locked"] is True


async def test_the_catalog_shows_a_locked_key_read_only_with_a_reason(
    admin_client: tuple[AsyncClient, dict[str, str]],
) -> None:
    http, headers = admin_client
    resp = await http.get("/admin/config/fernet_key", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["locked"] is True
    assert body["locked_reason"]
    assert body["sensitive"] is True
