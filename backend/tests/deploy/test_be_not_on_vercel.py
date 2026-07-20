"""The D11 split is unrepresentable, not merely discouraged (phase-35, risk §12).

A persistent Express server on Vercel's serverless model is the specific failure this locks out.
These tests assert there is **no reachable path** from a backend spec to Vercel's transport — the
guard fires before any network call, and the registry cannot be pointed the wrong way.
"""

from __future__ import annotations

import pytest

from app.core.config import reset_config
from app.core.errors import ProviderError
from app.db.models.enums import CredentialKind, DeployMode
from app.deploy.providers import provider_for
from app.deploy.providers.base import DeploySpec, DeployTarget
from app.deploy.providers.errors import DeployErrorKind, kind_of
from app.deploy.providers.render import RenderDeployProvider
from app.deploy.providers.vercel import VercelDeployProvider
from tests.deploy.conftest import FakeDeployApi, error_detail, no_sleep

pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest.fixture
def platform_creds(monkeypatch: pytest.MonkeyPatch, fernet_key: str) -> None:
    monkeypatch.setenv("VERCEL_TOKEN", "platform-vercel-token")
    monkeypatch.setenv("RENDER_API_KEY", "platform-render-key")
    reset_config()


def be_spec() -> DeploySpec:
    return DeploySpec(
        name="forgeflow-todos-api",
        target=DeployTarget.be,
        start_cmd="pnpm start",
        port=3001,
    )


def fe_spec() -> DeploySpec:
    return DeploySpec(name="forgeflow-todos", target=DeployTarget.fe, files={"index.html": "<h1>"})


async def test_deploying_a_backend_to_vercel_is_refused(platform_creds: None) -> None:
    api = FakeDeployApi({("POST", "/v13/deployments"): (200, {"id": "dpl_1"})})
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)

    with pytest.raises(ProviderError) as exc:
        await provider.deploy(be_spec(), mode=DeployMode.seamless)

    assert kind_of(exc.value) is DeployErrorKind.config
    assert error_detail(exc.value)["target"] == "be"


async def test_the_refusal_happens_before_any_network_call(platform_creds: None) -> None:
    """Not even a request is issued — Vercel never sees a backend spec."""
    api = FakeDeployApi({("POST", "/v13/deployments"): (200, {"id": "dpl_1"})})
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)

    with pytest.raises(ProviderError):
        await provider.deploy(be_spec(), mode=DeployMode.seamless)

    assert api.requests == []


async def test_the_refusal_explains_why(platform_creds: None) -> None:
    api = FakeDeployApi()
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)

    with pytest.raises(ProviderError) as exc:
        await provider.deploy(be_spec(), mode=DeployMode.seamless)

    hint = (exc.value.fallback_hint or "").lower()
    assert "render" in hint and "serverless" in hint


async def test_the_symmetric_mistake_is_refused_too(platform_creds: None) -> None:
    """Render is equally strict: it hosts services, not static bundles."""
    api = FakeDeployApi({("POST", "/v1/services"): (201, {"service": {"id": "srv-1"}})})
    provider = RenderDeployProvider(transport=api.transport(), sleep=no_sleep)

    with pytest.raises(ProviderError) as exc:
        await provider.deploy(fe_spec(), mode=DeployMode.seamless)

    assert kind_of(exc.value) is DeployErrorKind.config
    assert api.requests == []


def test_each_provider_declares_exactly_one_target() -> None:
    assert VercelDeployProvider.target is DeployTarget.fe
    assert RenderDeployProvider.target is DeployTarget.be


def test_each_provider_uses_its_own_credential_kind() -> None:
    assert VercelDeployProvider.credential_kind is CredentialKind.vercel
    assert RenderDeployProvider.credential_kind is CredentialKind.render


def test_the_registry_cannot_route_a_backend_to_vercel() -> None:
    assert isinstance(provider_for(DeployTarget.be), RenderDeployProvider)
    assert isinstance(provider_for(DeployTarget.fe), VercelDeployProvider)


def test_the_registry_covers_every_target_exactly_once() -> None:
    """No target is unroutable and none has two providers competing for it."""
    resolved = {target: provider_for(target).key for target in DeployTarget}
    assert resolved == {DeployTarget.fe: "vercel", DeployTarget.be: "render"}
    assert len(set(resolved.values())) == len(DeployTarget)


def test_an_unknown_target_cannot_be_routed() -> None:
    with pytest.raises(ValueError):
        provider_for("lambda")  # type: ignore[arg-type]
