"""A phase is `done` only with evidence (phase-64).

`finished ∧ typecheck green` used to be the whole gate. A loop that finished without writing a
single file left the workspace unchanged, so the typecheck was trivially green and the phase was
recorded done — seven times over on the reported build, with the report claiming 8/8 complete over
a workspace holding nothing but the template. Now: ≥1 file written + a closing summary + green
typecheck, with one explicit-feedback retry for a phase that wrote nothing, and an honest `failed`
after that.
"""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.agents.anthropic_client import (
    NUDGE_TRUNCATED,
    AnthropicClient,
    MessageRequest,
    StreamEvent,
    TextDelta,
    TurnComplete,
)
from app.agents.build_phases import NOOP_FEEDBACK, NOTE_NO_SUMMARY, NOTE_WROTE_NOTHING
from app.agents.build_plan import PHASE_DONE, PHASE_FAILED
from app.agents.codegen import BuildReport, CodegenAgent
from app.agents.cost import Usage
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.core.config import reset_config
from app.db.models import Project
from tests.agents.codegen_fakes import (
    configure_env,
    make_project_run,
    make_skeleton,
    phase_plan_json,
    phase_write,
)
from tests.agents.test_client_tool_loop import ScriptedTurn
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")

_PHASES = [("be-core", "Backend API", "backend"), ("fe-core", "Frontend pages", "frontend")]


class RecordingTransport:
    """Replays scripted turns and keeps a deep copy of every request (the loop reuses its list)."""

    def __init__(self, turns: list[ScriptedTurn]) -> None:
        self._turns = list(turns)
        self.requests: list[MessageRequest] = []

    async def stream(self, request: MessageRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(copy.deepcopy(request))
        script = self._turns.pop(0)
        for delta in script.deltas:
            yield TextDelta(delta)
        yield script.turn

    @property
    def exhausted(self) -> bool:
        return not self._turns


def _plan(*phases: tuple[str, str, str]) -> ScriptedTurn:
    text = phase_plan_json(*phases)
    return ScriptedTurn(deltas=[text], turn=TurnComplete(text=text, usage=Usage(50, 20)))


def _say(text: str) -> ScriptedTurn:
    return ScriptedTurn(deltas=[text], turn=TurnComplete(text=text, usage=Usage(40, 15)))


def _write(path: str) -> ScriptedTurn:
    return ScriptedTurn(
        deltas=[],
        turn=TurnComplete(
            tool_uses=phase_write(path), usage=Usage(200, 60), stop_reason="tool_use"
        ),
    )


def _last_user_text(request: MessageRequest) -> str:
    last = request.messages[-1]
    content = last["content"]
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if isinstance(b, dict))


async def _build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, transport: RecordingTransport
) -> tuple[BuildReport, Project, FakeWorkspace]:
    skeleton = tmp_path / "skeleton"
    if not skeleton.exists():
        make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)
    project, run = await make_project_run()
    ws = FakeWorkspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx
    )
    return report, project, ws


async def test_a_phase_that_writes_nothing_is_retried_once_with_feedback_then_failed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The model "finishes" twice without a single write — the reported failure's exact shape.
    transport = RecordingTransport([_plan(_PHASES[0]), _say("All done!"), _say("Done, really.")])

    report, _project, ws = await _build(monkeypatch, tmp_path, transport)

    assert transport.exhausted
    [phase] = report.phases
    assert phase.status == PHASE_FAILED
    assert phase.attempts == 2
    assert phase.note.startswith(NOTE_WROTE_NOTHING)
    assert phase.files == []
    assert report.outcome == "partial" and not report.phases_complete
    # The retry carried the feedback, naming the phase's goal.
    feedback = _last_user_text(transport.requests[2])
    assert NOOP_FEEDBACK.split("{")[0] in feedback
    assert "Implement Backend API." in feedback
    # Still committed (the plan checklist), so the trail is auditable — but as a failed phase.
    assert any(m.startswith("build(backend)") for m in ws.commits)
    assert "backend/src/features" not in "".join(ws.files)


