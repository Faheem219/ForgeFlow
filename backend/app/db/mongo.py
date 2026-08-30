"""MongoDB client + Beanie initialization.

Control-plane metadata lives in ``forgeflow_meta`` (D8). Each user-project gets its **own**
database for generated-app data; :func:`get_app_db` returns that isolated handle and refuses to
hand back the meta DB.
"""

from __future__ import annotations

from typing import Any

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import get_config
from app.core.errors import SystemError  # noqa: A004 - taxonomy name fixed by the plan
from app.db.models import ALL_DOCUMENT_MODELS

_client: AsyncIOMotorClient[dict[str, Any]] | None = None


def get_client() -> AsyncIOMotorClient[dict[str, Any]]:
    """Return the process-wide Motor client (created lazily)."""
    global _client
    if _client is None:
        # `tz_aware=True` is load-bearing, not a preference. BSON stores datetimes as UTC, and the
        # default driver hands them back **naive** — which Pydantic then serializes with no offset
        # (`2026-08-30T07:34:18`). A browser parses an offset-less date-time as *local* time, so
        # every timestamp in the UI silently shifted by the viewer's UTC offset: 07:34 UTC rendered
        # as 07:34 IST, five and a half hours in the past. Reading them back aware makes the wire
        # format `…+00:00`, which is the same instant everywhere.
        _client = AsyncIOMotorClient(get_config().get("mongodb_uri"), tz_aware=True)
    return _client


async def init_db(
    client: AsyncIOMotorClient[dict[str, Any]] | None = None,
    meta_db_name: str | None = None,
) -> None:
    """Initialize Beanie over the meta database and ensure all indexes exist."""
    client = client or get_client()
    db_name = meta_db_name or get_config().get("forgeflow_meta_db")
    await init_beanie(database=client[db_name], document_models=ALL_DOCUMENT_MODELS)


def get_app_db(app_db_name: str) -> AsyncIOMotorDatabase[dict[str, Any]]:
    """Return an isolated per-project app database handle (never the meta DB)."""
    meta = get_config().get("forgeflow_meta_db")
    if not app_db_name or app_db_name == meta:
        raise SystemError(f"Refusing to expose meta or empty DB name: {app_db_name!r}")
    return get_client()[app_db_name]


async def close_client() -> None:
    """Close the Motor client (shutdown / test teardown)."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
