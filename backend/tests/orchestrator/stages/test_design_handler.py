"""Design stage handler (phase-19), driven end-to-end through the conductor.

Uses the deterministic ``fake`` provider (set active via config) so generate/refine are exact, and
the filesystem blob backend so uploaded screenshots never touch the shared meta DB.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.core.errors import ProviderError, UserError
from app.db.blobs import get_blob_store
from app.db.models.enums import Stage, StageStatus
from app.design.base import (
    DesignCapabilities,
    DesignCode,
    DesignImage,
    DesignResult,
    ProviderHealth,
    provider_error,
)
from app.design.registry import register_provider
from app.design.service import META_PROVIDER, META_SOURCE, DesignService
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.conductor import Conductor
from app.orchestrator.schemas import Intent, IntentAction
from app.projects.service import ProjectService
from app.projects.state_machine import Action
from tests.resilience.conftest import ScriptedDesignProvider

pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest.fixture
def design_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("DESIGN_PROVIDER", "fake")
    monkeypatch.setenv("BLOB_BACKEND", "filesystem")
    monkeypatch.setenv("BLOB_FS_DIR", str(tmp_path))
    reset_config()
    yield


async def _project(user: PydanticObjectId) -> PydanticObjectId:
    project = await ProjectService().create_project(user, "p")
    assert project.id is not None
    return project.id


def _intent(
    pid: PydanticObjectId,
    action: IntentAction,
    *,
    message: str | None = None,
    payload: dict[str, object] | None = None,
) -> Intent:
    return Intent(
        project_id=pid, stage=Stage.design, action=action, message=message, payload=payload or {}
    )


async def test_generate_from_text_creates_awaiting_user_version(design_env: None) -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    resp = await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, payload={"text": "a todo app"})
    )

    assert resp.to_status is StageStatus.awaiting_user
    assert len(resp.artifacts) == 1
    art = resp.artifacts[0]
    assert art.version == 1
    assert art.meta[META_PROVIDER] == "fake"
    assert art.meta[META_SOURCE] == "text"


async def test_each_generate_is_a_new_version(design_env: None) -> None:
    user = PydanticObjectId()
    pid = await _project(user)
    svc = DesignService()

    await Conductor().handle_intent(user, _intent(pid, IntentAction.refine, payload={"text": "v1"}))
    await Conductor().handle_intent(user, _intent(pid, IntentAction.refine, payload={"text": "v2"}))

    versions = [a.version for a in await svc.list_versions(pid)]
    assert versions == [1, 2]  # nothing overwritten


async def test_refine_instruction_chains_from_latest(design_env: None) -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, payload={"text": "base"})
    )
    resp = await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, message="make the header violet")
    )

    assert resp.artifacts[0].version == 2
    assert resp.artifacts[0].meta[META_SOURCE] == "refine"


async def test_refine_after_a_fallback_goes_to_the_provider_that_produced_the_design(
    design_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported failure, through the real UI path.

    A generate falls back to ``fake`` (Stitch unavailable), then the user refines. Sending the
    ``fake-text-…`` ref to the still-active Stitch is what produced "Stitch tool error: Requested
    entity was not found"; the refine must go to ``fake`` and say so.
    """
    user = PydanticObjectId()
    pid = await _project(user)

    # Stitch is active but broken; the chain falls back to the real deterministic fake provider.
    stitch = ScriptedDesignProvider("stitch", fail_text="quota")
    register_provider(stitch)
    monkeypatch.setenv("DESIGN_PROVIDER", "stitch")
    reset_config()

    generated = await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, payload={"text": "a todo app"})
    )
    assert generated.artifacts[0].meta[META_PROVIDER] == "fake"  # the fallback served it

    resp = await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, message="make the header indigo")
    )

    art = resp.artifacts[0]
    assert art.meta[META_SOURCE] == "refine"
    assert art.meta[META_PROVIDER] == "fake"  # …and the refine followed it
    assert stitch.refine_calls == []  # the call that used to fail is never made
    # The user is told why the refine did not use their configured provider.
    note = resp.messages[-1].content
    assert "fake" in note and "stitch" in note


async def test_approve_completes_the_stage(design_env: None) -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, payload={"text": "a todo app"})
    )
    resp = await Conductor().handle_intent(user, _intent(pid, IntentAction.proceed))

    assert resp.to_status is StageStatus.complete
    assert resp.artifacts == []  # approving produces no new artifact


