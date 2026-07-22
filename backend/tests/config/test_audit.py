"""Every admin config change is audited with before/after + the actor (phase-51)."""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.config_admin import ConfigAdminService
from app.core.config_db import DbSettingProvider
from app.db.models import ConfigAudit

pytestmark = pytest.mark.usefixtures("mongo_db")


async def _audits(key: str) -> list[ConfigAudit]:
    return await ConfigAudit.find({"key": key}).sort("+created_at").to_list()


async def test_a_write_records_before_and_after_and_actor(
    db_provider: DbSettingProvider,
) -> None:
    admin_id = PydanticObjectId()

    await ConfigAdminService().set("repair_max_iterations", 8, admin_id=admin_id)

    [entry] = await _audits("repair_max_iterations")
    assert entry.action == "update"
    assert entry.before == 5  # the default that was in force
    assert entry.after == 8
    assert entry.updated_by == admin_id
    assert entry.created_at is not None


async def test_successive_writes_capture_the_moving_before(
    db_provider: DbSettingProvider,
) -> None:
    service = ConfigAdminService()
    await service.set("repair_max_iterations", 8, admin_id=None)
    await service.set("repair_max_iterations", 3, admin_id=None)

    first, second = await _audits("repair_max_iterations")
    assert (first.before, first.after) == (5, 8)
    assert (second.before, second.after) == (8, 3)  # before tracks the prior value


async def test_a_delete_is_audited_as_a_revert(db_provider: DbSettingProvider) -> None:
    admin_id = PydanticObjectId()
    service = ConfigAdminService()
    await service.set("design_provider", "figma", admin_id=admin_id)

    await service.delete("design_provider", admin_id=admin_id)

    audits = await _audits("design_provider")
    delete_entry = audits[-1]
    assert delete_entry.action == "delete"
    assert delete_entry.before == "figma"
    assert delete_entry.after is None  # reverted to env/default
    assert delete_entry.updated_by == admin_id


async def test_a_rejected_write_leaves_no_audit(db_provider: DbSettingProvider) -> None:
    """An invalid or sensitive write is refused *before* anything is recorded."""
    from app.core.errors import UserError

    with pytest.raises(UserError):
        await ConfigAdminService().set("repair_max_iterations", 999, admin_id=None)

    assert await _audits("repair_max_iterations") == []


async def test_list_audit_returns_changes_newest_first(db_provider: DbSettingProvider) -> None:
    """The phase-52 audit view reads the history newest-first, with the actor id as a string."""
    admin_id = PydanticObjectId()
    service = ConfigAdminService()
    await service.set("repair_max_iterations", 8, admin_id=admin_id)
    await service.set("repair_stall_threshold", 3, admin_id=None)

    entries = await service.list_audit()

    assert [e.key for e in entries[:2]] == ["repair_stall_threshold", "repair_max_iterations"]
    assert entries[1].updated_by == str(admin_id)
    assert (entries[1].before, entries[1].after) == (5, 8)
    assert entries[0].updated_by is None  # a system write has no actor
