"""DesignResult → versioned `design` artifact: payload round-trip, meta, blob offload, versioning.

Also covers the phase's headline acceptance criterion end-to-end via the fake provider:
text/image → DesignResult → versioned design artifact, and refine → a new version referencing
the prior one.
"""

from __future__ import annotations

import json

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.core.errors import UserError
from app.db.models import Project
from app.db.models.enums import ArtifactType, Stage
from app.design.base import DesignImage, DesignResult, DesignScreen
from app.design.service import (
    META_EXTERNAL_REF,
    META_PREVIEW_IMAGE,
    META_PROVIDER,
    META_PROVIDER_META,
    META_REFINED_FROM,
    META_SOURCE,
    DesignService,
)
from app.orchestrator.artifacts import ArtifactService
from app.projects.service import ProjectService

pytestmark = pytest.mark.usefixtures("mongo_db")


@pytest.fixture(autouse=True)
def _use_fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DESIGN_PROVIDER", "fake")
    reset_config()


async def _project() -> Project:
    return await ProjectService().create_project(PydanticObjectId(), "p")


def _pid(project: Project) -> PydanticObjectId:
    """A persisted project always has an id; narrow it for the type checker."""
    assert project.id is not None
    return project.id


def _result(**overrides: object) -> DesignResult:
    base: dict[str, object] = {
        "provider": "fake",
        "external_ref": "ref-1",
        "html": "<h1>hi</h1>",
        "css": "h1 { color: red; }",
        "preview_image": "https://img.example/p.png",
        "meta": {"source": "text", "prompt": "hi"},
    }
    base.update(overrides)
    return DesignResult(**base)  # type: ignore[arg-type]


async def test_save_result_maps_payload_and_meta() -> None:
    project = await _project()
    service = DesignService()

    artifact = await service.save_result(_pid(project), _result(), source="text")

    assert artifact.stage == Stage.design
    assert artifact.type == ArtifactType.design
    assert artifact.version == 1
    assert artifact.meta[META_PROVIDER] == "fake"
    assert artifact.meta[META_EXTERNAL_REF] == "ref-1"
    assert artifact.meta[META_PREVIEW_IMAGE] == "https://img.example/p.png"
    assert artifact.meta[META_SOURCE] == "text"
    # Provider-specific meta is nested so it can never clobber our keys.
    assert artifact.meta[META_PROVIDER_META] == {"source": "text", "prompt": "hi"}

    payload = await service.load_payload(artifact)
    assert payload.html == "<h1>hi</h1>"
    assert payload.css == "h1 { color: red; }"


async def test_provider_meta_cannot_clobber_our_keys() -> None:
    project = await _project()
    service = DesignService()

    hostile = _result(meta={META_PROVIDER: "evil", META_EXTERNAL_REF: "evil"})
    artifact = await service.save_result(_pid(project), hostile, source="text")

    assert artifact.meta[META_PROVIDER] == "fake"  # ours wins
    assert artifact.meta[META_EXTERNAL_REF] == "ref-1"
    assert artifact.meta[META_PROVIDER_META][META_PROVIDER] == "evil"  # theirs is preserved, nested


async def test_small_designs_stay_inline_and_large_ones_go_to_a_blob() -> None:
    project = await _project()

    small = DesignService(ArtifactService(inline_max_bytes=10_000))
    inline = await small.save_result(_pid(project), _result(), source="text")
    assert inline.ref is None
    assert (await small.load_payload(inline)).html == "<h1>hi</h1>"

    # Same service surface, oversized payload → offloaded, still resolves transparently.
    big = DesignService(ArtifactService(inline_max_bytes=16))
    heavy = _result(html="<p>" + ("x" * 5000) + "</p>")
    offloaded = await big.save_result(_pid(project), heavy, source="text")
    assert offloaded.ref is not None
    assert "content" not in offloaded.meta
    assert (await big.load_payload(offloaded)).html == heavy.html


