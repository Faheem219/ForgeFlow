"""Shared helpers for the codegen agent tests (phase-23).

Not a test module (no ``test_`` prefix) so pytest doesn't collect it. Reuses the scripted transport
from the phase-20 client tests and the sandbox fakes from the phase-21 tool tests, so codegen is
driven entirely by a mock model over the real client + real tool registry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from beanie import PydanticObjectId

from app.agents.anthropic_client import ToolUse, TurnComplete
from app.agents.cost import Usage
from app.core.config import reset_config
from app.db.models import Project, Run
from tests.agents.test_client_tool_loop import FakeTransport, ScriptedTurn


def configure_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, skeleton_dir: Path) -> None:
    """Point the skeleton copy at ``skeleton_dir`` and use filesystem blobs (no gridfs needed)."""
    monkeypatch.setenv("APP_SKELETON_DIR", str(skeleton_dir))
    monkeypatch.setenv("BLOB_BACKEND", "filesystem")
    monkeypatch.setenv("BLOB_FS_DIR", str(tmp_path / "blobs"))
    reset_config()


def make_skeleton(root: Path) -> None:
    """A minimal but structurally-real skeleton (workspace markers + FE/BE scaffold files)."""
    (root / "frontend" / "src").mkdir(parents=True)
    (root / "backend" / "src").mkdir(parents=True)
    (root / "package.json").write_text('{"name":"forgeflow-app","private":true}', encoding="utf-8")
    (root / "pnpm-workspace.yaml").write_text(
        "packages:\n  - frontend\n  - backend\n", encoding="utf-8"
    )
    (root / "frontend" / "package.json").write_text('{"name":"frontend"}', encoding="utf-8")
    (root / "frontend" / "src" / "main.tsx").write_text("export {};", encoding="utf-8")
    (root / "backend" / "package.json").write_text('{"name":"backend"}', encoding="utf-8")
    (root / "backend" / "src" / "app.ts").write_text("export {};", encoding="utf-8")


def tool_use(name: str, args: dict[str, object]) -> ToolUse:
    return ToolUse(id=f"tu_{name}", name=name, input=args)


def phase_write(path: str) -> list[ToolUse]:
    """The minimum a phase must do to be `done` (phase-64): write one file."""
    return [tool_use("write_file", {"path": path, "content": "export {};"})]


def phase_plan_json(*phases: tuple[str, str, str]) -> str:
    """A phase-plan JSON payload (phase-56) from ``(id, title, kind)`` triples."""
    return json.dumps(
        {
            "phases": [
                {
                    "id": pid,
                    "title": title,
                    "kind": kind,
                    "goal": f"Implement {title}.",
                    "files": [],
                    "depends_on": [],
                    "done_when": [f"{kind} typechecks"],
                }
                for pid, title, kind in phases
            ],
            "assumptions": [],
            "notes": [],
        }
    )


def build_transport(
    plan_text: str, tool_uses: list[ToolUse], final_text: str = "Done."
) -> FakeTransport:
    """A scripted build: plan (Haiku) → ONE phase implemented with tools (Sonnet) → summary.

    Since phase-56 the agent runs one ``run_tool_loop`` *per phase*, so the plan turn here returns
    a **single-phase** JSON plan: exactly one implement loop follows, which keeps this the same
    2–3 turn script it has always been. ``plan_text`` becomes that phase's title, so tests that
    assert on the plan still see their own words. Use :func:`phased_transport` for N phases.
    """
    plan_json = phase_plan_json(("be-core", plan_text, "backend"))
    turns = [
        ScriptedTurn(deltas=[plan_json], turn=TurnComplete(text=plan_json, usage=Usage(50, 20)))
    ]
    turns += _phase_turns(tool_uses, final_text)
    return FakeTransport(turns)


def phased_transport(
    phases: list[tuple[str, str, str]],
    tool_uses_per_phase: list[list[ToolUse]],
    final_texts: list[str] | None = None,
) -> FakeTransport:
    """A scripted N-phase build: one plan turn, then one implement loop per phase."""
    plan_json = phase_plan_json(*phases)
    turns = [
        ScriptedTurn(deltas=[plan_json], turn=TurnComplete(text=plan_json, usage=Usage(50, 20)))
    ]
    for index, tool_uses in enumerate(tool_uses_per_phase):
        final = (final_texts or [])[index] if final_texts and index < len(final_texts) else "Done."
        turns += _phase_turns(tool_uses, final)
    return FakeTransport(turns)


def _phase_turns(tool_uses: list[ToolUse], final_text: str) -> list[ScriptedTurn]:
    turns: list[ScriptedTurn] = []
    if tool_uses:
        turns.append(
            ScriptedTurn(
                deltas=["Implementing…"],
                turn=TurnComplete(
                    text="Implementing…",
                    tool_uses=tool_uses,
                    usage=Usage(200, 60),
                    stop_reason="tool_use",
                ),
            )
        )
    turns.append(
        ScriptedTurn(deltas=[final_text], turn=TurnComplete(text=final_text, usage=Usage(40, 15)))
    )
    return turns


async def make_project_run(name: str = "Todo App") -> tuple[Project, Run]:
    project = await Project(user_id=PydanticObjectId(), name=name, app_db_name="db").insert()
    run = await Run(project_id=project.id, kind="codegen").insert()
    return project, run
