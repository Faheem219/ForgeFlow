"""The ``DeployProvider`` interface + shared plumbing (phase-35).

Shaped after ``DesignProvider`` (phase-16) so targets stay pluggable without the orchestrator
knowing any vendor. Two things are deliberately structural rather than conventional:

1. **A provider serves exactly one target.** :attr:`BaseDeployProvider.target` is a class
   set, and :meth:`BaseDeployProvider._check_target` rejects a spec outside it before any network
   call. Render serves ``be`` only and Vercel serves both (D11 as amended, phase-58): the check is
   membership, so a provider can never be handed a target it has no code path for.
2. **Credentials are resolved per mode, at call time.** ``seamless`` uses the platform credential
   (vault, then config); ``byo`` requires *this user's* token and fails with an actionable auth
   error if it is absent. Nothing is cached and nothing is logged (phase-34).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import httpx
from beanie import PydanticObjectId

from app.core.config import get_config
from app.core.errors import ProviderError
from app.db.models.enums import CredentialKind, DeployMode
from app.deploy.providers.errors import DeployErrorKind, deploy_error, kind_of
from app.deploy.secrets import SecretVault

logger = logging.getLogger(__name__)


class DeployTarget(StrEnum):
    fe = "fe"
    be = "be"


class DeployState(StrEnum):
    queued = "queued"
    building = "building"
    live = "live"
    failed = "failed"
    canceled = "canceled"


#: States from which nothing further will happen without a new deploy.
TERMINAL_STATES = frozenset({DeployState.live, DeployState.failed, DeployState.canceled})


@dataclass(frozen=True)
class DeploySpec:
    """What to deploy. One shape, validated per target — the fields are target-exclusive.

    Frontend (``fe``): a **built** static bundle (``files``), because the SPA is compiled in the
    sandbox and only its output is uploaded. Backend (``be``): a repo + the commands that build and
    start a persistent Node process.
    """

    name: str
    target: DeployTarget
    env: Mapping[str, str] = field(default_factory=dict)

    # --- frontend (static) ---
    #: Workspace-relative path → file content, i.e. the built SPA output.
    files: Mapping[str, str] | None = None

    # --- backend (persistent service) ---
    repo_url: str | None = None
    branch: str = "main"
    root_dir: str | None = None
    build_cmd: str | None = None
    start_cmd: str | None = None
    port: int | None = None
    region: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise deploy_error(
                "deploy", DeployErrorKind.config, "A deployment needs a project name"
            )
        if self.target is DeployTarget.fe and not self.files:
            raise deploy_error(
                "vercel",
                DeployErrorKind.config,
                "A frontend deployment needs the built static output",
                target=str(self.target),
            )
        if self.target is DeployTarget.be and not (self.files or (self.start_cmd or "").strip()):
            raise deploy_error(
                "deploy",
                DeployErrorKind.config,
                "A backend deployment needs uploaded source or a start command",
                target=str(self.target),
            )


@dataclass(frozen=True)
class DeployRef:
    """Handle for a live deployment: what ``status``/``logs``/``set_env``/``destroy`` key off."""

    #: Vercel: the deployment id. Render: the service id.
    id: str
    #: Provider-side project/service name — Vercel scopes env vars to the project, not a deployment.
    project: str | None = None
    #: Which target this deployment serves. Needed because one provider may serve both (phase-58):
    #: ``status`` has only the ref, so without this it could not tell an fe deploy from a be one.
    target: DeployTarget | None = None


@dataclass(frozen=True)
class DeployResult:
    ref: DeployRef
    provider: str
    target: DeployTarget
    status: DeployState
    url: str | None = None
    #: Non-sensitive provider payload echoed back for the topology view (phase-38).
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.ref.id,
            "project": self.ref.project,
            "provider": self.provider,
            "target": str(self.target),
            "status": str(self.status),
            "url": self.url,
        }


@dataclass(frozen=True)
class LogLine:
    message: str
    ts: str | None = None


class DeployProvider(Protocol):
    """One vendor, one target. Every method is credential-resolving and mode-aware."""

    key: str
    target: DeployTarget
    #: Which vault credential this vendor authenticates with. Part of the contract, not an
    #: implementation detail: a caller that has to ask "what would a BYO deploy need?" (phase-62's
    #: readiness check) can only answer it from the provider that will actually run.
    credential_kind: CredentialKind

    async def deploy(
        self, spec: DeploySpec, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> DeployResult: ...

    async def set_env(
        self,
        ref: DeployRef,
        env: Mapping[str, str],
        *,
        mode: DeployMode,
        user_id: PydanticObjectId | None = None,
    ) -> None: ...

    async def status(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> DeployResult: ...

    async def logs(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> list[LogLine]: ...

    async def destroy(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> bool: ...


class BaseDeployProvider:
    """Shared credential resolution, HTTP calling, retry and error classification."""

    key: str = "deploy"
    target: DeployTarget
    credential_kind: CredentialKind
    api_base: str = ""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        # A transport seam (not a mocked client) keeps tests honest about URLs, headers and bodies.
        self._transport = transport
        self._sleep = sleep or asyncio.sleep

    # -- credentials ------------------------------------------------------------------------

    async def _token(self, mode: DeployMode, user_id: PydanticObjectId | None) -> str:
        """Resolve the credential for ``mode``; decrypted here and nowhere else (phase-34)."""
        vault = SecretVault()
        if mode is DeployMode.byo:
            if user_id is None:
                raise self._error(
                    DeployErrorKind.auth, "BYO deploys need a signed-in user's credential"
                )
            token = await vault.get_credential(user_id, self.credential_kind)
            if not token:
                raise self._error(
                    DeployErrorKind.auth,
                    f"No {self.key} token stored for this account",
                    hint=(
                        f"Add your {self.key} token in Settings → Provider credentials, or switch "
                        "this deploy to seamless mode to use the platform's account."
                    ),
                )
            return token

        # Seamless: the platform's credential only — a user's BYO token is never silently spent.
        token = await vault.resolve(self.credential_kind)
        if not token:
            raise self._error(
                DeployErrorKind.auth, f"The platform has no {self.key} credential configured"
            )
        return token

    # -- transport --------------------------------------------------------------------------

    def _base_url(self) -> str:
        return str(get_config().get(self.api_base)).rstrip("/")

    async def _request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        json_body: Any | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        """One authenticated call with bounded retry on transient failures."""
        config = get_config()
        max_retries = int(config.get("deploy_max_retries"))
        timeout = float(config.get("deploy_timeout_s"))
        url = f"{self._base_url()}{path}"

        attempt = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                    response = await client.request(
                        method,
                        url,
                        json=json_body,
                        params=dict(params) if params else None,
                        # The token goes in the header and never into logs or an error body.
                        headers={"Authorization": f"Bearer {token}"},
                    )
            except httpx.HTTPError as exc:
                if attempt < max_retries:
                    attempt += 1
                    await self._sleep(0.5 * 2 ** (attempt - 1))
                    continue
                raise self._error(DeployErrorKind.transient, f"Could not reach {self.key}") from exc

            try:
                self._raise_for_status(response, method, path)
            except ProviderError as exc:
                if kind_of(exc) is DeployErrorKind.transient and attempt < max_retries:
                    attempt += 1
                    await self._sleep(0.5 * 2 ** (attempt - 1))
                    continue
                raise

            if not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise self._error(
                    DeployErrorKind.fatal, f"Malformed response from {self.key}"
                ) from exc

    def _raise_for_status(self, response: httpx.Response, method: str, path: str) -> None:
        status = response.status_code
        if status < 400:
            return
        # The body can echo the request (which carries env values) — never include it.
        if status in (401, 403):
            raise self._error(
                DeployErrorKind.auth, f"{self.key} rejected the credentials ({status})"
            )
        if status == 429:
            raise self._error(DeployErrorKind.quota, f"{self.key} rate/plan limit reached")
        if status >= 500:
            raise self._error(DeployErrorKind.transient, f"{self.key} is unavailable ({status})")
        raise self._error(
            DeployErrorKind.fatal,
            f"{self.key} rejected {method} {path} ({status})",
        )

    # -- guards + helpers -------------------------------------------------------------------

    @property
    def served_targets(self) -> frozenset[DeployTarget]:
        """Every target this adapter can deploy. Defaults to just its primary ``target``."""
        return getattr(self, "targets", None) or frozenset({self.target})

    def _check_target(self, spec: DeploySpec) -> None:
        """Refuse a spec this provider does not serve — the D11 split, enforced in code."""
        if spec.target not in self.served_targets:
            raise self._error(
                DeployErrorKind.config,
                f"{self.key} does not deploy the '{spec.target}' target",
                target=str(spec.target),
                hint=(
                    "Vercel serves the frontend and (since phase-58) the backend; Render serves "
                    "the backend only. Check DEPLOY_BE_PROVIDER."
                ),
            )

    def _error(
        self,
        kind: DeployErrorKind,
        message: str,
        *,
        target: str | None = None,
        hint: str | None = None,
    ) -> ProviderError:
        return deploy_error(self.key, kind, message, target=target or str(self.target), hint=hint)


__all__ = [
    "BaseDeployProvider",
    "DeployProvider",
    "DeployRef",
    "DeployResult",
    "DeploySpec",
    "DeployState",
    "DeployTarget",
    "LogLine",
    "TERMINAL_STATES",
]
