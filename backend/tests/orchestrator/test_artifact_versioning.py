"""Artifact *service* versioning + payload strategy (phase-07).

Complements ``tests/db/test_artifact_versioning.py`` (which covers the repo's raw numbering) by
exercising the service's inline-vs-blob decision and content resolution.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
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
async def service(
    mongo_db: AsyncIOMotorDatabase[dict[str, Any]],
) -> AsyncIterator[ArtifactService]:
    # Low inline threshold so both the inline and blob paths are exercised cheaply.
    store = BlobStore(backend="gridfs", db=mongo_db)
    yield ArtifactService(blob_store=store, inline_max_bytes=16)


async def test_versions_increment_per_project_stage_type(service: ArtifactService) -> None:
    pid = PydanticObjectId()
    v1 = await service.create_version(pid, Stage.design, ArtifactType.design, text="one")
    v2 = await service.create_version(pid, Stage.design, ArtifactType.design, text="two")
    assert (v1.version, v2.version) == (1, 2)

    latest = await service.get_latest(pid, Stage.design, ArtifactType.design)
    assert latest is not None and latest.version == 2

    # Nothing overwritten — v1 still resolves to its own content.
    got1 = await service.get_version(pid, Stage.design, ArtifactType.design, 1)
    assert got1 is not None
    assert await service.get_content(got1) == "one"


async def test_version_sequence_is_independent_per_tuple(service: ArtifactService) -> None:
    pid = PydanticObjectId()
    await service.create_version(pid, Stage.build, ArtifactType.code_change, text="x")
    design = await service.create_version(pid, Stage.design, ArtifactType.design, text="y")
    assert design.version == 1  # a different (stage, type) starts its own sequence


async def test_small_payload_stays_inline(service: ArtifactService) -> None:
    pid = PydanticObjectId()
    artifact = await service.create_version(pid, Stage.design, ArtifactType.design, text="tiny")
    assert artifact.ref is None
    assert artifact.meta["content"] == "tiny"
    assert await service.get_content(artifact) == "tiny"


async def test_large_payload_offloaded_to_blob(service: ArtifactService) -> None:
    pid = PydanticObjectId()
    big = "x" * 1000  # > 16-byte threshold set in the fixture
    artifact = await service.create_version(pid, Stage.design, ArtifactType.design, text=big)
    assert artifact.ref is not None  # stored out-of-line
    assert "content" not in artifact.meta
    assert await service.get_content(artifact) == big


async def test_structured_artifact_without_text_has_no_content(service: ArtifactService) -> None:
    pid = PydanticObjectId()
    artifact = await service.create_version(
        pid, Stage.requirements, ArtifactType.requirement, meta={"foo": "bar"}
    )
    assert artifact.ref is None
    assert artifact.meta == {"foo": "bar"}
    assert await service.get_content(artifact) is None


async def test_list_for_project_filters_by_stage_and_type(service: ArtifactService) -> None:
    pid = PydanticObjectId()
    await service.create_version(pid, Stage.design, ArtifactType.design, text="d")
    await service.create_version(pid, Stage.build, ArtifactType.code_change, text="c")

    design_only = await service.list_for_project(pid, stage=Stage.design)
    assert [a.type for a in design_only] == [ArtifactType.design]

    code_only = await service.list_for_project(pid, artifact_type=ArtifactType.code_change)
    assert [a.type for a in code_only] == [ArtifactType.code_change]


async def test_delete_for_project_removes_artifacts_and_blobs(service: ArtifactService) -> None:
    pid = PydanticObjectId()
    inline = await service.create_version(pid, Stage.design, ArtifactType.design, text="tiny")
    blobbed = await service.create_version(pid, Stage.design, ArtifactType.design, text="x" * 1000)
    assert blobbed.ref is not None

    await service.delete_for_project(pid)

    assert await service.list_for_project(pid) == []
    # The offloaded blob is gone too.
    from app.core.errors import SystemError  # noqa: A004 - taxonomy name fixed by the plan

    with pytest.raises(SystemError):
        await service._blobs.get(blobbed.ref)
    assert inline.ref is None
