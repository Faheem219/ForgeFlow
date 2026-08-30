from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.auth.ratelimit import reset_rate_limits
from app.core.config import get_config, reset_config
from app.core.config_db import reset_db_provider
from app.db.models import ALL_DOCUMENT_MODELS
from app.design.registry import reset_registry
from app.realtime.hub import reset_hub
from app.sandbox.exec import reset_exec_service
from app.sandbox.manager import reset_manager
from app.sandbox.preview import reset_preview_service
from app.sandbox.workspace import reset_runtime_provider


@pytest.fixture(scope="session", autouse=True)
def _reap_sandboxes_created_by_this_session() -> Iterator[None]:
    """Remove real sandbox containers/networks this test session created — and only those.

    Some suites reach the *default* runtime provider, which talks to a real Docker daemon, and a
    test that fails part-way leaves its container behind. Each one pins a subnet from docker's
    finite address pools (~30), so a few full runs exhaust them: sandbox creation then fails for
    every project on the machine, including the developer's own. Snapshotting the container names
    that existed **before** the session and removing only what appeared during it keeps that
    cleanup precise — a pre-existing sandbox is never touched, and volumes are always left alone.
    """
    from app.sandbox.manager import LABEL_MANAGED

    def _names() -> set[str]:
        try:
            import docker

            client = docker.from_env()
            containers = client.containers.list(
                all=True, filters={"label": f"{LABEL_MANAGED}=true"}
            )
            return {c.name for c in containers}
        except Exception:  # no daemon here — nothing to snapshot or clean
            return set()

    before = _names()
    yield
    leaked = _names() - before
    if not leaked:
        return
    try:
        import docker

        client = docker.from_env()
    except Exception:  # pragma: no cover - daemon vanished mid-session
        return
    for name in leaked:
        try:
            client.containers.get(name).remove(force=True)
        except Exception:  # pragma: no cover - already gone
            continue
    # Reclaim the subnets those containers held — via the manager, which decides orphan-hood from
    # the surviving containers. Removing by label alone would delete the network a *stopped*
    # sandbox still needs (docker allows it) and leave that container permanently unstartable.
    from app.sandbox.manager import SandboxManager

    try:
        asyncio.run(SandboxManager(client=client).prune_orphan_networks())
    except Exception:  # pragma: no cover - best-effort cleanup
        pass


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    """Fresh config + rate limits + hub + sandbox manager/runtime/exec/preview + design registry."""
    reset_config()
    reset_db_provider()
    reset_rate_limits()
    reset_hub()
    reset_manager()
    reset_runtime_provider()
    reset_exec_service()
    reset_preview_service()
    reset_registry()
    yield
    reset_config()
    reset_db_provider()
    reset_rate_limits()
    reset_hub()
    reset_manager()
    reset_runtime_provider()
    reset_exec_service()
    reset_preview_service()
    reset_registry()


@pytest_asyncio.fixture
async def mongo_db() -> AsyncIterator[AsyncIOMotorDatabase[dict[str, Any]]]:
    """Disposable per-test database. Skips the test if MongoDB is unreachable."""
    uri = get_config().get("mongodb_uri")
    # `tz_aware=True` mirrors production (app/db/mongo.py) so tests see the same aware datetimes
    # the API actually serializes.
    client: AsyncIOMotorClient[dict[str, Any]] = AsyncIOMotorClient(
        uri, serverSelectionTimeoutMS=1500, tz_aware=True
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
