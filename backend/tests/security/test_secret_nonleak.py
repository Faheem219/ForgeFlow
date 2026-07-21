"""Secret handling audit (phase-47): nothing secret reaches a response, a log line, or an error.

Phase-34 asserts the vault's own guarantees. This is the system-wide sweep: it drives real HTTP
traffic with a distinctive secret planted in the vault and then greps *everything* the process
emitted — response bodies, error envelopes, and every formatted log record — for any trace of it.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re

import pytest
from beanie import PydanticObjectId
from httpx import AsyncClient

from app.core.logging import REDACTED, JsonFormatter
from app.db.models.enums import CredentialKind
from app.deploy.secrets import SecretVault
from tests.security.conftest import auth, create_project, register

pytestmark = pytest.mark.usefixtures("mongo_db")

SECRET = "ff_supersecret_canary_9f3a8b2c7d1e"
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _rendered(records: list[logging.LogRecord]) -> str:
    formatter = JsonFormatter()
    return "\n".join(formatter.format(record) for record in records)


# ---------------------------------------------------------------- responses


async def test_a_stored_secret_never_appears_in_any_response(
    http: AsyncClient, fernet_key: str
) -> None:
    token = await register(http, "leak-a@example.com")
    project_id = await create_project(http, token)

    stored = await http.put("/credentials/vercel", json={"secret": SECRET}, headers=auth(token))
    assert stored.status_code == 200

    # Sweep every readable surface that could plausibly echo configuration back.
    for path in (
        "/credentials",
        "/auth/me",
        "/projects",
        f"/projects/{project_id}",
        f"/projects/{project_id}/cost",
        f"/projects/{project_id}/runs",
        f"/projects/{project_id}/artifacts",
        f"/projects/{project_id}/stages",
        f"/projects/{project_id}/infra/plan",
        f"/projects/{project_id}/deploy/latest",
    ):
        response = await http.get(path, headers=auth(token))
        assert SECRET not in response.text, f"{path} leaked the secret"


async def test_the_credential_list_exposes_only_metadata(
    http: AsyncClient, fernet_key: str
) -> None:
    token = await register(http, "leak-b@example.com")
    await http.put("/credentials/vercel", json={"secret": SECRET}, headers=auth(token))

    body = (await http.get("/credentials", headers=auth(token))).json()

    assert len(body) == 1
    assert set(body[0]) == {"kind", "scope", "created_at", "last4"}
    assert body[0]["last4"] == SECRET[-4:]


async def test_error_envelopes_never_carry_a_secret(http: AsyncClient, fernet_key: str) -> None:
    """Errors are the classic leak path — a provider echoing the request back into a message."""
    token = await register(http, "leak-c@example.com")
    await http.put("/credentials/vercel", json={"secret": SECRET}, headers=auth(token))

    responses = [
        await http.get("/projects/not-an-id", headers=auth(token)),
        await http.get("/projects/507f1f77bcf86cd799439011", headers=auth(token)),
        await http.put("/credentials/vercel", json={}, headers=auth(token)),
        await http.put("/credentials/nonsense", json={"secret": "x"}, headers=auth(token)),
        await http.get("/credentials/vercel", headers=auth(token)),
    ]
    for response in responses:
        assert response.status_code >= 400
        assert SECRET not in response.text


# ---------------------------------------------------------------- logs


async def test_a_full_request_cycle_logs_no_secret(
    http: AsyncClient, fernet_key: str, caplog: pytest.LogCaptureFixture
) -> None:
    token = await register(http, "leak-d@example.com")

    with caplog.at_level(logging.DEBUG):
        await http.put("/credentials/vercel", json={"secret": SECRET}, headers=auth(token))
        await http.get("/credentials", headers=auth(token))
        await http.delete("/credentials/vercel", headers=auth(token))

    rendered = _rendered(caplog.records)
    assert SECRET not in rendered
    assert SECRET[-12:] not in rendered  # not even a long tail fragment


async def test_vault_operations_log_no_secret(
    fernet_key: str, caplog: pytest.LogCaptureFixture
) -> None:
    user_id = PydanticObjectId()
    vault = SecretVault()

    with caplog.at_level(logging.DEBUG):
        await vault.put_credential(user_id, CredentialKind.render, SECRET)
        await vault.resolve(CredentialKind.render, user_id)
        await vault.list_kinds(user_id)
        await vault.delete(user_id, CredentialKind.render)

    assert SECRET not in _rendered(caplog.records)


def test_the_formatter_redacts_credential_shaped_extras() -> None:
    record = logging.LogRecord(
        name="audit",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="calling provider",
        args=(),
        exc_info=None,
    )
    record.vercel_token = SECRET
    record.api_key = SECRET
    record.mongodb_uri = f"mongodb://user:{SECRET}@host/db"
    record.password = SECRET
    record.nested = {"authorization": f"Bearer {SECRET}", "keep": "visible"}
    record.project_id = "p1"

    payload = json.loads(JsonFormatter().format(record))

    assert SECRET not in json.dumps(payload)
    for field in ("vercel_token", "api_key", "mongodb_uri", "password"):
        assert payload[field] == REDACTED
    assert payload["nested"]["authorization"] == REDACTED
    assert payload["nested"]["keep"] == "visible"  # non-secrets stay debuggable
    assert payload["project_id"] == "p1"


# ---------------------------------------------------------------- subprocess environment


def test_a_subprocess_never_inherits_platform_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """A child process must not be able to `printenv` its way to our credentials.

    The allowlist fails closed: a secret added to the environment later is withheld without
    anyone remembering to deny it.
    """
    from app.core.config import subprocess_env

    for name in (
        "ANTHROPIC_API_KEY",
        "FERNET_KEY",
        "SECRET_KEY",
        "VERCEL_TOKEN",
        "RENDER_API_KEY",
        "MONGODB_URI",
        "STITCH_CLIENT_SECRET",
        "FIGMA_TOKEN",
        "A_FUTURE_SECRET_NOBODY_DENIED",
    ):
        monkeypatch.setenv(name, SECRET)

    env = subprocess_env()

    assert SECRET not in json.dumps(env)
    assert not any(SECRET == value for value in env.values())


def test_the_subprocess_environment_still_carries_what_a_child_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Withholding secrets must not break the tooling — PATH in particular is required."""
    from app.core.config import subprocess_env

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    env = subprocess_env({"GIT_TERMINAL_PROMPT": "0"})

    assert env["PATH"] == "/usr/bin:/bin"
    assert env["GIT_TERMINAL_PROMPT"] == "0"  # explicit extras are honoured


