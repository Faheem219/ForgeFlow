"""AuthZ sweep (phase-47): every protected route rejects anonymous and cross-user callers.

The routes are **discovered from the live app**, not listed by hand. That is the point: a route
added in a later phase is swept automatically, so the only way to ship an unprotected endpoint is
to add it to :data:`PUBLIC_ROUTES` deliberately — which is a reviewable diff.

Two properties are asserted per route:

1. *Authentication* — no token → ``401``.
2. *Authorization* — a **valid token for a different account** must never reach another user's
   project. The expected answer is ``404``, not ``403``: existence is not leaked (§7).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from tests.security.conftest import (
    AUTH_DEPENDENCIES,
    PUBLIC_ROUTES,
    api_routes,
    auth,
    create_project,
    dependency_names,
    register,
    required_query,
    route_methods,
    websocket_routes,
)

pytestmark = pytest.mark.usefixtures("mongo_db")

#: Statuses that all mean "you did not get someone else's data".
DENIED = {401, 403, 404, 422}

#: Plausible values for non-project path params, so a request reaches the ownership check rather
#: than dying in path parsing. All are well-formed but non-existent.
PARAM_FILLERS: dict[str, str] = {
    "artifact_id": "507f1f77bcf86cd799439011",
    "a": "507f1f77bcf86cd799439011",
    "b": "507f1f77bcf86cd799439012",
    "run_id": "507f1f77bcf86cd799439013",
    "attempt_id": "507f1f77bcf86cd799439014",
    "exec_id": "507f1f77bcf86cd799439015",
    "doc_id": "507f1f77bcf86cd799439016",
    "collection": "items",
    "kind": "vercel",
    "stage": "build",
}

#: Minimal bodies so a write route gets past request validation to the ownership check.
BODY_FOR_PATH: dict[str, dict[str, Any]] = {
    "/projects/{project_id}/intent": {"stage": "build", "action": "proceed"},
    "/projects/{project_id}/requirements": {
        "features": [{"name": "F", "acceptance_criteria": [{"text": "works"}]}]
    },
    "/projects/{project_id}/requirements/suggest": {"name": "F", "description": "d"},
    "/projects/{project_id}/requirements/draft": {"description": "a todo app"},
    "/projects/{project_id}/exec": {"cmd": "echo hi"},
    "/projects/{project_id}/fs/file": {"path": "a.txt", "content": "x"},
    "/projects/{project_id}/fs/dir": {"path": "sub"},
    "/projects/{project_id}/fs/move": {"src": "a.txt", "dst": "b.txt"},
    "/projects/{project_id}/tests/run": {"scope": "all"},
    "/projects/{project_id}/repair/run": {},
    "/projects/{project_id}/sandbox/git/commit": {"message": "m"},
    "/projects/{project_id}/data/collections/{collection}/docs": {"document": {"a": 1}},
    "/projects/{project_id}/data/collections/{collection}/docs/{doc_id}": {"document": {"a": 1}},
    "/projects/{project_id}/stages/{stage}/transition": {"action": "enter"},
}


def _fill(path: str, project_id: str) -> str:
    out = path.replace("{project_id}", project_id)
    for name, value in PARAM_FILLERS.items():
        out = out.replace("{" + name + "}", value)
    return out


def protected_route_cases(app: FastAPI) -> list[tuple[str, str, dict[str, str]]]:
    """(method, path-template, required-query-params) for every non-public route."""
    cases: list[tuple[str, str, dict[str, str]]] = []
    for route in api_routes(app):
        query = required_query(route)
        for method in route_methods(route):
            if (method, route.path) in PUBLIC_ROUTES:
                continue
            cases.append((method, route.path, query))
    return sorted(cases, key=lambda c: (c[1], c[0]))


# ---------------------------------------------------------------- static guarantees


def test_every_route_declares_an_auth_dependency(app: FastAPI) -> None:
    """No endpoint may exist without an auth dependency unless it is explicitly public."""
    missing = []
    for route in api_routes(app):
        if not (dependency_names(route.dependant) & AUTH_DEPENDENCIES):
            for method in route_methods(route):
                if (method, route.path) not in PUBLIC_ROUTES:
                    missing.append(f"{method} {route.path}")
    assert missing == [], f"Routes without authentication: {missing}"


def test_the_public_surface_is_exactly_what_we_expect(app: FastAPI) -> None:
    """Pin the unauthenticated surface so widening it is a deliberate, reviewable change."""
    public = set()
    for route in api_routes(app):
        if not (dependency_names(route.dependant) & AUTH_DEPENDENCIES):
            public |= {(m, route.path) for m in route_methods(route)}
    assert public == PUBLIC_ROUTES


def test_admin_routes_require_the_admin_role(app: FastAPI) -> None:
    for route in api_routes(app):
        if route.path.startswith("/admin"):
            assert "require_admin" in dependency_names(route.dependant), route.path


def test_websocket_routes_are_not_dependency_authenticated(app: FastAPI) -> None:
    """WS auth is in-handler (token query param) — asserted behaviourally below, not by DI."""
    assert {r.path for r in websocket_routes(app)} == {
        "/ws/projects/{project_id}",
        "/ws/projects/{project_id}/terminal",
    }


# ---------------------------------------------------------------- behavioural sweep


async def test_no_route_is_reachable_without_a_token(app: FastAPI, http: AsyncClient) -> None:
    failures: list[str] = []
    for method, path, query in protected_route_cases(app):
        url = _fill(path, "507f1f77bcf86cd799439011")
        response = await http.request(method, url, params=query, json=BODY_FOR_PATH.get(path))
        if response.status_code != 401:
            failures.append(f"{method} {path} -> {response.status_code}")
    assert failures == [], f"Routes reachable without authentication: {failures}"


async def test_no_route_leaks_another_users_project(app: FastAPI, http: AsyncClient) -> None:
    """The core cross-tenant assertion, run against every project-scoped route."""
    owner = await register(http, "owner@example.com")
    intruder = await register(http, "intruder@example.com")
    project_id = await create_project(http, owner)

    failures: list[str] = []
    for method, path, query in protected_route_cases(app):
        if "{project_id}" not in path:
            continue
        url = _fill(path, project_id)
        response = await http.request(
            method, url, params=query, json=BODY_FOR_PATH.get(path), headers=auth(intruder)
        )
        if response.status_code not in DENIED:
            failures.append(f"{method} {path} -> {response.status_code} {response.text[:120]}")
    assert failures == [], f"Cross-user access not denied: {failures}"


async def test_reads_of_another_users_project_are_404_not_403(
    app: FastAPI, http: AsyncClient
) -> None:
    """Existence must not leak: a GET on someone else's project is indistinguishable from absent."""
    owner = await register(http, "owner2@example.com")
    intruder = await register(http, "intruder2@example.com")
    project_id = await create_project(http, owner)

    failures: list[str] = []
    for method, path, query in protected_route_cases(app):
        if method != "GET" or "{project_id}" not in path:
            continue
        response = await http.request(
            method, _fill(path, project_id), params=query, headers=auth(intruder)
        )
        if response.status_code != 404:
            failures.append(f"GET {path} -> {response.status_code}")
    assert failures == [], f"Reads that leak existence (expected 404): {failures}"


