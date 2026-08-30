"""FS round-trip through the service on a real (local) FS: write/read/list/delete/move."""

from __future__ import annotations

import os

import pytest

from app.core.errors import NotFoundError
from app.realtime.hub import get_hub
from app.sandbox.fs import WorkspaceFs
from app.sandbox.runtime import LocalRuntime
from tests.sandbox.conftest import requires_posix_runtime

pytestmark = requires_posix_runtime


@pytest.fixture
def fs(tmp_path: object) -> WorkspaceFs:
    return WorkspaceFs(LocalRuntime(os.path.join(str(tmp_path), "ws")), "p1", max_bytes=1_000_000)


async def test_write_then_read_is_identical(fs: WorkspaceFs) -> None:
    content = "export const x = 42;\nconst π = 'unicode ✓';\n"
    node = await fs.write_file("src/app.ts", content)
    assert node.path == "src/app.ts"
    assert node.type == "file"

    read = await fs.read_file("src/app.ts")
    assert read.content == content
    assert read.size == len(content.encode("utf-8"))


async def test_write_emits_fs_write_event(fs: WorkspaceFs) -> None:
    await fs.write_file("src/a.ts", "a")
    events = get_hub().replay("p1", 0)
    assert [e.event for e in events] == ["fs.write"]
    assert events[0].payload["path"] == "src/a.ts"


async def test_list_tree_reports_nested_files(fs: WorkspaceFs) -> None:
    await fs.write_file("src/a.ts", "a")
    await fs.write_file("src/nested/b.ts", "b")
    await fs.write_file("readme.md", "hi")

    tree = await fs.list_tree(".")
    paths = {n.path: n.type for n in tree}
    assert paths["src"] == "dir"
    assert paths["src/a.ts"] == "file"
    assert paths["src/nested/b.ts"] == "file"
    assert paths["readme.md"] == "file"


async def test_list_tree_of_subdir(fs: WorkspaceFs) -> None:
    await fs.write_file("src/a.ts", "a")
    await fs.write_file("src/b.ts", "b")
    tree = await fs.list_tree("src")
    files = {n.path for n in tree if n.type == "file"}
    assert files == {"src/a.ts", "src/b.ts"}


async def test_delete_removes_file(fs: WorkspaceFs) -> None:
    await fs.write_file("gone.ts", "x")
    await fs.delete("gone.ts")
    with pytest.raises(NotFoundError):
        await fs.read_file("gone.ts")


async def test_move_relocates_file(fs: WorkspaceFs) -> None:
    await fs.write_file("old/name.ts", "payload")
    await fs.move("old/name.ts", "new/renamed.ts")

    moved = await fs.read_file("new/renamed.ts")
    assert moved.content == "payload"
    with pytest.raises(NotFoundError):
        await fs.read_file("old/name.ts")


async def test_move_missing_source_is_not_found(fs: WorkspaceFs) -> None:
    with pytest.raises(NotFoundError):
        await fs.move("nope.ts", "dst.ts")


async def test_mkdir_creates_directory(fs: WorkspaceFs) -> None:
    node = await fs.mkdir("src/components")
    assert node.type == "dir"
    tree = await fs.list_tree(".")
    assert any(n.path == "src/components" and n.type == "dir" for n in tree)


async def test_read_missing_file_is_not_found(fs: WorkspaceFs) -> None:
    with pytest.raises(NotFoundError):
        await fs.read_file("does/not/exist.ts")