async def test_approve_without_a_design_is_a_user_error(design_env: None) -> None:
    user = PydanticObjectId()
    pid = await _project(user)
    with pytest.raises(UserError):
        await Conductor().handle_intent(user, _intent(pid, IntentAction.proceed))


async def test_skip_marks_stage_skipped_without_an_artifact(design_env: None) -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    resp = await Conductor().handle_intent(user, _intent(pid, IntentAction.skip))

    assert resp.to_status is StageStatus.skipped
    assert resp.artifacts == []
    assert await DesignService().latest(pid) is None  # downstream tolerates no design


async def test_bring_your_own_design_stores_without_a_provider_call(design_env: None) -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    resp = await Conductor().handle_intent(
        user,
        _intent(
            pid,
            IntentAction.refine,
            payload={"own_design": {"html": "<h1>mine</h1>", "css": "h1{color:red}"}},
        ),
    )

    art = resp.artifacts[0]
    assert art.meta[META_PROVIDER] == "own"  # sentinel — not generated by a provider
    assert art.meta[META_SOURCE] == "own"

    artifact = await ArtifactService().get_by_id(PydanticObjectId(art.id))
    assert artifact is not None
    payload = await DesignService().load_payload(artifact)
    assert payload.html == "<h1>mine</h1>"


async def test_generate_from_uploaded_screenshots(design_env: None) -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    ref = await get_blob_store().put(b"\x89PNG fake bytes")
    resp = await Conductor().handle_intent(
        user,
        _intent(
            pid,
            IntentAction.refine,
            payload={"image_refs": [{"ref": ref, "filename": "a.png", "media_type": "image/png"}]},
        ),
    )

    assert resp.artifacts[0].meta[META_SOURCE] == "image"
    assert resp.to_status is StageStatus.awaiting_user


async def test_refine_after_completion_marks_downstream_stale(design_env: None) -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    # Generate → approve (complete), and complete requirements so it can go stale.
    await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, payload={"text": "a todo app"})
    )
    await Conductor().handle_intent(user, _intent(pid, IntentAction.proceed))
    await ProjectService().transition_stage(pid, user, Stage.requirements, Action.complete)

    resp = await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, message="make it dark")
    )

    assert Stage.requirements in resp.stale
    stages = await ProjectService().list_stages(pid, user)
    requirements = next(s for s in stages if s.stage is Stage.requirements)
    assert requirements.status is StageStatus.stale


async def test_provider_error_propagates_with_a_fallback_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the dead end — no fallback candidates left — the error still carries its actionable hint.

    Phase-48 adds automatic provider fallback, so a failure now propagates only once *every*
    candidate is exhausted. Here the fallback chain is disabled, making the failing provider the
    sole candidate, which restores the single-provider "surface the hint" contract this guards.
    """

    class FailingProvider:
        key = "failing"

        async def generate_from_text(self, prompt: str) -> DesignResult:
            raise provider_error("failing", "quota exhausted", hint="switch to figma")

        async def generate_from_image(
            self, images: list[DesignImage], prompt: str | None = None
        ) -> DesignResult:
            raise provider_error("failing", "quota exhausted", hint="switch to figma")

        async def refine(self, design_ref: str, instruction: str) -> DesignResult:
            raise provider_error("failing", "quota exhausted", hint="switch to figma")

        async def fetch_code(self, design_ref: str) -> DesignCode:
            raise provider_error("failing", "unavailable")

        def capabilities(self) -> DesignCapabilities:
            return DesignCapabilities(provider=self.key, from_text=True)

        async def health(self) -> ProviderHealth:
            return ProviderHealth.down

    register_provider(FailingProvider())
    monkeypatch.setenv("DESIGN_PROVIDER", "failing")
    monkeypatch.setenv("DESIGN_FALLBACK_CHAIN", "")  # no substitutes → the failure must surface
    reset_config()

    user = PydanticObjectId()
    pid = await _project(user)

    with pytest.raises(ProviderError) as excinfo:
        await Conductor().handle_intent(
            user, _intent(pid, IntentAction.refine, payload={"text": "a todo app"})
        )
    assert excinfo.value.fallback_hint == "switch to figma"