async def test_a_forged_token_is_rejected(http: AsyncClient) -> None:
    for bad in ("", "not-a-jwt", "Bearer.nonsense.sig", "a.b.c"):
        response = await http.get("/projects", headers={"Authorization": f"Bearer {bad}"})
        assert response.status_code == 401


async def test_project_listing_is_scoped_to_the_caller(http: AsyncClient) -> None:
    owner = await register(http, "owner3@example.com")
    intruder = await register(http, "intruder3@example.com")
    await create_project(http, owner, "secret-project")

    listing = await http.get("/projects", headers=auth(intruder))
    assert listing.status_code == 200
    assert listing.json() == []
    assert "secret-project" not in listing.text


async def test_artifacts_of_another_users_project_are_not_readable(http: AsyncClient) -> None:
    """Artifacts are addressed by their own id — ownership resolves via the owning project."""
    owner = await register(http, "owner4@example.com")
    intruder = await register(http, "intruder4@example.com")
    project_id = await create_project(http, owner)

    await http.post(
        f"/projects/{project_id}/requirements",
        json={"features": [{"name": "F", "acceptance_criteria": [{"text": "works"}]}]},
        headers=auth(owner),
    )
    listed = await http.get(f"/projects/{project_id}/artifacts", headers=auth(owner))
    for artifact in listed.json():
        response = await http.get(f"/artifacts/{artifact['id']}", headers=auth(intruder))
        assert response.status_code == 404


async def test_credentials_are_per_user(http: AsyncClient, fernet_key: str) -> None:
    owner = await register(http, "owner5@example.com")
    intruder = await register(http, "intruder5@example.com")

    stored = await http.put(
        "/credentials/vercel", json={"secret": "tok_secret_value"}, headers=auth(owner)
    )
    assert stored.status_code == 200

    assert (await http.get("/credentials", headers=auth(intruder))).json() == []
    assert (await http.delete("/credentials/vercel", headers=auth(intruder))).status_code == 404
