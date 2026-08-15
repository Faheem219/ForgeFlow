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
from app.db.models import Project
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
from app.design.service import (
    META_PROVIDER,
    META_PROVIDER_META,
    META_SOURCE,
    DesignService,
)
from app.orchestrator.artifacts import ArtifactService
from app.orchestrator.conductor import Conductor
from app.orchestrator.requirements import (
    CriterionInput,
    FeatureInput,
    RequirementSpecInput,
    RequirementsService,
)
from app.orchestrator.schemas import Intent, IntentAction, IntentResponse
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


async def _save_requirements(pid: PydanticObjectId) -> None:
    """A saved spec, with the requirements *stage* deliberately left untouched (see below)."""
    await RequirementsService().save(
        pid,
        RequirementSpecInput(
            features=[
                FeatureInput(
                    name="Todos",
                    description="manage a todo list",
                    acceptance_criteria=[CriterionInput(text="Adding a todo shows it in the list")],
                )
            ]
        ),
    )


async def _set_original_prompt(pid: PydanticObjectId, text: str) -> None:
    project = await Project.get(pid)
    assert project is not None
    project.original_prompt = text
    await project.save()


def _prompt_sent(resp: IntentResponse) -> str | None:
    """What actually reached the provider — the `fake` provider echoes its prompt into meta."""
    provider_meta = resp.artifacts[0].meta[META_PROVIDER_META]
    assert isinstance(provider_meta, dict)
    prompt = provider_meta["prompt"]
    assert prompt is None or isinstance(prompt, str)
    return prompt


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

    # Generate → approve (complete), and complete build so it can go stale. Build — not
    # requirements: since the 2026-08-06 reorder requirements is *upstream* of design, so a design
    # refine must leave it alone (a UI tweak does not invalidate the feature list).
    await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, payload={"text": "a todo app"})
    )
    await Conductor().handle_intent(user, _intent(pid, IntentAction.proceed))
    await ProjectService().transition_stage(pid, user, Stage.requirements, Action.complete)
    await ProjectService().transition_stage(pid, user, Stage.build, Action.complete)

    resp = await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, message="make it dark")
    )

    assert Stage.build in resp.stale
    assert Stage.requirements not in resp.stale
    stages = await ProjectService().list_stages(pid, user)
    build = next(s for s in stages if s.stage is Stage.build)
    assert build.status is StageStatus.stale
    requirements = next(s for s in stages if s.stage is Stage.requirements)
    assert requirements.status is StageStatus.complete


# ---------------------------------------------------------------- requirements as design context
# Requirements precede design since 2026-08-06, so a design generated *after* them should know what
# the app has to do. The dependency is soft: with no spec, the provider call is what it always was.


async def test_generate_from_text_sends_the_prompt_unchanged_without_requirements(
    design_env: None,
) -> None:
    user = PydanticObjectId()
    pid = await _project(user)

    resp = await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, payload={"text": "a todo app"})
    )

    assert _prompt_sent(resp) == "a todo app"  # byte-for-byte the pre-reorder behavior


async def test_generate_from_text_folds_in_the_requirements_when_they_exist(
    design_env: None,
) -> None:
    user = PydanticObjectId()
    pid = await _project(user)
    await _save_requirements(pid)

    resp = await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, payload={"text": "a todo app"})
    )

    prompt = _prompt_sent(resp)
    assert isinstance(prompt, str)
    assert prompt.startswith("a todo app")  # the user's own words still lead
    assert "Todos" in prompt
    assert "manage a todo list" in prompt
    assert "Adding a todo shows it in the list" in prompt


async def test_requirements_context_does_not_wait_on_the_stage_being_complete(
    design_env: None,
) -> None:
    """Content and stage-completion are decoupled: a *saved* spec is enough.

    `_save_requirements` never touches the requirements stage, so it is still `empty` here — and
    the summary must be used anyway. Gating on the stage's status would quietly turn a soft
    dependency into a workflow requirement (D12)."""
    user = PydanticObjectId()
    pid = await _project(user)
    await _save_requirements(pid)

    stages = await ProjectService().list_stages(pid, user)
    assert next(s for s in stages if s.stage is Stage.requirements).status is StageStatus.empty

    resp = await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, payload={"text": "a todo app"})
    )
    assert "Todos" in str(_prompt_sent(resp))


