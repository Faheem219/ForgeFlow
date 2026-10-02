"""Datasets are pinned: a cached file is reused only while it matches; a changed upstream fails."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.eval.bench.datasets import DatasetError, Source, fetch

GOOD = b"pinned bytes"
SOURCE = Source("x.json", "https://example.invalid/x.json", hashlib.sha256(GOOD).hexdigest())


class _Response:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


def _serve(monkeypatch: pytest.MonkeyPatch, content: bytes) -> list[str]:
    calls: list[str] = []

    def get(url: str, **_: Any) -> _Response:
        calls.append(url)
        return _Response(content)

    monkeypatch.setattr(httpx, "get", get)
    return calls


def test_a_download_is_verified_then_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _serve(monkeypatch, GOOD)

    assert fetch(SOURCE, tmp_path) == GOOD
    assert fetch(SOURCE, tmp_path) == GOOD
    assert len(calls) == 1  # the second read came from the verified cache
    assert (tmp_path / "x.json").read_bytes() == GOOD


def test_a_changed_upstream_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, b"different bytes")

    with pytest.raises(DatasetError, match="pinned checksum"):
        fetch(SOURCE, tmp_path)
    assert not (tmp_path / "x.json").exists()


def test_a_tampered_cache_is_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "x.json").write_bytes(b"tampered")
    _serve(monkeypatch, GOOD)

    assert fetch(SOURCE, tmp_path) == GOOD


def test_a_network_failure_is_a_dataset_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(url: str, **_: Any) -> None:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(DatasetError, match="could not download"):
        fetch(SOURCE, tmp_path)
