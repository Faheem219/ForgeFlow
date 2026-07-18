"""Requirement versioning + criterion-id stability (phase-25)."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.orchestrator.requirements import (
    CriterionInput,
    FeatureInput,
    RequirementSpecInput,
    RequirementsService,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


def _feature(name: str, criteria: list[CriterionInput]) -> FeatureInput:
    return FeatureInput(name=name, acceptance_criteria=criteria)


async def test_edit_creates_a_new_version() -> None:
    svc = RequirementsService()
    pid = PydanticObjectId()

    v1 = await svc.save(
        pid, RequirementSpecInput(features=[_feature("A", [CriterionInput(text="x")])])
    )
    assert v1.version == 1

    # Change the criterion text → new version.
    v2 = await svc.save(
        pid, RequirementSpecInput(features=[_feature("A", [CriterionInput(text="y")])])
    )
    assert v2.version == 2

    # Both retrievable.
    assert (await svc.get_version(pid, 1)) is not None
    assert (await svc.get_version(pid, 2)) is not None


async def test_no_change_does_not_create_a_new_version() -> None:
    svc = RequirementsService()
    pid = PydanticObjectId()

    v1 = await svc.save(
        pid, RequirementSpecInput(features=[_feature("A", [CriterionInput(text="x")])])
    )
    # Re-save identical content (no ids provided) → content match → identical → no new version.
    again = await svc.save(
        pid, RequirementSpecInput(features=[_feature("A", [CriterionInput(text="x")])])
    )

    assert again.version == 1
    assert again.features[0].acceptance_criteria[0].id == v1.features[0].acceptance_criteria[0].id


async def test_unchanged_criterion_ids_are_preserved_across_edits() -> None:
    svc = RequirementsService()
    pid = PydanticObjectId()

    v1 = await svc.save(
        pid,
        RequirementSpecInput(
            features=[
                _feature("A", [CriterionInput(text="a-crit")]),
                _feature("B", [CriterionInput(text="b-crit")]),
            ]
        ),
    )
    id_a = v1.features[0].acceptance_criteria[0].id
    id_b = v1.features[1].acceptance_criteria[0].id

    # Edit feature A's criterion (round-tripping its id); leave B untouched (round-trip its id too).
    v2 = await svc.save(
        pid,
        RequirementSpecInput(
            features=[
                _feature("A", [CriterionInput(id=id_a, text="a-crit-edited")]),
                _feature("B", [CriterionInput(id=id_b, text="b-crit")]),
            ]
        ),
    )
    assert v2.version == 2
    assert v2.features[0].acceptance_criteria[0].id == id_a  # kept its join key
    assert v2.features[1].acceptance_criteria[0].id == id_b  # unchanged → preserved


async def test_ids_preserved_by_content_match_without_round_tripping() -> None:
    svc = RequirementsService()
    pid = PydanticObjectId()

    v1 = await svc.save(
        pid,
        RequirementSpecInput(
            features=[_feature("A", [CriterionInput(text="keep"), CriterionInput(text="drop")])]
        ),
    )
    id_keep = next(c.id for c in v1.features[0].acceptance_criteria if c.text == "keep")

    # Add a new criterion + drop one, without sending any ids: "keep" matches by text → same id.
    v2 = await svc.save(
        pid,
        RequirementSpecInput(
            features=[
                _feature("A", [CriterionInput(text="keep"), CriterionInput(text="brand-new")])
            ]
        ),
    )
    kept = next(c for c in v2.features[0].acceptance_criteria if c.text == "keep")
    fresh = next(c for c in v2.features[0].acceptance_criteria if c.text == "brand-new")
    assert kept.id == id_keep
    assert fresh.id != id_keep
