"""Demo seed + reset (phase-50): pre-load the hero state, and tear it back down cleanly."""

from __future__ import annotations

import pytest

from app.core.config import reset_config
from app.db.models import Project
from app.db.models.enums import Stage, StageStatus
from app.db.repos import StageStateRepo, UserRepo
from app.orchestrator.requirements import RequirementsService
from scripts import demo

pytestmark = pytest.mark.usefixtures("mongo_db")

EMAIL = "demo@forgeflow.dev"


@pytest.fixture
def demo_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_PASSWORD", "rehearsal-password")
    reset_config()


async def test_seed_refuses_without_a_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """A known default credential would be a security hole — no password, no seed."""
    monkeypatch.setenv("DEMO_PASSWORD", "")
    reset_config()

    message = await demo.seed_demo()

    assert "refusing" in message.lower()
    assert await UserRepo().get_by_email(EMAIL) is None


async def test_seed_creates_the_account_and_a_preloaded_project(demo_password: None) -> None:
    message = await demo.seed_demo()

    user = await UserRepo().get_by_email(EMAIL)
    assert user is not None and user.id is not None
    assert "created demo user" in message

    projects = [p async for p in Project.find({"user_id": user.id})]
    assert [p.name for p in projects] == [demo.DEMO_PROJECT_NAME]
    project = projects[0]
    assert project.id is not None

    # Requirements are pre-captured from the hero spec, and marked complete so the demo starts
    # at the interesting part.
    spec = await RequirementsService().latest(project.id)
    assert spec is not None
    assert [f.name for f in spec.features] == [
        "Manage todos",
        "Outstanding count",
        "Clear completed",
    ]

    states = {s.stage: s.status for s in await StageStateRepo().list_for_project(project.id)}
    assert states.get(Stage.requirements) is StageStatus.complete


async def test_seed_is_idempotent_on_the_project(demo_password: None) -> None:
    await demo.seed_demo()
    message = await demo.seed_demo()

    assert "already present" in message
    user = await UserRepo().get_by_email(EMAIL)
    assert user is not None and user.id is not None
    projects = [p async for p in Project.find({"user_id": user.id})]
    assert len(projects) == 1  # not duplicated


async def test_reset_returns_to_a_clean_state(demo_password: None) -> None:
    await demo.seed_demo()
    user = await UserRepo().get_by_email(EMAIL)
    assert user is not None and user.id is not None

    removed = await demo.reset_demo()

    assert removed == 1
    assert [p async for p in Project.find({"user_id": user.id})] == []
    # The account itself survives a reset — only its projects are cleared.
    assert await UserRepo().get_by_email(EMAIL) is not None


async def test_reset_with_nothing_seeded_is_a_no_op(demo_password: None) -> None:
    assert await demo.reset_demo() == 0


async def test_seed_then_reset_can_run_again(demo_password: None) -> None:
    """Rehearse-reset-rehearse: the cycle must be repeatable without residue."""
    await demo.seed_demo()
    await demo.reset_demo()
    message = await demo.seed_demo()

    assert "seeded" in message
    user = await UserRepo().get_by_email(EMAIL)
    assert user is not None and user.id is not None
    assert len([p async for p in Project.find({"user_id": user.id})]) == 1
