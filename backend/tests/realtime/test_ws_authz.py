from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.auth.service import create_access_token
from app.db.models import Project, User
from app.db.repos import ProjectRepo, UserRepo
from app.realtime.router import authenticate_ws, authorize_project

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_authenticate_rejects_missing_and_garbage_tokens() -> None:
    assert await authenticate_ws(None) is None
    assert await authenticate_ws("") is None
    assert await authenticate_ws("not-a-real-token") is None


async def test_authenticate_accepts_a_valid_token() -> None:
    user = await UserRepo().insert(User(email="w@example.com", hashed_password="x"))
    authed = await authenticate_ws(create_access_token(str(user.id)))
    assert authed is not None and authed.id == user.id


async def test_authorize_allows_own_demo_channel() -> None:
    user = await UserRepo().insert(User(email="d@example.com", hashed_password="x"))
    assert await authorize_project(user, f"demo-{user.id}") is True


async def test_authorize_rejects_foreign_and_allows_owned_project() -> None:
    owner = await UserRepo().insert(User(email="o@example.com", hashed_password="x"))
    intruder = await UserRepo().insert(User(email="i@example.com", hashed_password="x"))
    project = await ProjectRepo().insert(Project(user_id=owner.id, name="p", app_db_name="app_p"))

    assert await authorize_project(intruder, str(project.id)) is False
    assert await authorize_project(owner, str(project.id)) is True


async def test_authorize_rejects_unknown_project() -> None:
    user = await UserRepo().insert(User(email="u@example.com", hashed_password="x"))
    assert await authorize_project(user, str(PydanticObjectId())) is False
    assert await authorize_project(user, "garbage-id") is False
