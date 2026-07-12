from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import ProviderError, UserError, register_exception_handlers


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/user-error")
    async def _user_error() -> None:
        raise UserError("bad input", detail={"field": "name"})

    @app.get("/provider-error")
    async def _provider_error() -> None:
        raise ProviderError("stitch quota exhausted")

    return app


def test_user_error_yields_standard_envelope() -> None:
    client = TestClient(_app())
    resp = client.get("/user-error")

    assert resp.status_code == 400
    assert resp.json() == {
        "error": {"type": "user_error", "message": "bad input", "detail": {"field": "name"}}
    }


def test_provider_error_maps_to_502_without_detail_key() -> None:
    client = TestClient(_app())
    resp = client.get("/provider-error")

    assert resp.status_code == 502
    assert resp.json() == {"error": {"type": "provider_error", "message": "stitch quota exhausted"}}
