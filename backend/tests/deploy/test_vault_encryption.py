"""Vault crypto (phase-34): secrets are ciphertext at rest and only decrypted at call time."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId
from cryptography.fernet import Fernet

from app.core.config import reset_config
from app.core.errors import SystemError, UserError
from app.db.models import Credential
from app.db.models.enums import CredentialKind, CredentialScope
from app.deploy.secrets import SecretVault, reset_vault

pytestmark = pytest.mark.usefixtures("mongo_db")

SECRET = "vercel_live_tok_9f3a8b2c7d1e"


async def test_round_trips_a_stored_secret(fernet_key: str) -> None:
    user_id = PydanticObjectId()
    vault = SecretVault()

    meta = await vault.put_credential(user_id, CredentialKind.vercel, SECRET)
    assert meta.kind is CredentialKind.vercel
    assert meta.scope is CredentialScope.byo
    assert meta.last4 == SECRET[-4:]

    assert await vault.get_credential(user_id, CredentialKind.vercel) == SECRET


async def test_value_is_ciphertext_at_rest(fernet_key: str) -> None:
    user_id = PydanticObjectId()
    await SecretVault().put_credential(user_id, CredentialKind.vercel, SECRET)

    doc = await Credential.find_one({"user_id": user_id, "kind": CredentialKind.vercel})
    assert doc is not None
    # What Mongo holds must not be (or contain) the plaintext.
    assert doc.encrypted_secret != SECRET
    assert SECRET not in doc.encrypted_secret
    # ...and it must be decryptable only with the configured key.
    assert Fernet(fernet_key.encode()).decrypt(doc.encrypted_secret.encode()).decode() == SECRET


async def test_put_replaces_rather_than_duplicating(fernet_key: str) -> None:
    user_id = PydanticObjectId()
    vault = SecretVault()

    await vault.put_credential(user_id, CredentialKind.render, "first-token")
    await vault.put_credential(user_id, CredentialKind.render, "second-token")

    docs = await Credential.find({"user_id": user_id, "kind": CredentialKind.render}).to_list()
    assert len(docs) == 1
    assert await vault.get_credential(user_id, CredentialKind.render) == "second-token"


async def test_list_kinds_exposes_metadata_only(fernet_key: str) -> None:
    user_id = PydanticObjectId()
    vault = SecretVault()
    await vault.put_credential(user_id, CredentialKind.vercel, SECRET)
    await vault.put_credential(user_id, CredentialKind.render, "render-abcd")

    metas = await vault.list_kinds(user_id)
    assert {m.kind for m in metas} == {CredentialKind.vercel, CredentialKind.render}
    # The dataclass has no field that could carry a value — only the last four characters.
    assert {m.last4 for m in metas} == {SECRET[-4:], "abcd"}
    assert not any(SECRET in str(m) for m in metas)


async def test_delete_removes_the_credential(fernet_key: str) -> None:
    user_id = PydanticObjectId()
    vault = SecretVault()
    await vault.put_credential(user_id, CredentialKind.figma, SECRET)

    assert await vault.delete(user_id, CredentialKind.figma) is True
    assert await vault.get_credential(user_id, CredentialKind.figma) is None
    assert await vault.delete(user_id, CredentialKind.figma) is False  # idempotent


async def test_empty_secret_is_rejected(fernet_key: str) -> None:
    with pytest.raises(UserError):
        await SecretVault().put_credential(PydanticObjectId(), CredentialKind.vercel, "   ")


async def test_rotating_the_key_degrades_instead_of_raising(
    fernet_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rotated FERNET_KEY makes old ciphertext unreadable — that must read as 'missing'."""
    user_id = PydanticObjectId()
    await SecretVault().put_credential(user_id, CredentialKind.vercel, SECRET)

    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode("ascii"))
    reset_config()
    reset_vault()

    assert await SecretVault().get_credential(user_id, CredentialKind.vercel) is None


async def test_missing_key_is_a_system_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FERNET_KEY", "")
    reset_config()
    reset_vault()

    with pytest.raises(SystemError):
        await SecretVault().put_credential(PydanticObjectId(), CredentialKind.vercel, SECRET)


async def test_invalid_key_is_a_system_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FERNET_KEY", "not-a-valid-fernet-key")
    reset_config()
    reset_vault()

    with pytest.raises(SystemError):
        await SecretVault().put_credential(PydanticObjectId(), CredentialKind.vercel, SECRET)
