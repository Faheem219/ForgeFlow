from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app


def test_health_returns_ok_envelope() -> None:
    client = TestClient(create_app())

    resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "env" in body
    assert "version" in body
