"""A change request extends the phase plan; it never replaces it (phase-67).

Reported 2026-10-02: refining a finished build re-planned the whole app. The plan file was written
from scratch, every phase was implemented again, and the full-build planner's mandatory `wiring`
phase rewrote routes and layout that already worked, breaking other features. Now a change keeps
the history, plans only what it adds (or, if small, makes the change directly), and runs only that.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.agents.anthropic_client import AnthropicClient, TurnComplete
from app.agents.build_phases import BuildPlanner, PhaseOutcome
from app.agents.build_plan import BuildPhase, BuildPlan
from app.agents.codegen import BUILD_REPORT_KIND, BuildReport, CodegenAgent
from app.agents.cost import Usage
from app.agents.prompts.codegen import CHANGE_PLAN_SYSTEM_EXTRA, PHASE_PLAN_SYSTEM_EXTRA
from app.agents.tools.context import ToolContext
from app.agents.tools.definitions import default_registry
from app.db.models.enums import ArtifactType, Stage
from app.orchestrator.artifacts import ArtifactService
from tests.agents.codegen_fakes import (
    _phase_turns,
    configure_env,
    make_project_run,
    make_skeleton,
    phase_plan_json,
    phase_write,
)
from tests.agents.test_client_tool_loop import FakeTransport, ScriptedTurn
from tests.agents.tools.conftest import FakeExec, FakePreview, FakeWorkspace

pytestmark = pytest.mark.usefixtures("mongo_db")

_BUILT = [
    ("be-core", "Todo API", "backend"),
    ("fe-core", "Todo pages", "frontend"),
    ("wiring", "Routing and identity", "wiring"),
]


def _text(text: str) -> ScriptedTurn:
    return ScriptedTurn(deltas=[text], turn=TurnComplete(text=text, usage=Usage(30, 10)))


async def _seed_built_app(project_id: Any, statuses: list[str] | None = None) -> None:
    """What a finished initial build leaves behind: its plan, and a report of its phases."""
    statuses = statuses or ["done", "done", "done"]
    plan = BuildPlan(
        phases=[
            BuildPhase(id=pid, title=title, kind=kind, goal=f"Build the {title}.")  # type: ignore[arg-type]
            for pid, title, kind in _BUILT
        ]
    )
    await BuildPlanner(client=None).persist(project_id, plan)
    report = BuildReport(
        boot_status="healthy",
        files_changed=["backend/src/features/todos/todo.routes.ts"],
        features_built=["Todos"],
        follow_ups=[],
        notes=[],
        commit="sha1",
        tests_passed=None,
        summary="Built the todo app.",
        plan=plan.to_prose(),
        phases=[
            PhaseOutcome(id=pid, title=title, kind=kind, status=status, files=["x.ts"])
            for (pid, title, kind), status in zip(_BUILT, statuses, strict=True)
        ],
        outcome="complete",
        scope="large",
    )
    await ArtifactService().create_version(
        project_id,
        Stage.build,
        ArtifactType.code_change,
        text=json.dumps(report.to_dict(), indent=2),
        meta={"kind": BUILD_REPORT_KIND},
    )


def _workspace() -> FakeWorkspace:
    ws = FakeWorkspace()
    ws.files["pnpm-workspace.yaml"] = "packages:\n"
    ws.files["backend/src/features/todos/todo.routes.ts"] = "export {};"
    ws.files["frontend/src/features/todos/TodoPage.tsx"] = "export {};"
    ws.files["phase-plan/plan.json"] = "ORIGINAL"
    return ws


async def _run_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    turns: list[ScriptedTurn],
    instruction: str,
    *,
    statuses: list[str] | None = None,
    ws: FakeWorkspace | None = None,
    seed: bool = True,
) -> tuple[BuildReport, FakeWorkspace, FakeTransport, Any]:
    skeleton = tmp_path / "skeleton"
    if not skeleton.exists():
        make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)
    project, run = await make_project_run()
    assert project.id is not None
    if seed:
        await _seed_built_app(project.id, statuses)
    ws = ws or _workspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )
    transport = FakeTransport(turns)
    report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
        project, run, ctx=ctx, instruction=instruction
    )
    return report, ws, transport, project


# -------------------------------------------------------------------------------- a large change


async def test_a_large_change_appends_its_phases_and_runs_only_those(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    change_plan = phase_plan_json(("be-tags", "Tag API", "backend"))
    turns = [
        _text("LARGE\nnew entity end to end"),
        _text(change_plan),
        *_phase_turns(phase_write("backend/src/features/tags/tag.routes.ts"), "Added tags."),
    ]

    report, ws, transport, _ = await _run_change(monkeypatch, tmp_path, turns, "add tags to todos")

    # Every earlier phase is still there, as it was, followed by the change's own phase.
    assert [(p.id, p.status) for p in report.phases] == [
        ("be-core", "done"),
        ("fe-core", "done"),
        ("wiring", "done"),
        ("be-tags", "done"),
    ]
    assert report.prior_phases == 3
    assert [p.id for p in report.run_phases] == ["be-tags"]
    assert report.phases_complete
    assert report.scope == "large"
    # Only the new phase was implemented and committed — nothing earlier was redone.
    assert [m for m in ws.commits if m.startswith("build(")] == ["build(backend): Tag API"]
    # The change planner, not the whole-app planner, was asked — and told the app exists.
    planner_request = transport.requests[1]
    assert CHANGE_PLAN_SYSTEM_EXTRA in str(planner_request.system)
    assert PHASE_PLAN_SYSTEM_EXTRA not in str(planner_request.system)
    assert "Todo API" in str(planner_request.messages[0]["content"])


async def test_the_plan_file_grows_a_section_instead_of_being_rewritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    turns = [
        _text("LARGE\nnew entity"),
        _text(phase_plan_json(("be-tags", "Tag API", "backend"))),
        *_phase_turns(phase_write("backend/src/features/tags/tag.ts"), "Added tags."),
    ]

    _, ws, _, project = await _run_change(monkeypatch, tmp_path, turns, "add tags to todos")

    plan = json.loads(ws.files["phase-plan/plan.json"])
    assert [p["id"] for p in plan["phases"]] == ["be-core", "fe-core", "wiring", "be-tags"]
    assert plan["outcomes"] == {
        "be-core": "done",
        "fe-core": "done",
        "wiring": "done",
        "be-tags": "done",
    }
    assert plan["changes"] == [{"instruction": "add tags to todos", "phase_ids": ["be-tags"]}]
    readme = ws.files["phase-plan/README.md"]
    assert readme.index("Todo API") < readme.index("## Change 1: add tags to todos")
    assert readme.index("## Change 1:") < readme.index("Tag API")
    # And the persisted plan — what the next change extends — is the merged one.
    stored = await BuildPlanner(client=None).latest(project.id)
    assert stored is not None
    assert [p.id for p in stored.phases] == ["be-core", "fe-core", "wiring", "be-tags"]


async def test_a_change_phase_reusing_an_earlier_id_is_renamed_not_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Resume keys on ids: a new `be-core` read as the old, done one would never run."""
    turns = [
        _text("LARGE\nbig"),
        _text(phase_plan_json(("be-core", "Tag API", "backend"))),
        *_phase_turns(phase_write("backend/src/features/tags/tag.ts"), "Added tags."),
    ]

    report, _, _, _ = await _run_change(monkeypatch, tmp_path, turns, "add tags")

    assert [p.id for p in report.phases] == ["be-core", "fe-core", "wiring", "be-core-2"]
    assert report.phases[-1].status == "done"


