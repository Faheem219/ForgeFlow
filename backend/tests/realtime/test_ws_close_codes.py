"""Regression test for the WS_UNAUTHORIZED/WS_FORBIDDEN close-code contract.

``fastapi.testclient.TestClient`` simulates the ASGI message layer directly and does not exercise
a real ASGI server's HTTP-upgrade handling, so it cannot catch a real defect that was found here:
uvicorn (and other ASGI servers) collapse ANY ``websocket.close`` sent before ``websocket.accept``
into a bare HTTP 403 upgrade rejection, discarding the application's chosen close code entirely.
That made ``WS_UNAUTHORIZED`` (4401) and ``WS_FORBIDDEN`` (4403) indistinguishable — and invisible
— to every real client. The fix is to accept the handshake before closing with the specific code.
This test runs a real uvicorn server on a loopback port and connects with a real WebSocket client
so the fix (and any regression of it) is actually observable.

The server under test mounts *only* ``realtime_router`` on a bare ``FastAPI()`` — deliberately
skipping ``app.api.app.create_app()`` and its ``lifespan``. That lifespan calls ``init_db()``,
``reap_orphaned_runs()`` and ``manager.reconcile()`` against whatever Mongo/Docker the *process
env* points at, which in a dev setup is the same real, shared instance a live control-plane
process (and its in-flight builds) may be using. Booting that here would race a real server's
state out from under it. The realtime router only needs Beanie bound to *a* database — which the
``mongo_db`` fixture already does — so the extra lifespan machinery is both unnecessary and unsafe
to run a second time from a test process.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import uvicorn
import websockets
from fastapi import FastAPI
from websockets.exceptions import ConnectionClosed

from app.auth.service import create_access_token
from app.db.models import User
from app.db.repos import UserRepo
from app.realtime.router import router as realtime_router

pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest.fixture
async def live_server() -> AsyncIterator[int]:
    """Boot a minimal real ASGI server (realtime router only) on a loopback OS-assigned port."""
    app = FastAPI()
    app.include_router(realtime_router)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):  # up to ~10s
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started, "uvicorn server did not start"
        port = server.servers[0].sockets[0].getsockname()[1]
        yield port
    finally:
        server.should_exit = True
        await task


async def test_unauthorized_close_code_reaches_a_real_client(live_server: int) -> None:
    uri = f"ws://127.0.0.1:{live_server}/ws/projects/anything?token=not-a-real-jwt&last_seq=0"
    with pytest.raises(ConnectionClosed) as exc_info:
        async with websockets.connect(uri) as ws:
            await ws.recv()
    assert exc_info.value.rcvd is not None
    assert exc_info.value.rcvd.code == 4401  # WS_UNAUTHORIZED


async def test_forbidden_close_code_reaches_a_real_client(live_server: int) -> None:
    email = f"ws-close-code-{uuid.uuid4().hex[:8]}@example.com"
    user = await UserRepo().insert(User(email=email, hashed_password="x"))
    token = create_access_token(str(user.id))
    # A well-formed but nonexistent/unowned project id → authenticated, not authorized.
    uri = (
        f"ws://127.0.0.1:{live_server}/ws/projects/000000000000000000000000"
        f"?token={token}&last_seq=0"
    )
    with pytest.raises(ConnectionClosed) as exc_info:
        async with websockets.connect(uri) as ws:
            await ws.recv()
    assert exc_info.value.rcvd is not None
    assert exc_info.value.rcvd.code == 4403  # WS_FORBIDDEN
