from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models.enums import ArtifactType, Stage
from app.db.repos import ArtifactRepo

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_versions_increment_and_all_are_retrievable() -> None:
    repo = ArtifactRepo()
    pid = PydanticObjectId()

    v1 = await repo.create_version(pid, Stage.design, ArtifactType.design, ref="r1")
    v2 = await repo.create_version(pid, Stage.design, ArtifactType.design, ref="r2")
    assert (v1.version, v2.version) == (1, 2)

    latest = await repo.latest_version(pid, Stage.design, ArtifactType.design)
    assert latest is not None and latest.version == 2

    versions = await repo.list_versions(pid, Stage.design, ArtifactType.design)
    assert [v.version for v in versions] == [1, 2]

    # Nothing overwritten — the old version is still retrievable.
    got1 = await repo.get_version(pid, Stage.design, ArtifactType.design, 1)
    assert got1 is not None and got1.ref == "r1"


async def test_version_sequence_is_per_stage_and_type() -> None:
    repo = ArtifactRepo()
    pid = PydanticObjectId()

    await repo.create_version(pid, Stage.build, ArtifactType.code_change)
    design = await repo.create_version(pid, Stage.design, ArtifactType.design)

    # A different (stage, type) tuple starts its own version sequence at 1.
    assert design.version == 1
