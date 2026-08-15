"""WebSocket transport for the realtime hub + a sample progress stream (demo)."""

from __future__ import annotations

import asyncio
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from app.auth.deps import get_current_user
from app.auth.service import AuthService, decode_token
from app.core.config import get_config
from app.core.errors import AuthError
from app.db.models import User
from app.db.repos import ProjectRepo
from app.realtime.hub import emit, get_hub
from app.realtime.schemas import Event, EventType

router = APIRouter()

# WS close codes (application range).
WS_UNAUTHORIZED = 4401
WS_FORBIDDEN = 4403


async def authenticate_ws(token: str | None) -> User | None:
    """Resolve the connecting user from a JWT (query param), or None if invalid."""
    if not token:
        return None
    try:
        payload = decode_token(token)
    except AuthError:
        return None
    subject = payload.get("sub")
    if not isinstance(subject, str):
        return None
    return await AuthService().get_user_by_id(subject)


async def authorize_project(user: User, project_id: str) -> bool:
    """Allow the per-user demo channel or a real project the user owns."""
    if project_id == f"demo-{user.id}":
        return True
    try:
        oid = PydanticObjectId(project_id)
    except (InvalidId, ValueError, TypeError):
        return False
    project = await ProjectRepo().get(oid)
    return project is not None and project.user_id == user.id


def _ping(project_id: str) -> dict[str, Any]:
    return Event(event=EventType.ping, project_id=project_id, seq=0).model_dump(mode="json")


async def _wait_disconnect(websocket: WebSocket) -> None:
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                return
    except WebSocketDisconnect:
        return


@router.websocket("/ws/projects/{project_id}")
async def ws_project(
    websocket: WebSocket,
    project_id: str,
    token: str | None = Query(default=None),
    last_seq: int = Query(default=0),
) -> None:
    user = await authenticate_ws(token)
    if user is None:
        # Closing before accept() gets swallowed by the ASGI server into a bare HTTP 403 upgrade
        # rejection (uvicorn's websockets_impl collapses ANY pre-accept close into 403, dropping
        # the code entirely) — no real client can ever read WS_UNAUTHORIZED off that. Accept first
        # so the close frame — and its code — actually reaches the client.
        await websocket.accept()
        await websocket.close(code=WS_UNAUTHORIZED)
        return
    if not await authorize_project(user, project_id):
        await websocket.accept()
        await websocket.close(code=WS_FORBIDDEN)
        return

    await websocket.accept()
    hub = get_hub()
    heartbeat = float(get_config().get("realtime_heartbeat_s"))

    async with hub.subscription(project_id) as queue:
        # Connected signal (also guarantees the subscription is live before producers emit).
        await websocket.send_json(_ping(project_id))

        # Replay missed events; track last_sent to dedup against the live queue.
        last_sent = last_seq
        for event in hub.replay(project_id, last_seq):
            await websocket.send_json(event.model_dump(mode="json"))
            last_sent = max(last_sent, event.seq)

        disconnect_task = asyncio.create_task(_wait_disconnect(websocket))
        try:
            while not disconnect_task.done():
                get_task: asyncio.Task[Event] = asyncio.create_task(queue.get())
                done, _pending = await asyncio.wait(
                    {get_task, disconnect_task},
                    timeout=heartbeat,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnect_task in done:
                    get_task.cancel()
                    break
                if get_task in done:
                    event = get_task.result()
                    if event.seq <= last_sent:
                        continue  # dedup replay/live overlap
                    last_sent = event.seq
                    await websocket.send_json(event.model_dump(mode="json"))
                else:
                    get_task.cancel()
                    await websocket.send_json(_ping(project_id))
        except WebSocketDisconnect:
            pass
        finally:
            disconnect_task.cancel()


@router.post("/realtime/demo/progress")
async def demo_progress(
    user: User = Depends(get_current_user),
    steps: int = Query(default=5, ge=1, le=50),
    delay: float = Query(default=0.3, ge=0.0, le=2.0),
) -> dict[str, Any]:
    """Kick off a sample progress stream on the caller's demo channel (transport demo)."""
    project_id = f"demo-{user.id}"
    asyncio.create_task(_run_demo(project_id, steps, delay))
    return {"project_id": project_id, "steps": steps}


async def _run_demo(project_id: str, steps: int, delay: float) -> None:
    for i in range(1, steps + 1):
        await emit(
            project_id,
            EventType.progress,
            {"step": i, "total": steps, "message": f"Step {i} of {steps}", "done": i == steps},
        )
        if delay > 0:
            await asyncio.sleep(delay)