async def test_screenshot_generate_carries_the_requirements_but_needs_no_prompt(
    design_env: None,
) -> None:
    """The hero path (screenshots only, no requirements) must send exactly what it sent before."""
    user = PydanticObjectId()
    pid = await _project(user)
    ref = await get_blob_store().put(b"\x89PNG fake bytes")
    payload: dict[str, object] = {
        "image_refs": [{"ref": ref, "filename": "a.png", "media_type": "image/png"}]
    }

    bare = await Conductor().handle_intent(user, _intent(pid, IntentAction.refine, payload=payload))
    assert _prompt_sent(bare) is None  # unchanged: no prompt at all

    await _save_requirements(pid)
    informed = await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, payload=payload)
    )
    assert "Todos" in str(_prompt_sent(informed))


async def test_a_skipped_requirements_stage_leaves_the_design_prompt_alone(
    design_env: None,
) -> None:
    """Skipping requirements is a first-class choice — design must behave as if it never existed."""
    user = PydanticObjectId()
    pid = await _project(user)
    await ProjectService().transition_stage(pid, user, Stage.requirements, Action.skip)

    resp = await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, payload={"text": "a todo app"})
    )
    assert _prompt_sent(resp) == "a todo app"


# -- generating with nothing typed: fall back to what ForgeFlow already knows ---------------------


async def test_no_intake_falls_back_to_the_original_prompt_and_requirements(
    design_env: None,
) -> None:
    """The user should never have to repeat an idea they already gave ForgeFlow once.

    A bare `refine` (no text, no images, no message) with no design yet must generate directly
    from the description that first seeded requirements, folded together with the requirements
    themselves — not demand the user type something into an empty box.
    """
    user = PydanticObjectId()
    pid = await _project(user)
    await _set_original_prompt(pid, "A clean personal todo app")
    await _save_requirements(pid)

    resp = await Conductor().handle_intent(user, _intent(pid, IntentAction.refine))

    assert resp.to_status is StageStatus.awaiting_user
    prompt = _prompt_sent(resp)
    assert isinstance(prompt, str)
    assert prompt.startswith("A clean personal todo app")  # the original idea still leads
    assert "Todos" in prompt and "manage a todo list" in prompt  # …with requirements folded in
    assert "your requirements" in resp.messages[-1].content  # the summary says where it came from


async def test_no_intake_uses_requirements_alone_when_there_is_no_original_prompt(
    design_env: None,
) -> None:
    """A manually-authored spec (never drafted from a description) is still enough on its own."""
    user = PydanticObjectId()
    pid = await _project(user)
    await _save_requirements(pid)

    resp = await Conductor().handle_intent(user, _intent(pid, IntentAction.refine))

    assert "Todos" in str(_prompt_sent(resp))


async def test_no_intake_and_nothing_on_file_still_generates_something(design_env: None) -> None:
    """With truly nothing to go on — no prompt ever given, no requirements, no design — generation
    must still succeed rather than dead-end on an error: a UI always gets created, using the
    project's own name as the only guaranteed real content, refinable afterward either way."""
    user = PydanticObjectId()
    pid = await _project(user)
    project = await Project.get(pid)
    assert project is not None

    resp = await Conductor().handle_intent(user, _intent(pid, IntentAction.refine))

    assert resp.to_status is StageStatus.awaiting_user
    prompt = _prompt_sent(resp)
    assert isinstance(prompt, str) and project.name in prompt
    assert "starting design" in resp.messages[-1].content  # says it was a generic starting point


async def test_no_intake_with_an_existing_design_still_requires_an_instruction(
    design_env: None,
) -> None:
    """The auto-generate fallback only applies before any design exists. Once one does, a bare
    `refine` with no instruction is ambiguous (refine? approve? nothing?) and must still ask —
    even with an original prompt and requirements on file that COULD be used to regenerate."""
    user = PydanticObjectId()
    pid = await _project(user)
    await _set_original_prompt(pid, "A clean personal todo app")
    await _save_requirements(pid)
    await Conductor().handle_intent(
        user, _intent(pid, IntentAction.refine, payload={"text": "a todo app"})
    )

    with pytest.raises(UserError):
        await Conductor().handle_intent(user, _intent(pid, IntentAction.refine))


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

        async def generate_from_text(
            self,
            prompt: str,
            *,
            workspace: str | None = None,
            workspace_title: str | None = None,
        ) -> DesignResult:
            raise provider_error("failing", "quota exhausted", hint="switch to figma")

        async def generate_from_image(
            self,
            images: list[DesignImage],
            prompt: str | None = None,
            *,
            workspace: str | None = None,
            workspace_title: str | None = None,
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
