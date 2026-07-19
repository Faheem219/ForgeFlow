"""Env/secret scanning (phase-33): what each tier needs, and what must go in the vault."""

from __future__ import annotations

import pytest

from app.deploy.analyzer import InfraAnalyzer, is_secret
from tests.deploy.conftest import FakeAnalyzerWorkspace, fe_be_files, make_project

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_env_is_partitioned_between_the_tiers(fe_be_db: FakeAnalyzerWorkspace) -> None:
    project = await make_project()

    plan = await InfraAnalyzer(fe_be_db).analyze(project, persist=False)

    # Only VITE_-prefixed values can reach a browser bundle…
    assert plan.fe is not None and plan.fe.env == ["VITE_API_BASE_URL"]
    # …and the server never sees them.
    assert plan.be is not None
    assert set(plan.be.env) == {"MONGODB_URI", "NODE_ENV", "PORT"}
    assert not any(name.startswith("VITE_") for name in plan.be.env)


async def test_env_is_found_in_code_even_when_undocumented() -> None:
    """`.env.example` is the contract, but the code is the truth — both are scanned."""
    files = fe_be_files(mongoose=True)
    files["backend/src/mailer.ts"] = (
        "const key = process.env.SENDGRID_API_KEY\n"
        "const from = process.env['MAIL_FROM']\n"  # bracket access counts too
    )
    files["frontend/src/flags.ts"] = "export const on = import.meta.env.VITE_FEATURE_FLAGS"
    project = await make_project()

    plan = await InfraAnalyzer(FakeAnalyzerWorkspace(files)).analyze(project, persist=False)

    assert plan.be is not None
    assert "SENDGRID_API_KEY" in plan.be.env and "MAIL_FROM" in plan.be.env
    assert plan.fe is not None and "VITE_FEATURE_FLAGS" in plan.fe.env


async def test_only_credential_shaped_values_are_required_secrets() -> None:
    files = fe_be_files(mongoose=True)
    files["backend/src/mailer.ts"] = (
        "process.env.SENDGRID_API_KEY\nprocess.env.STRIPE_SECRET\nprocess.env.LOG_LEVEL\n"
    )
    project = await make_project()

    plan = await InfraAnalyzer(FakeAnalyzerWorkspace(files)).analyze(project, persist=False)

    assert plan.required_secrets == ["MONGODB_URI", "SENDGRID_API_KEY", "STRIPE_SECRET"]
    assert "LOG_LEVEL" not in plan.required_secrets  # plain config, not a credential
    assert "NODE_ENV" not in plan.required_secrets
    assert "PORT" not in plan.required_secrets


async def test_a_secret_shaped_frontend_var_is_flagged_as_a_leak() -> None:
    """VITE_ values are compiled into the bundle, so a secret-shaped one is a real exposure."""
    files = fe_be_files(mongoose=True)
    files["frontend/src/bad.ts"] = "export const k = import.meta.env.VITE_STRIPE_SECRET_KEY"
    project = await make_project()

    plan = await InfraAnalyzer(FakeAnalyzerWorkspace(files)).analyze(project, persist=False)

    assert any("exposed to the browser" in w for w in plan.warnings)
    assert plan.confidence == "medium"
    # It is never classified as a storable secret — the fix is to move it, not to vault it.
    assert "VITE_STRIPE_SECRET_KEY" not in plan.required_secrets


def test_is_secret_classification() -> None:
    for name in ("MONGODB_URI", "STRIPE_SECRET", "API_KEY", "AUTH_TOKEN", "DB_PASSWORD", "PG_DSN"):
        assert is_secret(name) is True, name
    # A base URL is public config; a URI carries credentials.
    for name in ("VITE_API_BASE_URL", "API_BASE_URL", "PORT", "NODE_ENV", "LOG_LEVEL"):
        assert is_secret(name) is False, name
    # Anything shipped to the browser is public by construction.
    assert is_secret("VITE_STRIPE_SECRET_KEY") is False
