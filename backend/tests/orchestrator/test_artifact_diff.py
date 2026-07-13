"""Unified-diff correctness for text artifacts (phase-07)."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.blobs import BlobStore
from app.db.models.enums import ArtifactType, Stage
from app.orchestrator.artifacts import ArtifactService

pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest_asyncio.fixture
async def service(mongo_db: AsyncIOMotorDatabase[dict[str, Any]]) -> ArtifactService:
    return ArtifactService(blob_store=BlobStore(backend="gridfs", db=mongo_db), inline_max_bytes=16)


async def test_diff_shows_added_and_removed_lines(service: ArtifactService) -> None:
    pid = PydanticObjectId()
    a = await service.create_version(
        pid, Stage.build, ArtifactType.code_change, text="line1\nline2\nline3\n"
    )
    b = await service.create_version(
        pid, Stage.build, ArtifactType.code_change, text="line1\nCHANGED\nline3\n"
    )

    diff = await service.diff(a, b)
    assert "-line2" in diff
    assert "+CHANGED" in diff
    assert "line1" in diff  # context retained


async def test_diff_of_identical_content_is_empty(service: ArtifactService) -> None:
    pid = PydanticObjectId()
    a = await service.create_version(pid, Stage.build, ArtifactType.code_change, text="same\n")
    b = await service.create_version(pid, Stage.build, ArtifactType.code_change, text="same\n")
    assert await service.diff(a, b) == ""


async def test_diff_works_across_blob_backed_artifacts(service: ArtifactService) -> None:
    pid = PydanticObjectId()
    left = "x" * 40 + "\nkeep\n"  # > 16-byte threshold → blob-backed
    right = "y" * 40 + "\nkeep\n"
    a = await service.create_version(pid, Stage.build, ArtifactType.code_change, text=left)
    b = await service.create_version(pid, Stage.build, ArtifactType.code_change, text=right)
    assert a.ref is not None and b.ref is not None  # both offloaded

    diff = await service.diff(a, b)
    assert diff  # non-empty
    assert "keep" in diff
