"""Admin authz: every /admin/config route requires the admin role (phase-51)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.db.models.enums import UserRole
from tests.config.conftest import auth, make_user, token_for

pytestmark = pytest.mark.usefixtures("mongo_db")

ROUTES = [
    ("GET", "/admin/config"),
    ("GET", "/admin/config/effective"),
    ("GET", "/admin/config/model_routing"),
    ("PUT", "/admin/config/model_routing"),
    ("DELETE", "/admin/config/model_routing"),
]


async def test_anonymous_is_rejected(http: AsyncClient) -> None:
    for method, path in ROUTES:
        resp = await http.request(method, path, json={"value": "x"})
        assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


async def test_a_non_admin_user_gets_403(http: AsyncClient) -> None:
    user = await make_user(UserRole.user, "plain@example.com")
    headers = auth(await token_for(http, user))

    for method, path in ROUTES:
        resp = await http.request(method, path, json={"value": "x"}, headers=headers)
        assert resp.status_code == 403, f"{method} {path} -> {resp.status_code}"


async def test_an_admin_is_allowed(admin_client: tuple[AsyncClient, dict[str, str]]) -> None:
    http, headers = admin_client
    resp = await http.get("/admin/config", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list) and resp.json()

    resp = await http.put(
        "/admin/config/model_routing", json={"value": "claude-haiku-x"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == "claude-haiku-x"
    assert resp.json()["source"] == "db"


async def test_every_admin_config_route_declares_require_admin() -> None:
    """A static guard so a future route can't be added without the role gate."""
    from app.api.app import create_app
    from tests.security.conftest import api_routes, dependency_names

    app = create_app()
    offenders = [
        f"{sorted(r.methods or ())} {r.path}"
        for r in api_routes(app)
        if r.path.startswith("/admin/config")
        and "require_admin" not in dependency_names(r.dependant)
    ]
    assert offenders == [], f"admin-config routes missing require_admin: {offenders}"
