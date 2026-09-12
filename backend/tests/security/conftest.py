"""Shared helpers for the phase-47 security suite.

The route walker is the load-bearing piece: it discovers routes from the *live* app rather than a
hand-maintained list, so a new endpoint added in a later phase is audited automatically instead of
silently escaping the sweep.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app

#: Dependency names that establish an authenticated caller.
AUTH_DEPENDENCIES = frozenset({"get_current_user_id", "get_current_user", "require_admin"})

#: The only endpoints that may be reached without a token, and why.
PUBLIC_ROUTES = {
    ("POST", "/auth/register"),  # account creation
    ("POST", "/auth/login"),  # credential exchange
    ("GET", "/health"),  # liveness — no data, so operators can see the process is up
    # Prometheus scrape target (devops step 4). Deliberately token-free: it exposes only method,
    # route-template and status-code labels plus process counters — no user, project or secret
    # data (asserted by tests/core/test_metrics.py). Kept off the public internet by the reverse
    # proxy and firewall, which is the conventional split for an exposition endpoint.
    ("GET", "/metrics"),
}


def walk_routes(routes: list[Any]) -> Iterator[Any]:
    """Flatten the router tree, descending through FastAPI's included-router wrappers."""
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from walk_routes(original.routes)
        elif getattr(route, "routes", None):
            yield from walk_routes(route.routes)
        else:
            yield route


def _name(call: Any) -> str:
    return str(getattr(call, "__name__", type(call).__name__))


def dependency_names(dependant: Any, depth: int = 0) -> set[str]:
    """Every dependency callable reachable from a route, transitively."""
    names: set[str] = set()
    for dep in dependant.dependencies:
        if dep.call is not None:
            names.add(_name(dep.call))
        if depth < 5:
            names |= dependency_names(dep, depth + 1)
    return names


def api_routes(app: FastAPI) -> list[APIRoute]:
    return [r for r in walk_routes(app.routes) if isinstance(r, APIRoute)]


def websocket_routes(app: FastAPI) -> list[APIWebSocketRoute]:
    return [r for r in walk_routes(app.routes) if isinstance(r, APIWebSocketRoute)]


def route_methods(route: APIRoute) -> set[str]:
    return set(route.methods or ()) - {"HEAD", "OPTIONS"}


def required_query(route: APIRoute) -> dict[str, str]:
    """Plausible values for a route's **required** query params.

    Without these a request dies at validation (``422``) before the ownership check, which would
    make the sweep look like it passed while never testing authorization at all. Derived from the
    route signature so new required params are handled automatically.
    """
    values: dict[str, str] = {}
    for param in route.dependant.query_params:
        field_info = param.field_info
        required = field_info.is_required() if hasattr(field_info, "is_required") else False
        if not required:
            continue
        name = param.name
        annotation = str(getattr(field_info, "annotation", "")).lower()
        if "path" in name or "file" in name:
            values[name] = "a.txt"
        elif "int" in annotation:
            values[name] = "1"
        elif "bool" in annotation:
            values[name] = "false"
        else:
            values[name] = "x"
    return values


@pytest.fixture
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A real encryption key, so credential routes exercise the vault rather than 500ing."""
    from cryptography.fernet import Fernet

    from app.core.config import reset_config
    from app.deploy.secrets import reset_vault

    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("FERNET_KEY", key)
    reset_config()
    reset_vault()
    yield key
    reset_vault()


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest_asyncio.fixture
async def http(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def register(client: AsyncClient, email: str) -> str:
    """Create a user and return its bearer token."""
    resp = await client.post("/auth/register", json={"email": email, "password": "password123"})
    assert resp.status_code in (200, 201), resp.text
    return str(resp.json()["access_token"])


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def create_project(client: AsyncClient, token: str, name: str = "victim") -> str:
    resp = await client.post("/projects", json={"name": name}, headers=auth(token))
    assert resp.status_code in (200, 201), resp.text
    return str(resp.json()["id"])
