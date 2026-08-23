"""Which provider serves which target — the D11 split as amended in phase-58.

D11 originally read *"FE→Vercel, BE→Render"* and phase-35 made it unrepresentable: one target per
provider, no reachable path from a backend spec to Vercel's transport. That rationale — *"Vercel
alone can't host a persistent Node backend"* — no longer holds, and the alternative (a git host,
a per-project repo, a push from the workspace) buys a persistent process at a cost the generated
CRUD stack does not need. So the backend now ships to Vercel as an inline upload by default.

What is asserted here is the *replacement* invariant, which is just as strict: routing is explicit
and configurable, every target resolves to exactly one provider, and a provider still refuses a
target it has no code path for — before any network call.
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


def be_source() -> dict[str, str]:
    return {"vercel.json": '{"version":2}', "api/index.ts": "export {}"}


def be_spec() -> DeploySpec:
    return DeploySpec(
        name="forgeflow-todos-api",
        target=DeployTarget.be,
        files=be_source(),
        start_cmd="pnpm start",
        port=3001,
    )


def fe_spec() -> DeploySpec:
    return DeploySpec(name="forgeflow-todos", target=DeployTarget.fe, files={"index.html": "<h1>"})


# --------------------------------------------------------------- the amendment, proven


async def test_vercel_now_accepts_a_backend_spec(platform_creds: None) -> None:
    api = FakeDeployApi(
        {("POST", "/v13/deployments"): (200, {"id": "dpl_1", "readyState": "READY"})}
    )
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)

    result = await provider.deploy(be_spec(), mode=DeployMode.seamless)

    assert result.target is DeployTarget.be
    assert [(r.method, r.url.path) for r in api.requests] == [("POST", "/v13/deployments")]


async def test_a_backend_deploy_reports_the_backend_target_not_the_primary_one(
    platform_creds: None,
) -> None:
    """One provider serves two targets, so the ref must carry which one it is (phase-58)."""
    api = FakeDeployApi(
        {("POST", "/v13/deployments"): (200, {"id": "dpl_1", "readyState": "READY"})}
    )
    provider = VercelDeployProvider(transport=api.transport(), sleep=no_sleep)

    result = await provider.deploy(be_spec(), mode=DeployMode.seamless)

    assert result.ref.target is DeployTarget.be


# --------------------------------------------------------------- still strict


async def test_render_still_refuses_a_frontend_spec(platform_creds: None) -> None:
    """Render hosts services, not static bundles — refused before any network call."""
    api = FakeDeployApi({("POST", "/v1/services"): (201, {"service": {"id": "srv-1"}})})
    provider = RenderDeployProvider(transport=api.transport(), sleep=no_sleep)

    with pytest.raises(ProviderError) as exc:
        await provider.deploy(fe_spec(), mode=DeployMode.seamless)

    assert kind_of(exc.value) is DeployErrorKind.config
    assert error_detail(exc.value)["target"] == "fe"
    assert api.requests == []


def test_a_backend_spec_needs_something_to_deploy() -> None:
    """Neither uploaded source nor a start command means there is nothing to ship."""
    with pytest.raises(ProviderError) as exc:
        DeploySpec(name="api", target=DeployTarget.be)

    assert kind_of(exc.value) is DeployErrorKind.config


def test_a_frontend_spec_still_needs_built_output() -> None:
    with pytest.raises(ProviderError) as exc:
        DeploySpec(name="web", target=DeployTarget.fe)

    assert kind_of(exc.value) is DeployErrorKind.config


def test_each_provider_declares_which_targets_it_serves() -> None:
    assert VercelDeployProvider().served_targets == frozenset({DeployTarget.fe, DeployTarget.be})
    assert RenderDeployProvider().served_targets == frozenset({DeployTarget.be})


def test_each_provider_uses_its_own_credential_kind() -> None:
    assert VercelDeployProvider.credential_kind is CredentialKind.vercel
    assert RenderDeployProvider.credential_kind is CredentialKind.render


# --------------------------------------------------------------- the registry


def test_the_backend_routes_to_vercel_by_default() -> None:
    assert isinstance(provider_for(DeployTarget.be), VercelDeployProvider)
    assert isinstance(provider_for(DeployTarget.fe), VercelDeployProvider)


def test_the_backend_routes_to_render_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEPLOY_BE_PROVIDER", "render")
    reset_config()
    try:
        assert isinstance(provider_for(DeployTarget.be), RenderDeployProvider)
        # The frontend has no such choice.
        assert isinstance(provider_for(DeployTarget.fe), VercelDeployProvider)
    finally:
        monkeypatch.delenv("DEPLOY_BE_PROVIDER", raising=False)
        reset_config()


def test_an_unknown_backend_provider_falls_back_rather_than_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in config must not take the whole deploy stage down."""
    monkeypatch.setenv("DEPLOY_BE_PROVIDER", "fly")
    reset_config()
    try:
        assert isinstance(provider_for(DeployTarget.be), VercelDeployProvider)
    finally:
        monkeypatch.delenv("DEPLOY_BE_PROVIDER", raising=False)
        reset_config()


def test_every_target_resolves_to_exactly_one_provider() -> None:
    """No target is unroutable, and each resolves deterministically."""
    resolved = {target: provider_for(target).key for target in DeployTarget}
    assert resolved == {DeployTarget.fe: "vercel", DeployTarget.be: "vercel"}


def test_an_unknown_target_cannot_be_routed() -> None:
    with pytest.raises(ValueError):
        provider_for("lambda")  # type: ignore[arg-type]
