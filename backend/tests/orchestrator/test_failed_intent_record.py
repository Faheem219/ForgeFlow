"""A failed request must leave a durable trace, and a dead process must not leave phantom runs.

Both are "closing the app lost my work" symptoms observed in practice: three refine attempts sat in
the conversation with no reply at all (the provider error had only ever been a toast, so after a
reload the requests looked silently ignored), and two `conductor:design` runs from a previous
process were still listed as *running* days later.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.errors import ProviderError, UserError
from app.db.models import Message, Run
from app.db.models.enums import MessageRole, Stage, StageStatus
from app.db.repos import RunRepo
from app.design.base import provider_error
from app.orchestrator.conductor import BUILD_RUN_KINDS, Conductor, reap_orphaned_runs
from app.orchestrator.schemas import Intent, IntentAction, StageResult
from app.orchestrator.stages import get_handler, register_handler
from app.projects.service import ProjectService

pytestmark = pytest.mark.usefixtures("mongo_db")


class BoomHandler:
    """A stage handler that fails the way a provider does."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def handle(self, intent: Intent, ctx: object) -> StageResult:
        raise self._exc


@pytest.fixture
def restore_design_handler() -> object:
    original = get_handler(Stage.design)
    yield
    register_handler(Stage.design, original)


async def _project() -> tuple[PydanticObjectId, PydanticObjectId]:
    user = PydanticObjectId()
    project = await ProjectService().create_project(user, "notes app")
    assert project.id is not None
    return user, project.id


async def _conversation(pid: PydanticObjectId) -> list[tuple[str, str]]:
    messages = await Message.find(Message.project_id == pid).sort("+created_at").to_list()
    return [(str(m.role), m.content) for m in messages]


async def test_a_failed_intent_answers_in_the_conversation(
    restore_design_handler: object,
) -> None:
    """The exact gap: the user's message was stored, the failure was not."""
    user, pid = await _project()
    register_handler(
        Stage.design,
        BoomHandler(
            provider_error(
                "stitch",
                "Stitch tool error: Requested entity was not found.",
                hint="Generate a new design to get a fresh Stitch project.",
            )
        ),
    )

    with pytest.raises(ProviderError):
        await Conductor().handle_intent(
            user,
            Intent(
                project_id=pid,
                stage=Stage.design,
                action=IntentAction.refine,
                message="refine the design a bit darker",
            ),
        )

    convo = await _conversation(pid)
    assert convo[0] == (str(MessageRole.user), "refine the design a bit darker")
    role, reply = convo[1]
    assert role == str(MessageRole.assistant)
    assert "didn't go through" in reply
    assert "Requested entity was not found" in reply  # the actual reason, kept
    assert "Generate a new design" in reply  # …and what to do about it


async def test_a_user_error_is_recorded_too(restore_design_handler: object) -> None:
    user, pid = await _project()
    register_handler(
        Stage.design,
        BoomHandler(UserError("There's no design to refine yet")),
    )

    with pytest.raises(UserError):
        await Conductor().handle_intent(
            user,
            Intent(
                project_id=pid,
                stage=Stage.design,
                action=IntentAction.refine,
                message="make it dark",
            ),
        )

    assert "There's no design to refine yet" in (await _conversation(pid))[-1][1]


async def test_an_unexpected_failure_does_not_leak_internals(
    restore_design_handler: object,
) -> None:
    """A crash still gets an answer — but the transcript is no place for a stack trace."""
    user, pid = await _project()
    secret = "postgres://user:sup3rsecret@host/db"
    register_handler(
        Stage.design,
        BoomHandler(RuntimeError(f"connection failed: {secret}")),
    )

    with pytest.raises(RuntimeError):
        await Conductor().handle_intent(
            user,
            Intent(
                project_id=pid,
                stage=Stage.design,
                action=IntentAction.refine,
                message="make it dark",
            ),
        )

    reply = (await _conversation(pid))[-1][1]
    assert "something went wrong" in reply.lower()
    assert secret not in reply
    assert "RuntimeError" not in reply


async def test_a_successful_intent_gets_no_failure_note(restore_design_handler: object) -> None:
    user, pid = await _project()

    resp = await Conductor().handle_intent(
        user,
        Intent(
            project_id=pid,
            stage=Stage.design,
            action=IntentAction.skip,
        ),
    )

    assert resp.to_status is StageStatus.skipped
    assert all("didn't go through" not in content for _, content in await _conversation(pid))


# ---------------------------------------------------------------- phantom runs


async def test_every_orphaned_run_is_closed_not_just_builds() -> None:
    """A dead process's `conductor:design` run showed as "running" in the cost panel forever."""
    _, pid = await _project()
    design = await Run(project_id=pid, kind="conductor:design").insert()
    build = await Run(project_id=pid, kind="codegen:build").insert()
    finished = await Run(project_id=pid, kind="conductor:design").insert()
    finished.finished_at = finished.started_at
    await finished.save()

    reaped = await reap_orphaned_runs()

    assert reaped == 2  # the two in-flight ones; the already-closed one is untouched
    for run_id in (design.id, build.id):
        reloaded = await Run.get(run_id)
        assert reloaded is not None and reloaded.finished_at is not None
    assert await RunRepo().active_for_project(pid, BUILD_RUN_KINDS) == []
