"""Message service: append + ordered listing, stage filter, pagination (phase-07)."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.db.models.enums import MessageRole, Stage
from app.orchestrator.messages import MessageService, token_usage

pytestmark = pytest.mark.usefixtures("mongo_db")


async def test_append_and_list_in_created_order() -> None:
    svc = MessageService()
    pid = PydanticObjectId()
    await svc.append(pid, MessageRole.user, "first")
    await svc.append(pid, MessageRole.assistant, "second")
    await svc.append(pid, MessageRole.user, "third")

    contents = [m.content for m in await svc.list_for_project(pid)]
    assert contents == ["first", "second", "third"]


async def test_list_filters_by_stage() -> None:
    svc = MessageService()
    pid = PydanticObjectId()
    await svc.append(pid, MessageRole.user, "d", stage=Stage.design)
    await svc.append(pid, MessageRole.user, "b", stage=Stage.build)

    design = await svc.list_for_project(pid, stage=Stage.design)
    assert [m.content for m in design] == ["d"]


async def test_list_is_scoped_to_project() -> None:
    svc = MessageService()
    mine, other = PydanticObjectId(), PydanticObjectId()
    await svc.append(mine, MessageRole.user, "mine")
    await svc.append(other, MessageRole.user, "other")

    assert [m.content for m in await svc.list_for_project(mine)] == ["mine"]


async def test_pagination_skip_and_limit() -> None:
    svc = MessageService()
    pid = PydanticObjectId()
    for i in range(5):
        await svc.append(pid, MessageRole.user, f"m{i}")

    page = await svc.list_for_project(pid, skip=1, limit=2)
    assert [m.content for m in page] == ["m1", "m2"]


async def test_append_persists_token_usage() -> None:
    svc = MessageService()
    pid = PydanticObjectId()
    usage = token_usage(input_tokens=10, output_tokens=20, model="claude-sonnet-5", inr=0.5)
    msg = await svc.append(pid, MessageRole.assistant, "hi", usage=usage)

    assert msg.token_usage["input_tokens"] == 10
    assert msg.token_usage["model"] == "claude-sonnet-5"


async def test_delete_for_project_clears_messages() -> None:
    svc = MessageService()
    pid = PydanticObjectId()
    await svc.append(pid, MessageRole.user, "gone")
    await svc.delete_for_project(pid)
    assert await svc.list_for_project(pid) == []


def test_token_usage_helper_shape() -> None:
    usage = token_usage(1, 2, "m", 0.1)
    assert usage == {"input_tokens": 1, "output_tokens": 2, "model": "m", "inr": 0.1}