async def test_an_unusable_change_plan_becomes_one_direct_pass_not_a_whole_app_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The deterministic whole-app fallback is exactly the re-implementation this prevents."""
    turns = [
        _text("LARGE\nbig"),
        _text("I would add a tag model, then…"),  # no JSON
        *_phase_turns(phase_write("backend/src/features/tags/tag.ts"), "Added tags."),
    ]

    report, _, _, _ = await _run_change(monkeypatch, tmp_path, turns, "add tags")

    assert [p.id for p in report.run_phases] == ["change"]
    assert report.run_phases[0].title == "Change: add tags"
    assert len(report.phases) == 4


# -------------------------------------------------------------------------------- a small change


async def test_a_small_change_keeps_the_history_and_leaves_the_plan_file_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    turns = [
        _text("SMALL\none component"),
        *_phase_turns(phase_write("frontend/src/features/todos/TodoPage.tsx"), "Fixed it."),
    ]

    report, ws, transport, _ = await _run_change(
        monkeypatch, tmp_path, turns, "the sign-in button is missing"
    )

    assert [p.id for p in report.phases] == ["be-core", "fe-core", "wiring", "change"]
    assert report.prior_phases == 3
    assert report.phases_complete
    assert ws.files["phase-plan/plan.json"] == "ORIGINAL"  # no planner, no rewrite
    assert len(transport.requests) == 3  # classify, then one implement loop (two turns)


async def test_two_small_changes_in_a_row_both_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The synthetic id is stable; joined to history it must not collide with the last change."""
    skeleton = tmp_path / "skeleton"
    make_skeleton(skeleton)
    configure_env(monkeypatch, tmp_path, skeleton)
    project, run = await make_project_run()
    assert project.id is not None
    await _seed_built_app(project.id)
    ws = _workspace()
    ctx = ToolContext.build(
        project, run, workspace=ws, exec_service=FakeExec(), preview=FakePreview()
    )

    for n, instruction in enumerate(("fix the button", "fix the header"), start=1):
        transport = FakeTransport(
            [
                _text("SMALL\ncontained"),
                *_phase_turns(phase_write(f"frontend/src/features/todos/Fix{n}.tsx"), "Fixed."),
            ]
        )
        report = await CodegenAgent(AnthropicClient(transport), default_registry()).run(
            project, run, ctx=ctx, instruction=instruction
        )

    assert [p.id for p in report.phases][-2:] == ["change", "change-2"]
    assert report.prior_phases == 4
    assert report.run_phases[0].title == "Change: fix the header"
    assert report.phases_complete


async def test_an_unfinished_earlier_phase_does_not_hold_the_change_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The change is judged by the change; the old gap is named, and left for a Rebuild."""
    turns = [
        _text("SMALL\ncontained"),
        *_phase_turns(phase_write("frontend/src/features/todos/TodoPage.tsx"), "Fixed it."),
    ]

    report, _, _, _ = await _run_change(
        monkeypatch, tmp_path, turns, "fix the button", statuses=["done", "done", "failed"]
    )

    assert report.phases[2].status == "failed"  # carried as it was, not re-run
    assert report.phases_complete
    assert report.outcome == "complete"
    assert any("earlier phase(s) are still unfinished" in n for n in report.notes)


async def test_the_report_round_trips_its_history_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    turns = [
        _text("SMALL\ncontained"),
        *_phase_turns(phase_write("frontend/src/features/todos/TodoPage.tsx"), "Fixed it."),
    ]

    _, _, _, project = await _run_change(monkeypatch, tmp_path, turns, "fix the button")

    previous = await CodegenAgent()._previous_report(project.id)
    assert previous is not None
    assert previous.prior_phases == 3
    assert previous.scope == "small"
