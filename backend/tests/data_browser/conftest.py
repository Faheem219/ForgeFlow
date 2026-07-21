"""Fixtures for the data browser (phase-41).

These run against the **real** local MongoDB, not a fake: the whole point of the phase is that one
project can reach exactly one database, and only a real server can prove a query actually landed
where it was supposed to.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from beanie import PydanticObjectId

from app.core.config import get_config
from app.db.models import Project


async def make_project(name: str = "app") -> Project:
    """A project with its own unique app database (as phase-36 assigns)."""
    return await Project(
        user_id=PydanticObjectId(),
        name=name,
        app_db_name=f"forgeflow_app_{uuid.uuid4().hex[:12]}",
    ).insert()


@pytest_asyncio.fixture
async def cleanup_app_dbs() -> AsyncIterator[list[str]]:
    """Drop every app database a test created, so real-Mongo tests leave nothing behind."""
    created: list[str] = []
    yield created

    from motor.motor_asyncio import AsyncIOMotorClient

    client: AsyncIOMotorClient[dict[str, Any]] = AsyncIOMotorClient(
        str(get_config().get("mongodb_uri"))
    )
    try:
        for db_name in created:
            await client.drop_database(db_name)
    finally:
        client.close()
