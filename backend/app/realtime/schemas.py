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
    # Stream lifecycle. `agent.token` alone gives a client no way to know a stream ENDED, which
    # leaves any UI spinner driven by it running forever. These bracket every model turn and are
    # emitted from a ``finally`` so they still fire when the turn raises.
    agent_stream_start = "agent.stream.start"
    agent_stream_end = "agent.stream.end"
    # A reasoning model's streamed *thinking* (phase-64). Not assistant text — a client must not
    # render it as the answer — but a two-minute think with no event at all reads as a hang.
    agent_reasoning = "agent.reasoning"
    # Terminal signal for a whole costed unit of agent work (see app/db/models/run.py). The
    # frontend clears task-level busy state on this, independently of the HTTP response.
    run_finished = "run.finished"
    fs_write = "fs.write"
    terminal_output = "terminal.output"
    exec_status = "exec.status"
    preview_status = "preview.status"
    test_result = "test.result"
    # Build stage: the structured report (was an ad-hoc string) + the verify-step stream (phase-55).
    build_report = "build.report"
    build_verify = "build.verify"
    build_phase = "build.phase"  # one phase of the phased build (phase-56)
    deploy_status = "deploy.status"
    validate_status = "validate.status"
    sandbox_status = "sandbox.status"
    design_quota = "design.quota"
    # The design provider is waiting on a decision from the user (app/design/questions.py).
    design_question = "design.question"
    budget_halt = "budget.halt"
    budget_warning = "budget.warning"
    stage_transition = "stage.transition"
    # Lets any other client watching this project tear down its local state instead of polling a
    # project that no longer exists.
    project_deleted = "project.deleted"
    progress = "progress"
    ping = "ping"


class Event(BaseModel):
    event: str  # an EventType value (or a future registered type)
    project_id: str
    stage: Stage | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    seq: int
    ts: datetime = Field(default_factory=utcnow)
