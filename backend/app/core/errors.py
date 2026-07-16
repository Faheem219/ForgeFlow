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
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
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

    def to_envelope(self) -> dict[str, Any]:
        # Surface the fallback hint so the client can offer graceful degradation (phase-19/48).
        envelope = super().to_envelope()
        if self.fallback_hint:
            envelope["error"]["fallback_hint"] = self.fallback_hint
        return envelope


class AuthError(ForgeFlowError):
    """Authentication failed / missing (401)."""

    status_code = 401
    error_type = "auth_error"


class ForbiddenError(ForgeFlowError):
    """Authenticated but not allowed (403)."""

    status_code = 403
    error_type = "forbidden"


class NotFoundError(ForgeFlowError):
    """No such resource, or the caller does not own it (404 — existence is not leaked)."""

    status_code = 404
    error_type = "not_found"


class ConflictError(ForgeFlowError):
    """A uniqueness/state conflict, e.g. duplicate email (409)."""

    status_code = 409
    error_type = "conflict"


class RateLimitError(ForgeFlowError):
    """Too many requests (429)."""

    status_code = 429
    error_type = "rate_limited"


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers that render every error as the standard envelope."""

    @app.exception_handler(ForgeFlowError)
    async def _handle_forgeflow_error(_request: Request, exc: ForgeFlowError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "type": "validation_error",
                    "message": "Request validation failed",
                    "detail": jsonable_encoder(exc.errors()),
                }
            },
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, _exc: Exception) -> JSONResponse:
        # Do not leak internals; details belong in logs, not the response body.
        return JSONResponse(
            status_code=500,
            content={"error": {"type": "system_error", "message": "Internal server error"}},
        )
