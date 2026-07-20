"""Deploy modes (phase-35, D11): seamless uses the platform's credential, BYO uses the user's.

The distinction is a real security boundary, not a preference: a seamless deploy must never
silently spend a user's token, and a BYO deploy must never silently fall back to the platform's.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.core.errors import ProviderError
from app.db.models.enums import CredentialKind, CredentialScope, DeployMode
from app.deploy.providers.base import DeploySpec, DeployTarget
from app.deploy.providers.errors import DeployErrorKind, kind_of
from app.deploy.providers.render import RenderDeployProvider
from app.deploy.providers.vercel import VercelDeployProvider
from app.deploy.secrets import SecretVault
from tests.deploy.conftest import FakeDeployApi, no_sleep

pytestmark = pytest.mark.usefixtures("mongo_db")

PLATFORM_TOKEN = "platform-vercel-token"
BYO_TOKEN = "users-own-vercel-token"


@pytest.fixture
def platform_creds(monkeypatch: pytest.MonkeyPatch, fernet_key: str) -> None:
    monkeypatch.setenv("VERCEL_TOKEN", PLATFORM_TOKEN)
    monkeypatch.setenv("RENDER_API_KEY", "platform-render-key")
    reset_config()


def fe_spec() -> DeploySpec:
    return DeploySpec(name="app", target=DeployTarget.fe, files={"index.html": "<html>"})


def be_spec() -> DeploySpec:
    return DeploySpec(name="api", target=DeployTarget.be, start_cmd="pnpm start")


def _api() -> FakeDeployApi:
    return FakeDeployApi(
        {
            ("POST", "/v13/deployments"): (200, {"id": "dpl_1", "readyState": "READY"}),
            ("POST", "/v1/services/"): (201, {"id": "dep-1"}),
            ("POST", "/v1/services"): (201, {"service": {"id": "srv-1", "status": "created"}}),
        }
    )


def _sent_token(api: FakeDeployApi) -> str:
    return api.requests[0].headers["Authorization"].removeprefix("Bearer ")


# ---------------------------------------------------------------- seamless


async def test_seamless_uses_the_platform_credential(platform_creds: None) -> None:
    api = _api()
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)

    await provider.deploy(fe_spec(), mode=DeployMode.seamless)

    assert _sent_token(api) == PLATFORM_TOKEN


async def test_seamless_never_spends_a_users_byo_token(platform_creds: None) -> None:
    """Even with a BYO token on file, a seamless deploy bills the platform's account."""
    user_id = PydanticObjectId()
    await SecretVault().put_credential(user_id, CredentialKind.vercel, BYO_TOKEN)

    api = _api()
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)
    await provider.deploy(fe_spec(), mode=DeployMode.seamless, user_id=user_id)

    assert _sent_token(api) == PLATFORM_TOKEN
    assert BYO_TOKEN not in str(api.requests[0].headers)


async def test_seamless_without_a_platform_credential_is_an_auth_error(
    fernet_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VERCEL_TOKEN", "")
    reset_config()

    api = _api()
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)

    with pytest.raises(ProviderError) as exc:
        await provider.deploy(fe_spec(), mode=DeployMode.seamless)

    assert kind_of(exc.value) is DeployErrorKind.auth
    assert api.requests == []  # failed before any network call


# ---------------------------------------------------------------- byo


async def test_byo_uses_the_users_own_credential(platform_creds: None) -> None:
    user_id = PydanticObjectId()
    await SecretVault().put_credential(user_id, CredentialKind.vercel, BYO_TOKEN)

    api = _api()
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)
    await provider.deploy(fe_spec(), mode=DeployMode.byo, user_id=user_id)

    assert _sent_token(api) == BYO_TOKEN


async def test_byo_never_falls_back_to_the_platform_credential(platform_creds: None) -> None:
    """A user who asked for BYO must be told their token is missing, not silently billed to us."""
    api = _api()
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)

    with pytest.raises(ProviderError) as exc:
        await provider.deploy(fe_spec(), mode=DeployMode.byo, user_id=PydanticObjectId())

    assert kind_of(exc.value) is DeployErrorKind.auth
    assert "Settings" in (exc.value.fallback_hint or "")
    assert api.requests == []


async def test_byo_ignores_another_users_credential(platform_creds: None) -> None:
    owner, other = PydanticObjectId(), PydanticObjectId()
    await SecretVault().put_credential(owner, CredentialKind.vercel, BYO_TOKEN)

    api = _api()
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)

    with pytest.raises(ProviderError):
        await provider.deploy(fe_spec(), mode=DeployMode.byo, user_id=other)


async def test_byo_without_a_user_is_an_auth_error(platform_creds: None) -> None:
    api = _api()
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)

    with pytest.raises(ProviderError) as exc:
        await provider.deploy(fe_spec(), mode=DeployMode.byo)

    assert kind_of(exc.value) is DeployErrorKind.auth


async def test_a_platform_scoped_vault_entry_serves_seamless(
    fernet_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator can move the platform token out of plaintext env into the vault."""
    monkeypatch.setenv("VERCEL_TOKEN", "")
    reset_config()
    await SecretVault().put_credential(
        PydanticObjectId(), CredentialKind.vercel, "vaulted-platform", CredentialScope.platform
    )

    api = _api()
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)
    await provider.deploy(fe_spec(), mode=DeployMode.seamless)

    assert _sent_token(api) == "vaulted-platform"


# ---------------------------------------------------------------- both modes, both targets


async def test_render_honours_the_same_mode_split(platform_creds: None) -> None:
    user_id = PydanticObjectId()
    await SecretVault().put_credential(user_id, CredentialKind.render, "users-render-key")

    seamless_api, byo_api = _api(), _api()
    await RenderDeployProvider(transport=seamless_api.transport(), sleep=no_sleep).deploy(
        be_spec(), mode=DeployMode.seamless, user_id=user_id
    )
    await RenderDeployProvider(transport=byo_api.transport(), sleep=no_sleep).deploy(
        be_spec(), mode=DeployMode.byo, user_id=user_id
    )

    assert _sent_token(seamless_api) == "platform-render-key"
    assert _sent_token(byo_api) == "users-render-key"


async def test_each_target_resolves_its_own_credential_kind(platform_creds: None) -> None:
    """A Vercel token must never be handed to Render (or vice versa)."""
    user_id = PydanticObjectId()
    await SecretVault().put_credential(user_id, CredentialKind.vercel, BYO_TOKEN)

    api = _api()
    # The user has a Vercel token but no Render one — the Render deploy must still fail.
    with pytest.raises(ProviderError) as exc:
        await RenderDeployProvider(transport=api.transport(), sleep=no_sleep).deploy(
            be_spec(), mode=DeployMode.byo, user_id=user_id
        )

    assert kind_of(exc.value) is DeployErrorKind.auth
    assert "render" in str(exc.value).lower()
