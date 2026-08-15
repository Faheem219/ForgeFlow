"""`get_latest_of_kind` filters by meta.kind within a (stage, type) pair (phase-55 task 1).

Build stores a ``build_report`` and (from phase-56) a ``build_plan`` under the *same*
``(build, code_change)`` pair. `get_latest` returns whichever was written last regardless of kind,
so incremental resume — which wants the newest *report* — must filter by kind or a later plan write
silently hides the last report.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models.enums import ArtifactType, Stage
from app.orchestrator.artifacts import ArtifactService

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_returns_newest_matching_kind_even_when_another_kind_is_newer() -> None:
    project_id = PydanticObjectId()
    artifacts = ArtifactService()

    await artifacts.create_version(
        project_id,
        Stage.build,
        ArtifactType.code_change,
        text="report v1",
        meta={"kind": "build_report"},
    )
    report_v2 = await artifacts.create_version(
        project_id,
        Stage.build,
        ArtifactType.code_change,
        text="report v2",
        meta={"kind": "build_report"},
    )
    # A newer artifact of a DIFFERENT kind, same (stage, type) pair — the phase-56 collision.
    await artifacts.create_version(
        project_id,
        Stage.build,
        ArtifactType.code_change,
        text="the plan",
        meta={"kind": "build_plan"},
    )

    latest_report = await artifacts.get_latest_of_kind(
        project_id, Stage.build, ArtifactType.code_change, "build_report"
    )
    assert latest_report is not None
    assert latest_report.id == report_v2.id  # the newest *report*, not the newer plan

    # get_latest (kind-blind) returns the plan — which is exactly why the filtered lookup exists.
    latest_any = await artifacts.get_latest(project_id, Stage.build, ArtifactType.code_change)
    assert latest_any is not None and latest_any.meta.get("kind") == "build_plan"


async def test_returns_none_when_no_artifact_of_that_kind_exists() -> None:
    project_id = PydanticObjectId()
    artifacts = ArtifactService()
    await artifacts.create_version(
        project_id, Stage.build, ArtifactType.code_change, text="plan", meta={"kind": "build_plan"}
    )
    assert (
        await artifacts.get_latest_of_kind(
            project_id, Stage.build, ArtifactType.code_change, "build_report"
        )
        is None
    )
