"""Git-backed workspace: init / commit / sha / diff / last-passing correctness (real git)."""

from __future__ import annotations

import os
import shutil

import pytest

from app.sandbox.git import WorkspaceGit
from app.sandbox.runtime import LocalRuntime

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture
def workspace(tmp_path: object) -> tuple[LocalRuntime, WorkspaceGit]:
    root = os.path.join(str(tmp_path), "ws")
    rt = LocalRuntime(root)
    return rt, WorkspaceGit(rt)


async def test_ensure_init_is_idempotent(workspace: tuple[LocalRuntime, WorkspaceGit]) -> None:
    rt, git = workspace
    assert not await git.is_initialized()
    await git.ensure_init()
    assert await git.is_initialized()
    await git.ensure_init()  # second call is a no-op
    assert rt.exists(".git")


async def test_commit_records_and_reports_sha(
    workspace: tuple[LocalRuntime, WorkspaceGit],
) -> None:
    rt, git = workspace
    await git.ensure_init()
    rt.write_bytes("a.txt", b"one\n")

    sha, created = await git.commit("first")
    assert created is True
    assert sha is not None and len(sha) == 40
    assert await git.current_sha() == sha


async def test_commit_noop_when_nothing_changed(
    workspace: tuple[LocalRuntime, WorkspaceGit],
) -> None:
    rt, git = workspace
    await git.ensure_init()
    rt.write_bytes("a.txt", b"one\n")
    sha1, _ = await git.commit("first")

    sha2, created = await git.commit("again")
    assert created is False
    assert sha2 == sha1


async def test_diff_between_shas(workspace: tuple[LocalRuntime, WorkspaceGit]) -> None:
    rt, git = workspace
    await git.ensure_init()
    rt.write_bytes("a.txt", b"one\n")
    sha1, _ = await git.commit("first")

    rt.write_bytes("a.txt", b"one\ntwo\n")
    sha2, _ = await git.commit("second")

    assert sha1 is not None and sha2 is not None
    diff = await git.diff(sha1, sha2)
    assert "+two" in diff
    assert "a.txt" in diff

    scoped = await git.diff(sha1, sha2, paths=["a.txt"])
    assert "+two" in scoped


async def test_changed_paths_between_shas(workspace: tuple[LocalRuntime, WorkspaceGit]) -> None:
    rt, git = workspace
    await git.ensure_init()
    rt.write_bytes("a.txt", b"one\n")
    sha1, _ = await git.commit("first")

    rt.write_bytes("a.txt", b"one\ntwo\n")
    rt.write_bytes("b.txt", b"new\n")
    sha2, _ = await git.commit("second")

    assert sha1 is not None and sha2 is not None
    changed = await git.changed_paths(sha1, sha2)
    assert sorted(changed) == ["a.txt", "b.txt"]


async def test_last_passing_ref_roundtrip(
    workspace: tuple[LocalRuntime, WorkspaceGit],
) -> None:
    rt, git = workspace
    await git.ensure_init()
    rt.write_bytes("a.txt", b"one\n")
    sha, _ = await git.commit("first")
    assert sha is not None

    assert await git.last_passing() is None
    await git.set_last_passing(sha)
    assert await git.last_passing() == sha


async def test_commit_on_empty_repo_returns_none(
    workspace: tuple[LocalRuntime, WorkspaceGit],
) -> None:
    _, git = workspace
    sha, created = await git.commit("nothing here")
    assert sha is None
    assert created is False
