"""Blob storage for large artifact payloads (phase-07).

Two backends, selectable through the layered config (``admin > env > default``):

- **gridfs** (default) — keeps everything in Mongo (one dependency, aligns with D8).
- **filesystem** — a local directory keyed by opaque filenames; handy for high-volume local dev.

Refs are **scheme-prefixed** (``gridfs:<oid>`` / ``fs:<name>``) so a stored ref resolves for
``get``/``delete`` regardless of which backend is currently the default — switching backends never
strands existing blobs. Small structured/text artifacts stay inline in the doc (see
``app.orchestrator.artifacts``); only large payloads land here.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from gridfs.errors import NoFile
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorGridFSBucket

from app.core.config import get_config
from app.core.errors import SystemError  # noqa: A004 - taxonomy name fixed by the plan
from app.db.mongo import get_client

_GRIDFS_SCHEME = "gridfs:"
_FS_SCHEME = "fs:"


class BlobStore:
    """Facade over the configured blob backends.

    ``put`` writes via the configured default backend; ``get``/``delete`` dispatch on the ref's
    scheme so cross-backend refs always resolve. Constructor args override config for tests.
    """

    def __init__(
        self,
        backend: str | None = None,
        *,
        fs_dir: str | None = None,
        db: AsyncIOMotorDatabase[dict[str, Any]] | None = None,
    ) -> None:
        config = get_config()
        self._backend = backend or str(config.get("blob_backend"))
        self._fs_dir = fs_dir if fs_dir is not None else str(config.get("blob_fs_dir"))
        self._db = db

    # -- backend resolvers -------------------------------------------------

    def _bucket(self) -> AsyncIOMotorGridFSBucket:
        db = self._db
        if db is None:
            db = get_client()[str(get_config().get("forgeflow_meta_db"))]
        return AsyncIOMotorGridFSBucket(db)

    def _fs_root(self) -> Path:
        if not self._fs_dir:
            raise SystemError("blob_fs_dir must be set to use the filesystem blob backend")
        root = Path(self._fs_dir)
        root.mkdir(parents=True, exist_ok=True)
        return root

    # -- public API --------------------------------------------------------

    async def put(self, data: bytes) -> str:
        """Store ``data`` via the configured default backend; return a scheme-prefixed ref."""
        if self._backend == "filesystem":
            name = uuid.uuid4().hex
            (self._fs_root() / name).write_bytes(data)
            return f"{_FS_SCHEME}{name}"
        if self._backend == "gridfs":
            oid = await self._bucket().upload_from_stream("blob", data)
            return f"{_GRIDFS_SCHEME}{oid}"
        raise SystemError(f"Unknown blob backend: {self._backend!r}")

    async def get(self, ref: str) -> bytes:
        """Read back the bytes for ``ref`` (dispatched by scheme)."""
        if ref.startswith(_FS_SCHEME):
            path = self._fs_root() / ref[len(_FS_SCHEME) :]
            if not path.is_file():
                raise SystemError(f"Blob not found: {ref}")
            return path.read_bytes()
        if ref.startswith(_GRIDFS_SCHEME):
            oid = _parse_oid(ref[len(_GRIDFS_SCHEME) :])
            try:
                stream = await self._bucket().open_download_stream(oid)
                data: bytes = await stream.read()
            except NoFile as exc:
                raise SystemError(f"Blob not found: {ref}") from exc
            return data
        raise SystemError(f"Unrecognized blob ref: {ref!r}")

    async def delete(self, ref: str) -> None:
        """Delete the blob for ``ref`` (idempotent — a missing blob is not an error)."""
        if ref.startswith(_FS_SCHEME):
            (self._fs_root() / ref[len(_FS_SCHEME) :]).unlink(missing_ok=True)
            return
        if ref.startswith(_GRIDFS_SCHEME):
            oid = _parse_oid(ref[len(_GRIDFS_SCHEME) :])
            try:
                await self._bucket().delete(oid)
            except Exception:  # GridFS raises NoFile for a missing id; deletion is best-effort
                pass
            return
        raise SystemError(f"Unrecognized blob ref: {ref!r}")


def _parse_oid(raw: str) -> ObjectId:
    try:
        return ObjectId(raw)
    except (InvalidId, TypeError) as exc:
        raise SystemError(f"Malformed GridFS ref: {raw!r}") from exc


def get_blob_store() -> BlobStore:
    """The process default blob store (reads the configured backend)."""
    return BlobStore()
