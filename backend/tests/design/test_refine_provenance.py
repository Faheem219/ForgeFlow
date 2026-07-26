"""A refine must go to the provider that PRODUCED the design, not the active one.

The bug this locks down: a generate that fell back (Stitch down/out of quota → `fake`) stored a
`fake-text-…` external_ref, and the next refine was sent to the still-active Stitch — which
answered *"Stitch tool error: Requested entity was not found"* and dead-ended the design stage.
An external_ref only means anything to its own provider, so refine follows provenance.
"""

from __future__ import annotations

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.core.errors import ProviderError
from app.db.models import Artifact, Project
from app.db.models.enums import ArtifactType, Stage
from app.design.base import DesignResult
from app.design.fake import FakeDesignProvider
from app.design.registry import register_provider
from app.design.resilient import provider_for_refine
from app.design.service import META_PROVIDER, DesignService
from tests.resilience.conftest import ScriptedDesignProvider

pytestmark = pytest.mark.usefixtures("mongo_db")


async def _project() -> Project:
    return await Project(user_id=PydanticObjectId(), name="p", app_db_name="db").insert()


def _stitch_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESIGN_PROVIDER", "stitch")
    reset_config()


def _install(**providers: ScriptedDesignProvider) -> dict[str, ScriptedDesignProvider]:
    for provider in providers.values():
        register_provider(provider)
    return providers


# ---------------------------------------------------------------- the routing decision


async def test_refine_targets_the_producing_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _stitch_active(monkeypatch)
    _install(stitch=ScriptedDesignProvider("stitch"), fake=ScriptedDesignProvider("fake"))

    target = provider_for_refine(await _project(), "fake")

    assert target.key == "fake"  # …not the active "stitch"
    assert target.note is not None and "fake" in target.note and "stitch" in target.note


async def test_refine_uses_the_active_provider_when_provenance_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stitch_active(monkeypatch)
    _install(stitch=ScriptedDesignProvider("stitch"))

    target = provider_for_refine(await _project(), "stitch")

    assert target.key == "stitch"
    assert target.note is None  # nothing surprising happened, so nothing to explain


@pytest.mark.parametrize("produced_by", [None, "", "   "])
async def test_missing_provenance_falls_back_to_the_active_provider(
    monkeypatch: pytest.MonkeyPatch, produced_by: str | None
) -> None:
    """A legacy artifact with no recorded provider must still be refinable."""
    _stitch_active(monkeypatch)
    _install(stitch=ScriptedDesignProvider("stitch"))

    target = provider_for_refine(await _project(), produced_by)

    assert target.key == "stitch"


async def test_an_unregistered_producing_provider_is_flagged_not_crashed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stitch_active(monkeypatch)
    _install(stitch=ScriptedDesignProvider("stitch"))

    target = provider_for_refine(await _project(), "some-removed-provider")

    assert target.key == "stitch"
    assert target.note is not None and "not registered" in target.note


async def test_a_per_project_override_is_honored_as_the_active_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stitch_active(monkeypatch)
    _install(stitch=ScriptedDesignProvider("stitch"), figma=ScriptedDesignProvider("figma"))
    project = await Project(
        user_id=PydanticObjectId(), name="p", app_db_name="db", design_provider="figma"
    ).insert()

    # Provenance matches the project's override → no cross-provider note.
    assert provider_for_refine(project, "figma").note is None
    # …and a design from elsewhere still routes to its own producer.
    assert provider_for_refine(project, "stitch").key == "stitch"


# ---------------------------------------------------------------- end to end through the service


async def test_a_fallback_produced_design_refines_at_the_fallback_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact regression: fake-produced design + stitch active → refine must not touch stitch."""
    _stitch_active(monkeypatch)
    providers = _install(
        stitch=ScriptedDesignProvider("stitch"), fake=ScriptedDesignProvider("fake")
    )
    project = await _project()
    service = DesignService()
    assert project.id is not None
    await service.save_result(
        project.id,
        DesignResult(provider="fake", external_ref="fake-text-6f58ed67", html="<h1>x</h1>", css=""),
        source="text",
    )

    artifact = await service.refine(project, "make the header indigo")

    assert providers["fake"].refine_calls == [("fake-text-6f58ed67", "make the header indigo")]
    assert providers["stitch"].refine_calls == []  # the call that used to fail
    assert artifact.meta[META_PROVIDER] == "fake"
    assert artifact.meta["refined_from_version"] == 1


async def test_a_stitch_produced_design_still_refines_at_stitch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stitch_active(monkeypatch)
    providers = _install(
        stitch=ScriptedDesignProvider("stitch"), fake=ScriptedDesignProvider("fake")
    )
    project = await _project()
    service = DesignService()
    assert project.id is not None
    await service.save_result(
        project.id,
        DesignResult(provider="stitch", external_ref="proj-9/screen-1", html="<h1/>", css=""),
        source="text",
    )

    await service.refine(project, "tighter spacing")

    assert providers["stitch"].refine_calls == [("proj-9/screen-1", "tighter spacing")]
    assert providers["fake"].refine_calls == []


async def test_the_refined_version_records_the_serving_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provenance must stay correct across a chain of refines, or the next one re-breaks."""
    _stitch_active(monkeypatch)
    _install(stitch=ScriptedDesignProvider("stitch"), fake=ScriptedDesignProvider("fake"))
    project = await _project()
    service = DesignService()
    assert project.id is not None
    await service.save_result(
        project.id,
        DesignResult(provider="fake", external_ref="fake-text-abc", html="<h1/>", css=""),
        source="text",
    )

    first = await service.refine(project, "one")
    second = await service.refine(project, "two")

    assert first.meta[META_PROVIDER] == "fake"
    assert second.meta[META_PROVIDER] == "fake"
    assert second.meta["refined_from_version"] == first.version


# ---------------------------------------------------------------- the fake provider's own refs


async def test_a_fake_ref_survives_a_control_plane_restart() -> None:
    """The in-memory map is a cache: a ref this provider issued stays refinable after a restart."""
    issued = await FakeDesignProvider().generate_from_text("a todo app")

    restarted = FakeDesignProvider()  # fresh instance == new process
    refined = await restarted.refine(issued.external_ref, "make it dark")

    assert refined.provider == "fake"
    assert refined.external_ref.startswith(issued.external_ref)
    assert "make it dark" in refined.html


async def test_the_fake_provider_still_rejects_a_foreign_ref() -> None:
    with pytest.raises(ProviderError):
        await FakeDesignProvider().refine("proj-9/screen-1", "make it dark")


# ---------------------------------------------------------------- stale artifacts keep working


async def test_an_artifact_without_provider_meta_is_refinable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stitch_active(monkeypatch)
    providers = _install(stitch=ScriptedDesignProvider("stitch"))
    project = await _project()
    assert project.id is not None
    # A hand-rolled artifact as an older build would have written it: no provider in meta.
    await Artifact(
        project_id=project.id,
        stage=Stage.design,
        type=ArtifactType.design,
        version=1,
        meta={"external_ref": "proj-9/screen-1"},
    ).insert()

    await DesignService().refine(project, "nudge it")

    assert providers["stitch"].refine_calls == [("proj-9/screen-1", "nudge it")]
