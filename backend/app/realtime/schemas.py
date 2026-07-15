"""Typed realtime event contract (§7)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.db.models.common import utcnow
from app.db.models.enums import Stage


class EventType(StrEnum):
    agent_token = "agent.token"
    fs_write = "fs.write"
    terminal_output = "terminal.output"
    exec_status = "exec.status"
    preview_status = "preview.status"
    test_result = "test.result"
    deploy_status = "deploy.status"
    sandbox_status = "sandbox.status"
    stage_transition = "stage.transition"
    progress = "progress"
    ping = "ping"


class Event(BaseModel):
    event: str  # an EventType value (or a future registered type)
    project_id: str
    stage: Stage | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    seq: int
    ts: datetime = Field(default_factory=utcnow)
