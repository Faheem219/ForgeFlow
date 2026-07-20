"""Credential resolution (phase-34, D11): BYO > platform (vault) > platform (config)."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.db.models.enums import CredentialKind, CredentialScope
from app.deploy.secrets import SecretVault, resolve_secret

pytestmark = pytest.mark.usefixtures("mongo_db")

BYO = "byo-vercel-token-1111"
PLATFORM_VAULT = "platform-vercel-token-2222"
PLATFORM_CONFIG = "config-vercel-token-3333"


@pytest.fixture
def platform_config(monkeypatch: pytest.MonkeyPatch, fernet_key: str) -> str:
    monkeypatch.setenv("VERCEL_TOKEN", PLATFORM_CONFIG)
    reset_config()
    return PLATFORM_CONFIG


async def _store(user_id: PydanticObjectId, secret: str, scope: CredentialScope) -> None:
    await SecretVault().put_credential(user_id, CredentialKind.vercel, secret, scope)


async def test_falls_back_to_platform_config_when_the_vault_is_empty(platform_config: str) -> None:
    assert await resolve_secret(CredentialKind.vercel, PydanticObjectId()) == PLATFORM_CONFIG


async def test_platform_vault_entry_beats_config(platform_config: str) -> None:
    await _store(PydanticObjectId(), PLATFORM_VAULT, CredentialScope.platform)

    assert await resolve_secret(CredentialKind.vercel, PydanticObjectId()) == PLATFORM_VAULT


async def test_byo_beats_platform_vault_and_config(platform_config: str) -> None:
    user_id = PydanticObjectId()
    await _store(PydanticObjectId(), PLATFORM_VAULT, CredentialScope.platform)
    await _store(user_id, BYO, CredentialScope.byo)

    assert await resolve_secret(CredentialKind.vercel, user_id) == BYO


async def test_another_users_byo_never_leaks(platform_config: str) -> None:
    owner, other = PydanticObjectId(), PydanticObjectId()
    await _store(owner, BYO, CredentialScope.byo)

    # The other user falls through to the platform value; they never see the owner's token.
    assert await resolve_secret(CredentialKind.vercel, other) == PLATFORM_CONFIG


async def test_anonymous_resolution_ignores_byo_entirely(platform_config: str) -> None:
    await _store(PydanticObjectId(), BYO, CredentialScope.byo)

    # No user context (e.g. a platform-level call path) → BYO is not consulted at all.
    assert await resolve_secret(CredentialKind.vercel) == PLATFORM_CONFIG


async def test_deleting_byo_reverts_to_the_platform_credential(platform_config: str) -> None:
    user_id = PydanticObjectId()
    await _store(user_id, BYO, CredentialScope.byo)
    assert await resolve_secret(CredentialKind.vercel, user_id) == BYO

    await SecretVault().delete(user_id, CredentialKind.vercel)
    assert await resolve_secret(CredentialKind.vercel, user_id) == PLATFORM_CONFIG


async def test_both_modes_are_usable_side_by_side(platform_config: str) -> None:
    byo_user, platform_user = PydanticObjectId(), PydanticObjectId()
    await _store(byo_user, BYO, CredentialScope.byo)

    assert await resolve_secret(CredentialKind.vercel, byo_user) == BYO
    assert await resolve_secret(CredentialKind.vercel, platform_user) == PLATFORM_CONFIG


async def test_unset_kind_resolves_to_none(
    fernet_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RENDER_API_KEY", "")
    reset_config()

    assert await resolve_secret(CredentialKind.render, PydanticObjectId()) is None
