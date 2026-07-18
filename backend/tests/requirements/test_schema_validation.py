"""RequirementSpec validation (phase-25): valid specs persist; empties/dupes/missing rejected."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.errors import UserError
from app.db.models.enums import CriterionKind
from app.orchestrator.requirements import (
    CriterionInput,
    FeatureInput,
    RequirementSpecInput,
    RequirementsService,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


def _feature(name: str, *criteria: str) -> FeatureInput:
    return FeatureInput(name=name, acceptance_criteria=[CriterionInput(text=c) for c in criteria])


async def test_valid_spec_persists_with_criterion_ids_and_kinds() -> None:
    spec = RequirementSpecInput(
        features=[
            FeatureInput(
                name="Todos",
                description="Manage a todo list",
                inputs=["title"],
                expected_behaviors=["adds a todo"],
                acceptance_criteria=[
                    CriterionInput(text="can add a todo", kind=CriterionKind.unit),
                    CriterionInput(text="shows the todo in the list", kind=CriterionKind.e2e),
                ],
            )
        ]
    )
    saved = await RequirementsService().save(PydanticObjectId(), spec)

    assert saved.version == 1
    criteria = saved.features[0].acceptance_criteria
    assert [c.kind for c in criteria] == [CriterionKind.unit, CriterionKind.e2e]
    assert all(c.id.startswith("ac-") for c in criteria)
    assert len({c.id for c in criteria}) == 2  # unique ids


async def test_empty_spec_is_rejected() -> None:
    with pytest.raises(UserError, match="at least one feature"):
        await RequirementsService().save(PydanticObjectId(), RequirementSpecInput(features=[]))


async def test_empty_feature_name_is_rejected() -> None:
    with pytest.raises(UserError, match="must not be empty"):
        await RequirementsService().save(
            PydanticObjectId(),
            RequirementSpecInput(features=[_feature("  ", "does a thing")]),
        )


async def test_duplicate_feature_names_are_rejected() -> None:
    with pytest.raises(UserError, match="Duplicate feature"):
        await RequirementsService().save(
            PydanticObjectId(),
            RequirementSpecInput(features=[_feature("Todos", "a"), _feature("todos", "b")]),
        )


async def test_feature_without_criteria_is_rejected() -> None:
    with pytest.raises(UserError, match="at least one acceptance criterion"):
        await RequirementsService().save(
            PydanticObjectId(),
            RequirementSpecInput(features=[FeatureInput(name="Todos")]),
        )


async def test_empty_criterion_text_is_rejected() -> None:
    with pytest.raises(UserError, match="must not be empty"):
        await RequirementsService().save(
            PydanticObjectId(),
            RequirementSpecInput(features=[_feature("Todos", "   ")]),
        )
