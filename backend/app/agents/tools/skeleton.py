"""Skeleton instantiation (phase-21).

A deterministic copy of the prebuilt generated-app scaffold (``templates/app-skeleton``, phase-22)
into ``/workspace``. Exposed both as a tool and as a direct service call (phase-23 codegen calls it
first) so the LLM never regenerates the FE/BE scaffold — a token saver per the user's directive.

The copy and the *derived path set* (:func:`skeleton_relpaths`, phase-54) share one walk
(:func:`_iter_skeleton_files`), so the files written into a workspace and the set the codegen agent
treats as "shipped by the skeleton, not feature code" can never drift apart.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from app.agents.tools.context import WorkspaceApi
from app.core.config import get_config
from app.core.errors import UserError
from app.db.models import Project

# app/agents/tools/skeleton.py → parents: [tools, agents, app, backend, <repo root>]
_REPO_ROOT = Path(__file__).resolve().parents[4]

# Directories that never belong in a freshly-instantiated workspace: dependencies, build output,
# VCS, and test/coverage artifacts. A git-checkout of the template (what the real pipeline copies)
# has none of these, so pruning them is a no-op there; it only bites on a developer machine where
# someone ran `pnpm install`/`build` inside the template dir, leaving ~15k node_modules files that
# would otherwise be walked (and copied) on every build. Pruning keeps :func:`copy_skeleton` and
# :func:`skeleton_relpaths` fast and in lock-step.
_SKELETON_IGNORE_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        "dist",
        "build",
        ".cache",
        "coverage",
        ".turbo",
        ".pnpm-store",
        "playwright-report",
        "test-results",
    }
)


def skeleton_source_dir() -> Path:
    raw = str(get_config().get("app_skeleton_dir"))
    path = Path(raw)
    return path if path.is_absolute() else _REPO_ROOT / raw


def _iter_skeleton_files(src: Path) -> Iterator[tuple[str, str]]:
    """Yield ``(relpath, text)`` for every UTF-8 text file the skeleton ships.

    The single walk both :func:`copy_skeleton` and :func:`skeleton_relpaths` consume, so the set of
    files copied into a workspace and the set derived as "shipped by the skeleton" cannot disagree.
    ``os.walk`` with in-place ``dirnames`` pruning never *descends* into ignored dirs (unlike
    ``rglob``, which would enumerate all of ``node_modules`` before filtering). Binary assets are
    skipped — the fixed-stack skeleton is text and the FS tool layer is UTF-8 only.
    """
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKELETON_IGNORE_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # skip binary asset
            yield path.relative_to(src).as_posix(), text


async def copy_skeleton(
    workspace: WorkspaceApi, project: Project, source: Path | None = None
) -> list[str]:
    """Copy every text file of the skeleton into the project's workspace. Returns the paths written.

    Deterministic: files are written in sorted relative-path order, so two runs produce identical
    workspaces. Binary assets are skipped (see :func:`_iter_skeleton_files`).
    """
    src = source or skeleton_source_dir()
    if not src.is_dir():
        raise UserError(f"App skeleton not found at {src}")

    written: list[str] = []
    for rel, text in sorted(_iter_skeleton_files(src)):
        await workspace.write(project, rel, text)
        written.append(rel)
    return written


@lru_cache(maxsize=4)
def _relpaths_for(src: str) -> frozenset[str]:
    """The derived path set for one source dir. Cached — read once per source, per process."""
    return frozenset(rel for rel, _ in _iter_skeleton_files(Path(src)))


def skeleton_relpaths() -> frozenset[str]:
    """Every path the skeleton ships, derived from the source dir (never hand-maintained).

    The single source of truth for "which files are scaffold". Whatever :func:`copy_skeleton` would
    write, this returns — so a scaffold file can never be mistaken for feature code, and the set can
    never silently drift from the template as it did before phase-54.
    """
    src = skeleton_source_dir()
    if not src.is_dir():
        raise UserError(f"App skeleton not found at {src}")
    return _relpaths_for(str(src))
