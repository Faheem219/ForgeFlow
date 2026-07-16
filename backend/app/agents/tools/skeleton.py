"""Skeleton instantiation (phase-21).

A deterministic copy of the prebuilt generated-app scaffold (``templates/app-skeleton``, phase-22)
into ``/workspace``. Exposed both as a tool and as a direct service call (phase-23 codegen calls it
first) so the LLM never regenerates the FE/BE scaffold — a token saver per the user's directive.
"""

from __future__ import annotations

from pathlib import Path

from app.agents.tools.context import WorkspaceApi
from app.core.config import get_config
from app.core.errors import UserError
from app.db.models import Project

# app/agents/tools/skeleton.py → parents: [tools, agents, app, backend, <repo root>]
_REPO_ROOT = Path(__file__).resolve().parents[4]


def skeleton_source_dir() -> Path:
    raw = str(get_config().get("app_skeleton_dir"))
    path = Path(raw)
    return path if path.is_absolute() else _REPO_ROOT / raw


async def copy_skeleton(
    workspace: WorkspaceApi, project: Project, source: Path | None = None
) -> list[str]:
    """Copy every text file of the skeleton into the project's workspace. Returns the paths written.

    Binary assets are skipped (the fixed-stack skeleton is text; the FS tool layer is UTF-8 only).
    """
    src = source or skeleton_source_dir()
    if not src.is_dir():
        raise UserError(f"App skeleton not found at {src}")

    written: list[str] = []
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # skip binary asset
        rel = path.relative_to(src).as_posix()
        await workspace.write(project, rel, text)
        written.append(rel)
    return written