async def test_the_retry_can_rescue_the_phase(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transport = RecordingTransport(
        [
            _plan(_PHASES[0]),
            _say("Everything is already in place."),  # a no-op "finish"
            _write("backend/src/features/todos/todos.ts"),  # the feedback turn gets a write
            _say("Wrote the todos router."),
        ]
    )

    report, _project, _ws = await _build(monkeypatch, tmp_path, transport)

    [phase] = report.phases
    assert phase.status == PHASE_DONE
    assert phase.attempts == 2
    assert phase.files == ["backend/src/features/todos/todos.ts"]
    assert phase.summary == "Wrote the todos router."


async def test_no_retry_when_configured_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BUILD_PHASE_NOOP_RETRIES", "0")
    reset_config()
    transport = RecordingTransport([_plan(_PHASES[0]), _say("All done!")])

    report, _project, _ws = await _build(monkeypatch, tmp_path, transport)

    assert transport.exhausted
    [phase] = report.phases
    assert phase.status == PHASE_FAILED and phase.attempts == 1
    assert phase.note.startswith(NOTE_WROTE_NOTHING)


async def test_a_phase_that_writes_and_then_goes_silent_fails_when_a_summary_is_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The client nudges an empty turn twice (LLM_MAX_TRUNCATED_TURNS) and then stops the loop;
    the phase runner must not read that as a finished phase."""
    monkeypatch.setenv("LLM_MAX_TRUNCATED_TURNS", "2")
    reset_config()
    transport = RecordingTransport(
        [_plan(_PHASES[0]), _write("backend/src/features/t/t.ts"), _say(""), _say(""), _say("")]
    )

    report, _project, _ws = await _build(monkeypatch, tmp_path, transport)

    assert transport.exhausted
    [phase] = report.phases
    assert phase.status == PHASE_FAILED
    assert phase.note.startswith(NOTE_NO_SUMMARY)
    assert phase.files == ["backend/src/features/t/t.ts"]  # the work is kept and reported
    assert NUDGE_TRUNCATED in _last_user_text(transport.requests[-1])


async def test_the_same_silence_is_synthesised_when_a_summary_is_optional(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LLM_MAX_TRUNCATED_TURNS", "2")
    monkeypatch.setenv("BUILD_PHASE_REQUIRE_SUMMARY", "false")
    reset_config()
    transport = RecordingTransport(
        [_plan(_PHASES[0]), _write("backend/src/features/t/t.ts"), _say(""), _say(""), _say("")]
    )

    report, _project, _ws = await _build(monkeypatch, tmp_path, transport)

    [phase] = report.phases
    assert phase.status == PHASE_DONE
    assert phase.summary == "wrote 1 file(s): backend/src/features/t/t.ts"
    assert phase.note == ""


async def test_each_phase_sees_what_the_previous_phases_wrote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The repository map used to be taken once, before the plan: phase 2 was told 'first build on
    a bare skeleton' while phase 1's files were already on disk."""
    transport = RecordingTransport(
        [
            _plan(*_PHASES),
            _write("backend/src/features/todos/todos.ts"),
            _say("Backend done."),
            _write("frontend/src/features/todos/TodoList.tsx"),
            _say("Frontend done."),
        ]
    )

    report, _project, _ws = await _build(monkeypatch, tmp_path, transport)

    assert [p.status for p in report.phases] == [PHASE_DONE, PHASE_DONE]
    phase_one = _last_user_text(transport.requests[1])
    phase_two = _last_user_text(transport.requests[3])
    assert "first build on a bare skeleton" in phase_one
    assert "backend/src/features/todos/todos.ts" in phase_two
    assert "first build on a bare skeleton" not in phase_two


async def test_the_next_build_resumes_at_the_phase_that_wrote_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End to end: build 1 records the no-op phase as failed; build 2 re-runs exactly it."""
    first = RecordingTransport(
        [
            _plan(*_PHASES),
            _write("backend/src/features/todos/todos.ts"),
            _say("Backend done."),
            _say("Frontend is done."),  # lies — wrote nothing
            _say("Really done."),  # …and again on the retry
        ]
    )
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)
    project, run = await make_project_run()
    ws = FakeWorkspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    report = await CodegenAgent(AnthropicClient(first), default_registry()).run(
        project, run, ctx=ctx
    )
    assert [(p.id, p.status) for p in report.phases] == [
        ("be-core", PHASE_DONE),
        ("fe-core", PHASE_FAILED),
    ]

    # Build 2: the same plan; only the frontend loop is scripted — if the agent re-ran the backend
    # phase it would exhaust the transport.
    second = RecordingTransport(
        [
            _plan(*_PHASES),
            _write("frontend/src/features/todos/TodoList.tsx"),
            _say("Frontend done."),
        ]
    )
    _project, run2 = await make_project_run()
    run2.project_id = project.id
    await run2.save()
    ctx2 = ToolContext.build(
        project, run2, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    report2 = await CodegenAgent(AnthropicClient(second), default_registry()).run(
        project, run2, ctx=ctx2
    )
    assert [(p.id, p.status) for p in report2.phases] == [
        ("be-core", PHASE_DONE),
        ("fe-core", PHASE_DONE),
    ]
    assert any("Resuming at phase 2" in note for note in report2.notes)
    assert report2.phases_complete
