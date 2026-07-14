"""Workspace path safety (phase-12).

The single choke-point that turns a caller-supplied path into a normalized workspace-relative
path, rejecting anything that could escape ``/workspace``. Purely lexical + side-effect free so
it is exhaustively unit-testable; the runtime layer additionally resolves symlinks at call time
(a lexical check cannot catch a symlink that points outside the tree).
"""

from __future__ import annotations

from pathlib import PurePosixPath

from app.core.errors import UserError


def safe_rel_path(raw: str) -> str:
    """Normalize ``raw`` to a clean workspace-relative POSIX path (``""`` == workspace root).

    Rejects absolute paths, home-relative (``~``) paths, ``..`` traversal, and NUL bytes.
    """
    if not isinstance(raw, str):
        raise UserError("A path is required")
    if "\x00" in raw:
        raise UserError("Invalid path")

    candidate = raw.strip()
    if candidate.startswith("/"):
        raise UserError("Absolute paths are not allowed")
    if candidate.startswith("~"):
        raise UserError("Home-relative paths are not allowed")

    parts: list[str] = []
    for segment in PurePosixPath(candidate).parts:
        if segment == "..":
            raise UserError("Path traversal is not allowed")
        if segment in (".", "/"):
            continue
        parts.append(segment)
    return "/".join(parts)


def require_rel_path(raw: str) -> str:
    """Like :func:`safe_rel_path` but also rejects the workspace root (a file op needs a target)."""
    rel = safe_rel_path(raw)
    if not rel:
        raise UserError("A file path is required")
    return rel