def test_the_control_plane_never_hands_os_environ_to_a_child() -> None:
    """Regression guard for the pattern itself, not just today's call sites."""
    offenders: list[str] = []
    pattern = re.compile(r"env\s*=\s*\{\s*\*\*\s*os\.environ")
    for path in (BACKEND_ROOT / "app").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(str(path.relative_to(BACKEND_ROOT)))
    assert offenders == [], f"Subprocess inherits the full environment in: {offenders}"


# ---------------------------------------------------------------- at rest + in source


async def test_secrets_are_ciphertext_at_rest(fernet_key: str) -> None:
    from app.db.models import Credential

    user_id = PydanticObjectId()
    await SecretVault().put_credential(user_id, CredentialKind.vercel, SECRET)

    doc = await Credential.find_one({"user_id": user_id})
    assert doc is not None
    assert SECRET not in doc.encrypted_secret
    assert doc.encrypted_secret != SECRET


def test_no_credential_is_hardcoded_in_the_application_source() -> None:
    """Guard against a real token reaching the shipped source.

    Scoped to ``app/``: test modules legitimately contain synthetic credential-shaped fixtures
    (a fake Atlas URI, a canary API key for the redaction test). Repo-wide secret scanning is the
    CI job's role — this keeps the *application* provably free of embedded credentials.
    """
    patterns = [
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),  # Anthropic
        re.compile(r"\bghp_[A-Za-z0-9]{30,}"),  # GitHub PAT
        re.compile(r"\brnd_[A-Za-z0-9]{20,}"),  # Render
        re.compile(r"mongodb\+srv://[^:\s]+:[^@\s]+@"),  # Mongo URI carrying credentials
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    ]
    app_root = BACKEND_ROOT / "app"
    offenders: list[str] = []
    for path in app_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in patterns:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(app_root)}: {pattern.pattern}")
    assert offenders == [], f"Possible hardcoded credentials: {offenders}"


#: `.env.example` is committed, so secret-shaped keys must ship blank. The one exception is the
#: dev JWT signing key, which ships a deliberately self-describing placeholder.
ENV_PLACEHOLDERS = {"SECRET_KEY": "dev-insecure-change-me"}

#: Suffixes that mean "this holds a credential" — matched on the *last* segment of the key so
#: numeric tuning knobs (ANTHROPIC_MAX_OUTPUT_TOKENS, STITCH_TOKEN_SKEW_S) are not swept up.
SECRET_KEY_SUFFIXES = ("TOKEN", "SECRET", "KEY", "PASSWORD", "URI", "DSN")


def test_the_env_example_ships_no_real_values() -> None:
    example = BACKEND_ROOT.parent / ".env.example"
    populated: list[str] = []

    for line in example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, rest = stripped.partition("=")
        key = key.strip()
        value = rest.split("#")[0].strip()
        if not value:
            continue
        if not key.upper().endswith(SECRET_KEY_SUFFIXES):
            continue
        if ENV_PLACEHOLDERS.get(key) == value:
            continue  # documented, self-describing dev placeholder
        if value.startswith("mongodb://localhost") or value.startswith("http://localhost"):
            continue  # local, credential-free defaults
        populated.append(f"{key}={value}")

    assert populated == [], f".env.example ships non-empty secrets: {populated}"
