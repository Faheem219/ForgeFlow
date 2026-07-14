"""The tar pack/unpack helpers underpin DockerRuntime's read/write (get_archive/put_archive).

They are exercised here without a daemon so the production backend's trickiest plumbing is covered.
"""

from __future__ import annotations

from app.sandbox.runtime import make_tar, read_single_from_tar


def test_tar_roundtrip_text() -> None:
    data = b"export const x = 42;\n"
    tar = make_tar("app.ts", data)
    assert read_single_from_tar([tar]) == data


def test_tar_roundtrip_binary() -> None:
    data = bytes(range(256))
    tar = make_tar("blob.bin", data)
    # get_archive yields the tar in chunks — reassembly must be chunk-boundary agnostic.
    mid = len(tar) // 2
    assert read_single_from_tar([tar[:mid], tar[mid:]]) == data


def test_read_empty_tar_returns_empty() -> None:
    empty = make_tar("empty.txt", b"")
    assert read_single_from_tar([empty]) == b""
