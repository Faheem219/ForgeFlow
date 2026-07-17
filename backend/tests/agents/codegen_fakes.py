"""Shared helpers for the codegen agent tests (phase-23).

Not a test module (no ``test_`` prefix) so pytest doesn't collect it. Reuses the scripted transport
from the phase-20 client tests and the sandbox fakes from the phase-21 tool tests, so codegen is
driven entirely by a mock model over the real client + real tool registry.
"""

from __future__ import annotations

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


def build_transport(
    plan_text: str, tool_uses: list[ToolUse], final_text: str = "Done."
) -> FakeTransport:
    """A scripted 2–3 turn build: plan (Haiku) → implement-with-tools (Sonnet) → final summary.

    The agent makes two ``run_tool_loop`` calls over one transport: the first consumes the plan
    turn, the second consumes the implement turn(s). With no tools the implement loop finishes on
    the summary turn directly.
    """
    turns = [
        ScriptedTurn(deltas=[plan_text], turn=TurnComplete(text=plan_text, usage=Usage(50, 20)))
    ]
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
    return FakeTransport(turns)


async def make_project_run(name: str = "Todo App") -> tuple[Project, Run]:
    project = await Project(user_id=PydanticObjectId(), name=name, app_db_name="db").insert()
    run = await Run(project_id=project.id, kind="codegen").insert()
    return project, run
