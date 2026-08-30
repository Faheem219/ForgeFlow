"""Path traversal / absolute / symlink-escape / oversize rejection (the security criterion)."""

from __future__ import annotations

import os

import pytest

from app.core.errors import UserError
from app.sandbox.fs import WorkspaceFs
from app.sandbox.paths import require_rel_path, safe_rel_path
from app.sandbox.runtime import LocalRuntime
from tests.sandbox.conftest import requires_posix_runtime

pytestmark = requires_posix_runtime


@pytest.mark.parametrize(
    "raw",
    [
        "../etc/passwd",
        "a/../../b",
        "/etc/passwd",
        "/abs",
        "~/secrets",
        "foo/../../bar",
    ],
)
def test_traversal_and_absolute_rejected(raw: str) -> None:
    with pytest.raises(UserError):
        safe_rel_path(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("src/app.ts", "src/app.ts"),
        ("./src/./app.ts", "src/app.ts"),
        ("a//b///c", "a/b/c"),
        (".", ""),
        ("  src/x.ts  ", "src/x.ts"),
    ],
)
def test_normalization(raw: str, expected: str) -> None:
    assert safe_rel_path(raw) == expected


def test_null_byte_rejected() -> None:
    with pytest.raises(UserError):
        safe_rel_path("a\x00b")


def test_require_rel_path_rejects_root() -> None:
    with pytest.raises(UserError):
        require_rel_path(".")


async def test_symlink_escape_rejected(tmp_path: object) -> None:
    root = os.path.join(str(tmp_path), "workspace")
    outside = os.path.join(str(tmp_path), "outside")
    os.makedirs(root)
    os.makedirs(outside)
    with open(os.path.join(outside, "secret.txt"), "w") as handle:
        handle.write("top secret")

    # A symlink inside the workspace pointing outside it.
    os.symlink(outside, os.path.join(root, "escape"))

    fs = WorkspaceFs(LocalRuntime(root), "p1", max_bytes=1_000_000)

    # Reading/writing *through* the symlink must be rejected by the resolved-path guard.
    with pytest.raises(UserError):
        await fs.read_file("escape/secret.txt")
    with pytest.raises(UserError):
        await fs.write_file("escape/pwned.txt", "x")


async def test_oversize_write_rejected(tmp_path: object) -> None:
    fs = WorkspaceFs(LocalRuntime(os.path.join(str(tmp_path), "ws")), "p1", max_bytes=8)
    with pytest.raises(UserError):
        await fs.write_file("big.txt", "this is definitely more than eight bytes")


async def test_oversize_read_rejected(tmp_path: object) -> None:
    root = os.path.join(str(tmp_path), "ws")
    rt = LocalRuntime(root)
    rt.write_bytes("big.txt", b"0123456789")
    fs = WorkspaceFs(rt, "p1", max_bytes=4)
    with pytest.raises(UserError):
        await fs.read_file("big.txt")
