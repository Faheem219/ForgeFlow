"""Error taxonomy + FastAPI handlers producing a single consistent envelope.

Taxonomy (mandated by IMPLEMENTATION_PLAN.md §7):
  - ``UserError``     — the caller did something wrong; actionable, 4xx.
  - ``SystemError``   — an internal invariant broke; 5xx, do not leak internals.
  - ``ProviderError`` — an external provider (Anthropic/Stitch/Vercel/Render) failed; 502.

The envelope shape is always ``{"error": {"type", "message", "detail"?}}``.

Note: ``SystemError`` deliberately shadows the Python builtin of the same name because the
plan fixes these class names; within ForgeFlow code the taxonomy class is always intended.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ForgeFlowError(Exception):
    """Base class for all ForgeFlow errors."""

    status_code: int = 500
    error_type: str = "system_error"

    def __init__(self, message: str, detail: Any | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_envelope(self) -> dict[str, Any]:
        body: dict[str, Any] = {"type": self.error_type, "message": self.message}
        if self.detail is not None:
            body["detail"] = self.detail
        return {"error": body}


class UserError(ForgeFlowError):
    """The request was invalid or not allowed. Surface actionably to the user."""

    status_code = 400
    error_type = "user_error"


class SystemError(ForgeFlowError):  # noqa: A001 - taxonomy name fixed by the plan
    """An internal error. Never leak internals to the client."""

    status_code = 500
    error_type = "system_error"


class ProviderError(ForgeFlowError):
    """An external provider failed. Should degrade gracefully upstream."""

    status_code = 502
    error_type = "provider_error"

    def __init__(
        self,
        message: str,
        detail: Any | None = None,
        fallback_hint: str | None = None,
    ) -> None:
        super().__init__(message, detail)
        self.fallback_hint = fallback_hint


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers that render every error as the standard envelope."""

    @app.exception_handler(ForgeFlowError)
    async def _handle_forgeflow_error(_request: Request, exc: ForgeFlowError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, _exc: Exception) -> JSONResponse:
        # Do not leak internals; details belong in logs, not the response body.
        return JSONResponse(
            status_code=500,
            content={"error": {"type": "system_error", "message": "Internal server error"}},
        )
