"""Rate limiting + request-size caps (phase-47).

Two abuse shapes, two controls, asserted end-to-end through the real app:

- unauthenticated endpoints are limited **per IP** (no user exists yet to charge);
- expensive authenticated endpoints are limited **per user**, because per-IP would both punish
  users behind shared NAT and be trivially evaded.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.config import reset_config
from app.core.errors import RateLimitError
from app.core.limits import RateLimiter, reset_limits
from tests.security.conftest import (
    api_routes,
    auth,
    create_project,
    dependency_names,
    register,
    route_methods,
)

pytestmark = pytest.mark.usefixtures("mongo_db")

#: Endpoints that spend money or capacity and must therefore be per-user limited.
EXPENSIVE_ROUTES = {
    ("POST", "/projects/{project_id}/intent"),
    ("POST", "/projects/{project_id}/requirements/suggest"),
    ("POST", "/projects/{project_id}/requirements/draft"),
    ("POST", "/projects/{project_id}/exec"),
    ("POST", "/projects/{project_id}/sandbox/ensure"),
    ("POST", "/projects/{project_id}/tests/run"),
    ("POST", "/projects/{project_id}/repair/run"),
    ("POST", "/projects/{project_id}/infra/analyze"),
    ("POST", "/projects/{project_id}/design/images"),
}


@pytest.fixture(autouse=True)
def _clean_limits() -> None:
    reset_limits()


# ---------------------------------------------------------------- the limiter itself


def test_the_limiter_allows_up_to_the_limit_then_refuses() -> None:
    limiter = RateLimiter()
    for _ in range(3):
        limiter.check("k", 3)
    with pytest.raises(RateLimitError):
        limiter.check("k", 3)


def test_limits_are_keyed_independently() -> None:
    """One noisy user must not exhaust another's budget."""
    limiter = RateLimiter()
    for _ in range(3):
        limiter.check("user:a", 3)
    limiter.check("user:b", 3)  # untouched budget


def test_a_zero_limit_disables_the_control() -> None:
    limiter = RateLimiter()
    for _ in range(100):
        limiter.check("k", 0)


def test_the_window_rolls_off(monkeypatch: pytest.MonkeyPatch) -> None:
    limiter = RateLimiter()
    clock = [1000.0]
    monkeypatch.setattr("app.core.limits.time.monotonic", lambda: clock[0])

    for _ in range(2):
        limiter.check("k", 2)
    with pytest.raises(RateLimitError):
        limiter.check("k", 2)

    clock[0] += 61.0  # past the window
    limiter.check("k", 2)


# ---------------------------------------------------------------- auth endpoints (per IP)


async def test_auth_endpoints_are_rate_limited(
    http: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_RATE_LIMIT_PER_MINUTE", "3")
    reset_config()

    statuses = [
        (await http.post("/auth/login", json={"email": "a@b.com", "password": "wrong"})).status_code
        for _ in range(5)
    ]
    assert 429 in statuses, statuses
    assert statuses[-1] == 429


async def test_the_rate_limit_error_is_the_standard_envelope(
    http: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_RATE_LIMIT_PER_MINUTE", "1")
    reset_config()

    await http.post("/auth/login", json={"email": "a@b.com", "password": "wrong"})
    limited = await http.post("/auth/login", json={"email": "a@b.com", "password": "wrong"})

    assert limited.status_code == 429
    assert limited.json()["error"]["type"] == "rate_limited"


# ---------------------------------------------------------------- expensive endpoints (per user)


def test_every_expensive_endpoint_declares_the_per_user_limit(app: FastAPI) -> None:
    """Pinned so a future phase cannot add a costly endpoint without a limit — or drop one."""
    limited = {
        (method, route.path)
        for route in api_routes(app)
        if "expensive_rate_limit" in dependency_names(route.dependant)
        for method in route_methods(route)
    }
    assert limited == EXPENSIVE_ROUTES


async def test_an_expensive_endpoint_refuses_a_burst(
    http: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXPENSIVE_RATE_LIMIT_PER_MINUTE", "2")
    reset_config()

    token = await register(http, "burst@example.com")
    project_id = await create_project(http, token)
    body = {"name": "F", "description": "d"}

    statuses = [
        (
            await http.post(
                f"/projects/{project_id}/requirements/suggest", json=body, headers=auth(token)
            )
        ).status_code
        for _ in range(4)
    ]
    assert statuses[-1] == 429, statuses


async def test_one_users_burst_does_not_limit_another(
    http: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason the bucket is keyed by user: noisy neighbours stay isolated."""
    monkeypatch.setenv("EXPENSIVE_RATE_LIMIT_PER_MINUTE", "2")
    reset_config()

    noisy = await register(http, "noisy@example.com")
    quiet = await register(http, "quiet@example.com")
    noisy_project = await create_project(http, noisy)
    quiet_project = await create_project(http, quiet)
    body = {"name": "F", "description": "d"}

    for _ in range(4):
        await http.post(
            f"/projects/{noisy_project}/requirements/suggest", json=body, headers=auth(noisy)
        )

    response = await http.post(
        f"/projects/{quiet_project}/requirements/suggest", json=body, headers=auth(quiet)
    )
    assert response.status_code != 429


async def test_the_expensive_limit_does_not_block_logging_in(
    http: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Separate buckets: burning the expensive budget must not lock a user out of auth."""
    monkeypatch.setenv("EXPENSIVE_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("AUTH_RATE_LIMIT_PER_MINUTE", "50")
    reset_config()

    token = await register(http, "locked@example.com")
    project_id = await create_project(http, token)
    for _ in range(3):
        await http.post(
            f"/projects/{project_id}/requirements/suggest",
            json={"name": "F"},
            headers=auth(token),
        )

    login = await http.post(
        "/auth/login", json={"email": "locked@example.com", "password": "password123"}
    )
    assert login.status_code == 200


# ---------------------------------------------------------------- request size cap


async def test_an_oversized_body_is_refused(
    http: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BYTES", "512")
    reset_config()

    # A fresh app so the middleware picks up the tightened cap.
    from httpx import ASGITransport
    from httpx import AsyncClient as Client

    from app.api.app import create_app

    transport = ASGITransport(app=create_app())
    async with Client(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/register", json={"email": "big@example.com", "password": "x" * 4096}
        )

    assert response.status_code == 413
    assert response.json()["error"]["type"] == "user_error"


async def test_a_normal_body_passes_the_cap(
    http: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_REQUEST_BYTES", "8192")
    reset_config()

    from httpx import ASGITransport
    from httpx import AsyncClient as Client

    from app.api.app import create_app

    transport = ASGITransport(app=create_app())
    async with Client(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/auth/register", json={"email": "small@example.com", "password": "password123"}
        )

    assert response.status_code in (200, 201)


def test_the_cap_admits_a_full_design_image_batch() -> None:
    """The global cap must not be tighter than a legitimate multi-screenshot upload (phase-19)."""
    reset_config()
    from app.core.config import get_config

    config = get_config()
    batch = int(config.get("design_max_images")) * int(config.get("design_max_image_bytes"))
    assert int(config.get("max_request_bytes")) > batch
