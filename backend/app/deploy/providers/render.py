"""Render adapter — the **backend only** (phase-35, D11).

Render hosts the Express API as a persistent Node **web service**: a long-lived process with a real
port, which is precisely what Vercel's serverless model cannot give it (risk §12). ``target`` is
``be``, so a frontend spec is rejected before any network call.

The service is the durable object here, so :attr:`DeployRef.id` is the **service id** — every
subsequent operation (status, logs, env, destroy) keys off it, and ``deploy`` on an existing
service triggers a new deploy rather than creating a duplicate.
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

#: Render's deploy/service status → our vendor-neutral state.
_STATES: dict[str, DeployState] = {
    "created": DeployState.queued,
    "queued": DeployState.queued,
    "build_in_progress": DeployState.building,
    "update_in_progress": DeployState.building,
    "pre_deploy_in_progress": DeployState.building,
    "live": DeployState.live,
    "active": DeployState.live,
    "deactivated": DeployState.canceled,
    "build_failed": DeployState.failed,
    "update_failed": DeployState.failed,
    "pre_deploy_failed": DeployState.failed,
    "canceled": DeployState.canceled,
}


class RenderDeployProvider(BaseDeployProvider):
    key = "render"
    target = DeployTarget.be
    credential_kind = CredentialKind.render
    api_base = "render_api_url"

    async def deploy(
        self, spec: DeploySpec, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> DeployResult:
        self._check_target(spec)
        token = await self._token(mode, user_id)

        service_details: dict[str, Any] = {
            "env": "node",
            "envSpecificDetails": {
                "buildCommand": spec.build_cmd or "pnpm install",
                "startCommand": spec.start_cmd,
            },
        }
        if spec.region:
            service_details["region"] = spec.region

        body: dict[str, Any] = {
            "type": "web_service",
            "name": spec.name,
            "branch": spec.branch,
            "serviceDetails": service_details,
            # Render injects PORT; the service must bind it (the skeleton already does).
            "envVars": [{"key": k, "value": v} for k, v in sorted(spec.env.items())],
        }
        if spec.repo_url:
            body["repo"] = spec.repo_url
        if spec.root_dir:
            body["rootDir"] = spec.root_dir

        payload = await self._request("POST", "/v1/services", token, json_body=body)
        result = self._to_result(payload)

        # Creating a service does not always start a deploy — ask for one explicitly so the caller
        # always gets a build in flight.
        await self._request("POST", f"/v1/services/{result.ref.id}/deploys", token, json_body={})
        return result

    async def set_env(
        self,
        ref: DeployRef,
        env: Mapping[str, str],
        *,
        mode: DeployMode,
        user_id: PydanticObjectId | None = None,
    ) -> None:
        """Replace the service's env vars (``MONGODB_URI``, ``NODE_ENV``, …)."""
        token = await self._token(mode, user_id)
        await self._request(
            "PUT",
            f"/v1/services/{ref.id}/env-vars",
            token,
            json_body=[{"key": k, "value": v} for k, v in sorted(env.items())],
        )

    async def status(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> DeployResult:
        token = await self._token(mode, user_id)
        payload = await self._request("GET", f"/v1/services/{ref.id}", token)
        return self._to_result(payload)

    async def logs(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> list[LogLine]:
        token = await self._token(mode, user_id)
        payload = await self._request(
            "GET", "/v1/logs", token, params={"resource": ref.id, "direction": "backward"}
        )
        entries = payload.get("logs") if isinstance(payload, dict) else payload
        lines: list[LogLine] = []
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            message = entry.get("message")
            if message is None:
                continue
            timestamp = entry.get("timestamp")
            lines.append(LogLine(message=str(message), ts=str(timestamp) if timestamp else None))
        return lines

    async def destroy(
        self, ref: DeployRef, *, mode: DeployMode, user_id: PydanticObjectId | None = None
    ) -> bool:
        token = await self._token(mode, user_id)
        await self._request("DELETE", f"/v1/services/{ref.id}", token)
        return True

    # -- internals --------------------------------------------------------------------------

    def _to_result(self, payload: Any) -> DeployResult:
        # Create returns {"service": {...}}; reads return the service object directly.
        service = payload.get("service") if isinstance(payload, dict) else None
        if not isinstance(service, dict):
            service = payload if isinstance(payload, dict) else None
        if not isinstance(service, dict):
            raise self._error(DeployErrorKind.fatal, "Malformed service response from render")

        service_id = service.get("id")
        if not service_id:
            raise self._error(DeployErrorKind.fatal, "Render did not return a service id")

        details = service.get("serviceDetails")
        details = details if isinstance(details, dict) else {}
        raw_status = str(service.get("status") or details.get("status") or "created").lower()

        return DeployResult(
            ref=DeployRef(id=str(service_id), project=service.get("name")),
            provider=self.key,
            target=self.target,
            status=_STATES.get(raw_status, DeployState.queued),
            url=str(details.get("url")) if details.get("url") else None,
            raw={"status": raw_status},
        )


__all__ = ["RenderDeployProvider"]
