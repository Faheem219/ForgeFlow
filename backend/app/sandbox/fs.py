"""Workspace filesystem service (phase-12).

Runtime-agnostic: it applies path safety, the symlink-escape guard, size caps, and emits
``fs.write`` events, delegating the actual IO to a :class:`~app.sandbox.runtime.WorkspaceRuntime`.
"""

from __future__ import annotations

import asyncio

from app.core.errors import NotFoundError, UserError
from app.realtime.hub import emit
from app.realtime.schemas import EventType
from app.sandbox.paths import require_rel_path, safe_rel_path
from app.sandbox.runtime import WorkspaceRuntime
from app.sandbox.schemas import FileContent, FileNode

DEFAULT_TREE_DEPTH = 50


class WorkspaceFs:
    def __init__(self, runtime: WorkspaceRuntime, project_id: str, max_bytes: int) -> None:
        self._rt = runtime
        self._project_id = project_id
        self._max_bytes = max_bytes

    async def _guard(self, rel: str) -> None:
        """Reject a path whose *resolved* location (symlinks included) escapes the workspace."""
        real = await asyncio.to_thread(self._rt.real_path, rel)
        root = self._rt.workspace_root.rstrip("/")
        if real != root and not real.startswith(root + "/"):
            raise UserError("Path escapes workspace")

    async def list_tree(self, path: str = ".", depth: int | None = None) -> list[FileNode]:
        rel = safe_rel_path(path)
        window = depth if depth is not None else DEFAULT_TREE_DEPTH
        nodes = await asyncio.to_thread(self._rt.list_tree, rel, window)
        result = [
            FileNode(
                path=node.path,
                type="dir" if node.is_dir else "file",
                size=None if node.is_dir else node.size,
            )
            for node in nodes
        ]
        result.sort(key=lambda n: (n.type != "dir", n.path))
        return result

    async def read_file(self, path: str) -> FileContent:
        rel = require_rel_path(path)
        await self._guard(rel)
        try:
            size = await asyncio.to_thread(self._rt.size, rel)
        except FileNotFoundError as exc:
            raise NotFoundError("File not found") from exc
        if size > self._max_bytes:
            raise UserError(f"File exceeds the {self._max_bytes}-byte limit")
        data = await asyncio.to_thread(self._rt.read_bytes, rel)
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UserError("File is not UTF-8 text") from exc
        return FileContent(path=rel, content=content, size=size)

    async def write_file(self, path: str, content: str) -> FileNode:
        rel = require_rel_path(path)
        data = content.encode("utf-8")
        if len(data) > self._max_bytes:
            raise UserError(f"File exceeds the {self._max_bytes}-byte limit")
        await self._guard(rel)
        await asyncio.to_thread(self._rt.write_bytes, rel, data)
        await emit(self._project_id, EventType.fs_write, {"path": rel})
        return FileNode(path=rel, type="file", size=len(data))

    async def delete(self, path: str) -> None:
        rel = require_rel_path(path)
        await self._guard(rel)
        await asyncio.to_thread(self._rt.delete, rel)

    async def mkdir(self, path: str) -> FileNode:
        rel = require_rel_path(path)
        await self._guard(rel)
        await asyncio.to_thread(self._rt.mkdir, rel)
        return FileNode(path=rel, type="dir", size=None)

    async def move(self, src: str, dst: str) -> FileNode:
        src_rel = require_rel_path(src)
        dst_rel = require_rel_path(dst)
        await self._guard(src_rel)
        await self._guard(dst_rel)
        try:
            await asyncio.to_thread(self._rt.move, src_rel, dst_rel)
        except FileNotFoundError as exc:
            raise NotFoundError("Source path not found") from exc
        await emit(self._project_id, EventType.fs_write, {"path": dst_rel, "moved_from": src_rel})
        return FileNode(path=dst_rel, type="file", size=None)
