"""Command execution routes + the interactive terminal WS bridge (phase-13).

The programmatic ``run`` streams typed ``terminal.output``/``exec.status`` events over the phase-05
hub. The interactive terminal instead uses a **dedicated WS carrying raw bytes** so TTY fidelity
(control chars, colours, Ctrl-C) survives the trip:

  - client → server, **binary** frame  = stdin keystrokes
  - client → server, **text** frame    = JSON control, currently ``{"type":"resize","cols","rows"}``
  - server → client, **binary** frame  = TTY output
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.auth.deps import get_current_user_id
from app.core.config import get_config
from app.core.limits import expensive_rate_limit
from app.db.models import Project, User
from app.db.repos import ProjectRepo
from app.projects.service import ProjectService, parse_object_id
from app.realtime.router import WS_FORBIDDEN, WS_UNAUTHORIZED, authenticate_ws
from app.sandbox.exec import get_exec_service
from app.sandbox.runtime import TtyHandle
from app.sandbox.workspace import active_runtime_provider

logger = logging.getLogger(__name__)

router = APIRouter(tags=["exec"])


class ExecRequest(BaseModel):
    cmd: list[str] = Field(min_length=1)
    cwd: str = ""
    env: dict[str, str] | None = None
    timeout: float | None = Field(default=None, gt=0, le=3600)


class ExecResponse(BaseModel):
    exec_id: str
    exit_code: int
    duration_s: float
    timed_out: bool
    cancelled: bool
    output_ref: str | None


@router.post(
    "/projects/{project_id}/exec",
    response_model=ExecResponse,
    dependencies=[Depends(expensive_rate_limit)],
)
async def run_command(
    project_id: str,
    body: ExecRequest,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> ExecResponse:
    """Run a command to completion; output streams over the realtime channel meanwhile."""
    project = await ProjectService().get_owned(parse_object_id(project_id), user_id)
    outcome = await get_exec_service().run(
        project, body.cmd, cwd=body.cwd, env=body.env, timeout=body.timeout
    )
    return ExecResponse(
        exec_id=outcome.exec_id,
        exit_code=outcome.exit_code,
        duration_s=outcome.duration_s,
        timed_out=outcome.timed_out,
        cancelled=outcome.cancelled,
        output_ref=outcome.output_ref,
    )


@router.post("/projects/{project_id}/exec/{exec_id}/cancel", status_code=204)
async def cancel_command(
    project_id: str,
    exec_id: str,
    user_id: PydanticObjectId = Depends(get_current_user_id),
) -> None:
    project = await ProjectService().get_owned(parse_object_id(project_id), user_id)
    await get_exec_service().cancel(str(project.id), exec_id)


# ------------------------------------------------------------------ terminal WS


async def authorize_owned_project(user: User, project_id: str) -> Project | None:
    """Terminals attach to a real owned project only (no demo channel)."""
    try:
        oid = PydanticObjectId(project_id)
    except (InvalidId, ValueError, TypeError):
        return None
    project = await ProjectRepo().get(oid)
    if project is None or project.user_id != user.id:
        return None
    return project


async def _pump_tty_to_ws(tty: TtyHandle, websocket: WebSocket) -> None:
    """Forward TTY output to the socket, reading on a thread so the loop stays free."""
    while True:
        data = await asyncio.to_thread(tty.read, 4096)
        if not data:
            return  # shell exited
        await websocket.send_bytes(data)


def _apply_control(tty: TtyHandle, message: dict[str, Any]) -> None:
    if message.get("type") != "resize":
        return
    try:
        cols = int(message["cols"])
        rows = int(message["rows"])
    except (KeyError, TypeError, ValueError):
        return
    if 1 <= cols <= 500 and 1 <= rows <= 300:
        tty.resize(cols, rows)


@router.websocket("/ws/projects/{project_id}/terminal")
async def ws_terminal(
    websocket: WebSocket,
    project_id: str,
    token: str | None = Query(default=None),
    cols: int = Query(default=80, ge=1, le=500),
    rows: int = Query(default=24, ge=1, le=300),
) -> None:
    user = await authenticate_ws(token)
    if user is None:
        await websocket.close(code=WS_UNAUTHORIZED)
        return
    project = await authorize_owned_project(user, project_id)
    if project is None:
        await websocket.close(code=WS_FORBIDDEN)
        return

    await websocket.accept()

    runtime = await active_runtime_provider()(project)
    shell = str(get_config().get("sandbox_shell"))
    tty = await asyncio.to_thread(runtime.start_tty, [shell], "", cols, rows)

    pump = asyncio.create_task(_pump_tty_to_ws(tty, websocket))
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                await asyncio.to_thread(tty.write, data)  # keystrokes (incl. Ctrl-C = 0x03)
                continue
            text = message.get("text")
            if text is not None:
                with contextlib.suppress(ValueError, TypeError):
                    _apply_control(tty, json.loads(text))
    except WebSocketDisconnect:
        pass
    finally:
        # Never leave an orphaned shell behind when the socket goes away.
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        await asyncio.to_thread(tty.close)
