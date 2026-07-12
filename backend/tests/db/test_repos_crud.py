from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models import Message, Project, User
from app.db.models.enums import MessageRole, SettingCategory, Stage
from app.db.repos import (
    MessageRepo,
    PlatformSettingRepo,
    ProjectRepo,
    StageStateRepo,
    UserRepo,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_user_crud() -> None:
    repo = UserRepo()
    user = await repo.insert(User(email="a@example.com", hashed_password="x"))
    assert user.id is not None

    fetched = await repo.get(user.id)
    assert fetched is not None and fetched.email == "a@example.com"
    assert await repo.get_by_email("a@example.com") is not None

    assert await repo.delete(user.id) is True
    assert await repo.get(user.id) is None


async def test_project_list_for_user_is_scoped() -> None:
    repo = ProjectRepo()
    mine, other = PydanticObjectId(), PydanticObjectId()
    await repo.insert(Project(user_id=mine, name="p1", app_db_name="app_p1"))
    await repo.insert(Project(user_id=mine, name="p2", app_db_name="app_p2"))
    await repo.insert(Project(user_id=other, name="p3", app_db_name="app_p3"))

    listed = await repo.list_for_user(mine)
    assert {p.name for p in listed} == {"p1", "p2"}


async def test_message_stage_filter() -> None:
    repo = MessageRepo()
    pid = PydanticObjectId()
    await repo.insert(
        Message(project_id=pid, role=MessageRole.user, content="hi", stage=Stage.design)
    )
    await repo.insert(
        Message(project_id=pid, role=MessageRole.assistant, content="yo", stage=Stage.build)
    )

    assert len(await repo.list_for_project(pid)) == 2
    design_only = await repo.list_for_project(pid, stage=Stage.design)
    assert len(design_only) == 1 and design_only[0].content == "hi"


async def test_platform_setting_upsert_replaces_in_place() -> None:
    repo = PlatformSettingRepo()
    first = await repo.upsert("model_codegen", "claude-sonnet-5", SettingCategory.models)
    second = await repo.upsert("model_codegen", "claude-opus", SettingCategory.models)

    assert second.id == first.id  # updated, not duplicated
    got = await repo.get_by_key("model_codegen")
    assert got is not None and got.value == "claude-opus"


async def test_stage_state_get_or_create_is_singleton() -> None:
    repo = StageStateRepo()
    pid = PydanticObjectId()
    a = await repo.get_or_create(pid, Stage.build)
    b = await repo.get_or_create(pid, Stage.build)

    assert a.id == b.id
    assert len(await repo.list_for_project(pid)) == 1