async def test_versions_are_appended_never_overwritten() -> None:
    project = await _project()
    service = DesignService()

    first = await service.save_result(_pid(project), _result(html="<h1>1</h1>"), source="text")
    second = await service.save_result(_pid(project), _result(html="<h1>2</h1>"), source="text")

    assert (first.version, second.version) == (1, 2)
    versions = await service.list_versions(_pid(project))
    assert [v.version for v in versions] == [1, 2]
    # v1 is untouched.
    assert (await service.load_payload(versions[0])).html == "<h1>1</h1>"
    latest = await service.latest(_pid(project))
    assert latest is not None and latest.version == 2


# --- end-to-end through the active (fake) provider ---


async def test_text_to_versioned_design_artifact() -> None:
    project = await _project()
    service = DesignService()

    artifact = await service.generate_from_text(project, "a todo app")

    assert artifact.version == 1
    assert artifact.meta[META_SOURCE] == "text"
    assert artifact.meta[META_PROVIDER] == "fake"
    assert "a todo app" in (await service.load_payload(artifact)).html


async def test_images_to_versioned_design_artifact() -> None:
    project = await _project()
    service = DesignService()

    images = [DesignImage(filename="home.png", media_type="image/png", data=b"pixels")]
    artifact = await service.generate_from_images(project, images, prompt="dark theme")

    assert artifact.meta[META_SOURCE] == "image"
    assert "home.png" in (await service.load_payload(artifact)).html


async def test_refine_creates_a_new_version_referencing_the_prior() -> None:
    project = await _project()
    service = DesignService()

    first = await service.generate_from_text(project, "landing page")
    refined = await service.refine(project, "bigger hero")

    assert refined.version == 2
    assert refined.meta[META_REFINED_FROM] == first.version
    assert refined.meta[META_SOURCE] == "refine"
    # The provider's new handle is recorded so the *next* refine chains off this version.
    assert refined.meta[META_EXTERNAL_REF] != first.meta[META_EXTERNAL_REF]
    assert "bigger hero" in (await service.load_payload(refined)).html


async def test_refine_chains_across_versions() -> None:
    project = await _project()
    service = DesignService()

    await service.generate_from_text(project, "p")
    await service.refine(project, "first change")
    third = await service.refine(project, "second change")

    assert third.version == 3
    assert third.meta[META_REFINED_FROM] == 2
    assert "second change" in (await service.load_payload(third)).html


async def test_refine_without_a_design_is_rejected() -> None:
    project = await _project()
    with pytest.raises(UserError, match="no design"):
        await DesignService().refine(project, "make it pop")


async def test_empty_inputs_are_rejected() -> None:
    project = await _project()
    service = DesignService()

    with pytest.raises(UserError):
        await service.generate_from_text(project, "   ")
    with pytest.raises(UserError):
        await service.generate_from_images(project, [])
    with pytest.raises(UserError):
        await service.refine(project, "")


async def test_payload_is_a_json_envelope_of_html_and_css() -> None:
    """The stored payload keeps markup+styles atomic in one version."""
    project = await _project()
    service = DesignService()
    artifact = await service.save_result(_pid(project), _result(), source="text")

    raw = await ArtifactService().get_content(artifact)
    # `screens` rides along for multi-screen designs and is empty for a single-screen one.
    assert json.loads(raw or "") == {
        "html": "<h1>hi</h1>",
        "css": "h1 { color: red; }",
        "screens": [],
    }


async def test_payload_carries_every_screen_of_a_multi_screen_design() -> None:
    """A provider can return a whole flow; the switcher in the UI needs all of them persisted."""
    project = await _project()
    result = _result()
    result.screens = [
        DesignScreen(id="s1", title="Light", html="<h1>hi</h1>"),
        DesignScreen(id="s2", title="Dark", html="<h1>dark</h1>"),
    ]
    artifact = await DesignService().save_result(_pid(project), result, source="text")

    payload = json.loads(await ArtifactService().get_content(artifact) or "")
    assert [s["title"] for s in payload["screens"]] == ["Light", "Dark"]
    assert payload["html"] == "<h1>hi</h1>"  # the primary is still the top-level markup
