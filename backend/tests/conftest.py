from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.auth.ratelimit import reset_rate_limits
from app.core.config import get_config, reset_config
from app.db.models import ALL_DOCUMENT_MODELS
from app.realtime.hub import reset_hub
from app.sandbox.exec import reset_exec_service
from app.sandbox.manager import reset_manager
from app.sandbox.preview import reset_preview_service
from app.sandbox.workspace import reset_runtime_provider


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    """Fresh config + rate limits + hub + sandbox manager/runtime/exec/preview per test."""
    reset_config()
    reset_rate_limits()
    reset_hub()
    reset_manager()
    reset_runtime_provider()
    reset_exec_service()
    reset_preview_service()
    yield
    reset_config()
    reset_rate_limits()
    reset_hub()
    reset_manager()
    reset_runtime_provider()
    reset_exec_service()
    reset_preview_service()


@pytest_asyncio.fixture
async def mongo_db() -> AsyncIterator[AsyncIOMotorDatabase[dict[str, Any]]]:
    """Disposable per-test database. Skips the test if MongoDB is unreachable."""
    uri = get_config().get("mongodb_uri")
    client: AsyncIOMotorClient[dict[str, Any]] = AsyncIOMotorClient(
        uri, serverSelectionTimeoutMS=1500
    )
    try:
        await client.admin.command("ping")
    except Exception:
        client.close()
        pytest.skip("MongoDB not reachable; skipping DB integration tests")

    db_name = f"forgeflow_test_{uuid.uuid4().hex[:12]}"
    await init_beanie(database=client[db_name], document_models=ALL_DOCUMENT_MODELS)
    try:
        yield client[db_name]
    finally:
        await client.drop_database(db_name)
        client.close()
