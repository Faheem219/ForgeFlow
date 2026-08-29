"""Vercel adapter — frontend **and** backend (phase-35; backend added in phase-58, D11 amended).

Both targets ship the same way: an **inline file upload**. Vercel never needs repo access, so what
deploys is exactly what the repair loop validated, and there is no git host anywhere in the path.

The two targets differ only in what is uploaded and who builds it:

- ``fe`` — the *built* SPA. The sandbox runs the Vite build and the output directory is uploaded
  as-is (``framework: None``), because there is no source for Vercel to build.
- ``be`` — the backend **source** plus the skeleton's ``vercel.json``, which points Vercel at
  ``api/index.ts`` (the entrypoint added in phase-58 that exports the Express app). Vercel compiles
  it into a function. D11's original rationale — *"Vercel alone can't host a persistent Node
  backend"* — no longer holds; the trade-offs actually taken on are recorded in risk §12.

Two Vercel API shapes matter here: a **deployment** is immutable (status/logs/destroy key off its
id), while **env vars belong to the project** — hence :class:`DeployRef` carrying both.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from beanie import PydanticObjectId

from app.db.models.enums import CredentialKind, DeployMode
from app.deploy.providers.base import (
    BaseDeployProvider,
    DeployRef,
    DeployResult,
    DeploySpec,
    DeployState,
    DeployTarget,
    LogLine,
)
from app.deploy.providers.errors import DeployErrorKind

#: Vercel's `readyState` → our vendor-neutral state.
_STATES: dict[str, DeployState] = {
    "QUEUED": DeployState.queued,
    "INITIALIZING": DeployState.building,
    "BUILDING": DeployState.building,
    "READY": DeployState.live,
    "ERROR": DeployState.failed,
    "CANCELED": DeployState.canceled,
}


#: Name shapes that carry a credential. ``encrypted`` values are still listed (and revealable) in
#: the dashboard — which is the visibility the project-scoped copy exists for — but the API will not
#: read them back, so a connection string never sits in plaintext in Vercel's settings store.
_SECRET_SUFFIXES = ("_URI", "_URL_SECRET", "_SECRET", "_TOKEN", "_KEY", "_PASSWORD", "_DSN")


def _env_type(key: str) -> str:
    """``encrypted`` for secret-shaped names, ``plain`` for the rest.

    Plain is deliberate for the others: ``NODE_ENV`` and ``VITE_API_BASE_URL`` are not secrets (the
    latter is compiled into the SPA and readable by anyone with devtools), and a readable value in
    the dashboard is more useful than a hidden one.
    """
    upper = key.upper()
    return "encrypted" if any(upper.endswith(suffix) for suffix in _SECRET_SUFFIXES) else "plain"


class VercelDeployProvider(BaseDeployProvider):
    key = "vercel"
    #: Primary target — what a result reports when a ref carries no target of its own.
    target = DeployTarget.fe
    #: Both targets are served; ``_check_target`` tests membership (phase-58).
    targets = frozenset({DeployTarget.fe, DeployTarget.be})
    credential_kind = CredentialKind.vercel
    api_base = "vercel_api_url"

    async def deploy(
        self, spec: DeploySpec, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> DeployResult:
        self._check_target(spec)
        token = await self._token(mode, user_id)

        body: dict[str, Any] = {
            "name": spec.name,
            "target": "production",
            # Inline the file set — no git integration, no repo access needed, for either target.
            "files": [
                {"file": path, "data": content, "encoding": "utf-8"}
                for path, content in sorted((spec.files or {}).items())
            ],
        }
        if spec.target is DeployTarget.fe:
            # `framework: null` + no output dir tells Vercel to serve the bundle as-is rather than
            # re-running a build it has no source for.
            body["projectSettings"] = {"framework": None, "outputDirectory": None}
            if spec.env:
                # A Vite var is read at BUILD time, so the SPA needs it in both places.
                body["env"] = dict(spec.env)
                body["build"] = {"env": dict(spec.env)}
        else:
            # The backend's vercel.json (shipped in the uploaded source) declares the function, so
            # framework detection must stay out of the way.
            body["projectSettings"] = {"framework": None}
            if spec.env:
                # MONGODB_URI is read at REQUEST time and is a secret: runtime env only, never the
                # build env, so it is not baked into a build log or artifact.
                body["env"] = dict(spec.env)

        payload = await self._request("POST", "/v13/deployments", token, json_body=body)
        return self._to_result(payload, project=spec.name, target=spec.target)

    async def set_env(
        self,
        ref: DeployRef,
        env: Mapping[str, str],
        *,
        mode: DeployMode,
        user_id: PydanticObjectId | None = None,
    ) -> None:
        """Upsert **project-scoped** production env vars (e.g. ``VITE_API_BASE_URL``).

        Project scope is the point: env passed in a deployment's creation body belongs to that one
        immutable deployment and never appears in the project's settings, so it is invisible to the
        account owner and absent from any deployment the provider's own dashboard creates later.
        """
        project = ref.project
        if not project:
            raise self._error(
                DeployErrorKind.config, "Setting Vercel env vars needs the project name"
            )
        token = await self._token(mode, user_id)
        for key, value in sorted(env.items()):
            await self._request(
                "POST",
                f"/v10/projects/{project}/env",
                token,
                json_body={
                    "key": key,
                    "value": value,
                    "type": _env_type(key),
                    "target": ["production"],
                },
                params={"upsert": "true"},
            )

    async def status(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> DeployResult:
        token = await self._token(mode, user_id)
        payload = await self._request("GET", f"/v13/deployments/{ref.id}", token)
        return self._to_result(payload, project=ref.project, target=ref.target)

    async def logs(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> list[LogLine]:
        token = await self._token(mode, user_id)
        payload = await self._request("GET", f"/v2/deployments/{ref.id}/events", token)
        events = payload if isinstance(payload, list) else []
        lines: list[LogLine] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            text = event.get("text")
            if text is None:
                inner = event.get("payload")
                text = inner.get("text") if isinstance(inner, dict) else None
            if text is None:
                continue
            created = event.get("created")
            lines.append(LogLine(message=str(text), ts=str(created) if created else None))
        return lines

    async def destroy(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> bool:
        token = await self._token(mode, user_id)
        await self._request("DELETE", f"/v13/deployments/{ref.id}", token)
        return True

    # -- internals --------------------------------------------------------------------------

    def _to_result(
        self, payload: Any, *, project: str | None, target: DeployTarget | None = None
    ) -> DeployResult:
        if not isinstance(payload, dict):
            raise self._error(DeployErrorKind.fatal, "Malformed deployment response from vercel")

        deployment_id = payload.get("id") or payload.get("uid")
        if not deployment_id:
            raise self._error(DeployErrorKind.fatal, "Vercel did not return a deployment id")

        state = _STATES.get(str(payload.get("readyState", "")).upper(), DeployState.queued)
        host = payload.get("url")
        alias = payload.get("alias")
        if isinstance(alias, list) and alias:
            host = alias[0]  # the stable production alias beats the per-deploy host
        url = f"https://{host}" if host and not str(host).startswith("http") else host

        served = target or self.target
        return DeployResult(
            ref=DeployRef(
                id=str(deployment_id),
                project=project or payload.get("name"),
                target=served,
            ),
            provider=self.key,
            target=served,
            status=state,
            url=str(url) if url else None,
            raw={"readyState": payload.get("readyState")},
        )


__all__ = ["VercelDeployProvider"]
