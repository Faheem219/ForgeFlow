"""Blob store round-trips, parametrized across both backends (phase-07)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.errors import SystemError  # noqa: A004 - taxonomy name fixed by the plan
from app.db.blobs import BlobStore


@pytest.fixture(params=["gridfs", "filesystem"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> BlobStore:
    if request.param == "gridfs":
        # getfixturevalue triggers the mongo_db fixture (which skips if Mongo is unreachable).
        mongo_db: AsyncIOMotorDatabase[dict[str, Any]] = request.getfixturevalue("mongo_db")
        return BlobStore(backend="gridfs", db=mongo_db)
    return BlobStore(backend="filesystem", fs_dir=str(tmp_path))


async def test_put_get_roundtrip(store: BlobStore) -> None:
    payload = b"the quick brown fox \x00\x01\x02 jumps"
    ref = await store.put(payload)
    assert await store.get(ref) == payload


async def test_put_yields_distinct_refs(store: BlobStore) -> None:
    a = await store.put(b"same")
    b = await store.put(b"same")
    assert a != b
    assert await store.get(a) == b"same"
    assert await store.get(b) == b"same"


async def test_delete_makes_blob_unretrievable(store: BlobStore) -> None:
    ref = await store.put(b"ephemeral")
    await store.delete(ref)
    with pytest.raises(SystemError):
        await store.get(ref)


async def test_delete_is_idempotent(store: BlobStore) -> None:
    ref = await store.put(b"x")
    await store.delete(ref)
    await store.delete(ref)  # second delete must not raise


async def test_refs_are_scheme_prefixed(store: BlobStore) -> None:
    ref = await store.put(b"x")
    assert ref.startswith(("gridfs:", "fs:"))


async def test_large_payload_roundtrip(store: BlobStore) -> None:
    payload = b"A" * (512 * 1024)  # 512 KiB, comfortably past the inline threshold
    ref = await store.put(payload)
    assert await store.get(ref) == payload
