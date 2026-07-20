"""Vercel adapter — the **frontend only** (phase-35, D11).

Deploys the *built* SPA: the sandbox runs the Vite build and this uploads the output directory as
an inline file set, so Vercel never needs repo access and the artifact that ships is exactly the
one the repair loop validated.

``target`` is ``fe`` and :meth:`~app.deploy.providers.base.BaseDeployProvider._check_target`
rejects anything else, so a backend can never reach this transport (risk §12).

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


class VercelDeployProvider(BaseDeployProvider):
    key = "vercel"
    target = DeployTarget.fe
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
            # Inline the built bundle — no git integration, no repo access needed.
            "files": [
                {"file": path, "data": content, "encoding": "utf-8"}
                for path, content in sorted((spec.files or {}).items())
            ],
            # `framework: null` + an output dir tells Vercel to serve the bundle as-is rather than
            # re-running a build it has no source for.
            "projectSettings": {"framework": None, "outputDirectory": None},
        }
        if spec.env:
            body["env"] = dict(spec.env)
            body["build"] = {"env": dict(spec.env)}

        payload = await self._request("POST", "/v13/deployments", token, json_body=body)
        return self._to_result(payload, project=spec.name)

    async def set_env(
        self,
        ref: DeployRef,
        env: Mapping[str, str],
        *,
        mode: DeployMode,
        user_id: PydanticObjectId | None = None,
    ) -> None:
        """Upsert project-scoped production env vars (e.g. ``VITE_API_BASE_URL``)."""
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
                    "type": "plain",
                    "target": ["production"],
                },
                params={"upsert": "true"},
            )

    async def status(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> DeployResult:
        token = await self._token(mode, user_id)
        payload = await self._request("GET", f"/v13/deployments/{ref.id}", token)
        return self._to_result(payload, project=ref.project)

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

    def _to_result(self, payload: Any, *, project: str | None) -> DeployResult:
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

        return DeployResult(
            ref=DeployRef(id=str(deployment_id), project=project or payload.get("name")),
            provider=self.key,
            target=self.target,
            status=state,
            url=str(url) if url else None,
            raw={"readyState": payload.get("readyState")},
        )


__all__ = ["VercelDeployProvider"]
