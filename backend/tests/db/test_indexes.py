from __future__ import annotations

import pytest
from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from app.db.models import PlatformSetting, StageState, User
from app.db.models.enums import SettingCategory, Stage
from app.db.repos import UserRepo

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_user_email_is_unique() -> None:
    await UserRepo().insert(User(email="dup@example.com", hashed_password="a"))
    with pytest.raises(DuplicateKeyError):
        await UserRepo().insert(User(email="dup@example.com", hashed_password="b"))


async def test_stage_state_project_stage_is_unique() -> None:
    pid = PydanticObjectId()
    await StageState(project_id=pid, stage=Stage.build).insert()
    with pytest.raises(DuplicateKeyError):
        await StageState(project_id=pid, stage=Stage.build).insert()


async def test_platform_setting_key_is_unique() -> None:
    await PlatformSetting(key="k1", value=1, category=SettingCategory.models).insert()
    with pytest.raises(DuplicateKeyError):
        await PlatformSetting(key="k1", value=2, category=SettingCategory.models).insert()


async def test_named_indexes_are_created() -> None:
    info = await User.get_motor_collection().index_information()
    assert "uniq_email" in set(info.keys())
