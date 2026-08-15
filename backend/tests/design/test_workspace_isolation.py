"""Each ForgeFlow project designs into its **own** provider container.

The registry hands out a single :class:`StitchDesignProvider` for the whole process (phase-16), and
the Stitch client used to cache "the last project I created" on that shared instance. Every
ForgeFlow project therefore inherited one Stitch project: a brand-new project opened showing the
*previous* project's screens in the picker — before its owner had designed anything — and a
generate added to that same stranger's Stitch project.

These pin the fix: the container is resolved from the project's **own** latest design, so a project
with no design yet gets a fresh one (and an empty picker), and successive turns keep building the
same app.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.db.models import Project
from app.design.base import DesignScreenRef
from app.design.registry import register_provider, reset_registry
from app.design.service import META_WORKSPACE, DesignService
from app.design.stitch import StitchDesignPayload, StitchDesignProvider
from app.orchestrator.requirements import (
    CriterionInput,
    FeatureInput,
    RequirementSpecInput,
    RequirementsService,
)

pytestmark = pytest.mark.usefixtures("mongo_db")


class RecordingClient:
    """A Stitch transport that reports which container each call was pointed at."""

    def __init__(self) -> None:
        #: The workspace each generate was handed (``None`` → "create a fresh one").
        self.generated_into: list[str | None] = []
        self.titles: list[str | None] = []
        self.listed: list[str | None] = []
        self._created = 0

    async def text_to_ui(
        self,
        prompt: str,
        *,
        headers: Mapping[str, str],
        workspace: str | None = None,
        title: str | None = None,
    ) -> StitchDesignPayload:
        self.generated_into.append(workspace)
        self.titles.append(title)
        if workspace is None:  # the real client creates a project here
            self._created += 1
            workspace = f"new-proj-{self._created}"
        return StitchDesignPayload(
            workspace=workspace,
            external_ref=f"{workspace}/screen-1",
            html="<main>design</main>",
            css="",
        )

    async def refine(
        self, external_ref: str, instruction: str, *, headers: Mapping[str, str]
    ) -> StitchDesignPayload:  # pragma: no cover - not exercised here
        raise AssertionError("refine is not part of these tests")

    async def fetch_code(
        self, external_ref: str, *, headers: Mapping[str, str]
    ) -> StitchDesignPayload:  # pragma: no cover - not exercised here
        raise AssertionError("fetch_code is not part of these tests")

    async def list_screens(
        self, *, headers: Mapping[str, str], workspace: str | None = None
    ) -> list[DesignScreenRef]:
        self.listed.append(workspace)
        return []


@pytest.fixture
def stitch(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[StitchDesignProvider, RecordingClient]]:
    monkeypatch.setenv("STITCH_API_KEY", "key-1")
    monkeypatch.setenv("STITCH_PROJECT_ID", "")  # no global pin — the per-project path
    monkeypatch.setenv("DESIGN_PROVIDER", "stitch")
    reset_config()
    client = RecordingClient()
    provider = StitchDesignProvider(client=client)
    # The flows resolve their provider through the registry, so the recording one has to stand in
    # for the real Stitch entry rather than merely being constructed here.
    register_provider(provider)
    yield provider, client
    reset_registry()


async def _project(name: str) -> Project:
    return await Project(user_id=PydanticObjectId(), name=name, app_db_name=f"db-{name}").insert()


async def test_a_project_with_no_design_has_no_workspace() -> None:
    """The lookup that keeps a fresh project's screen picker empty instead of borrowed."""
    project = await _project("brand-new")
    assert project.id is not None

    assert await DesignService().workspace_for(project.id, "stitch") is None


async def test_each_project_generates_into_its_own_stitch_project(
    stitch: tuple[StitchDesignProvider, RecordingClient],
) -> None:
    provider, client = stitch
    service = DesignService()
    first, second = await _project("alpha"), await _project("beta")

    await service.generate_from_text(first, "a todo app")
    await service.generate_from_text(second, "a recipe app")

    # Neither generate inherited a container: both asked for a fresh one…
    assert client.generated_into == [None, None]
    # …and the two projects ended up in different Stitch projects.
    assert first.id is not None and second.id is not None
    alpha = await service.workspace_for(first.id, "stitch")
    beta = await service.workspace_for(second.id, "stitch")
    assert alpha and beta and alpha != beta


async def test_a_second_generate_stays_in_the_projects_own_workspace(
    stitch: tuple[StitchDesignProvider, RecordingClient],
) -> None:
    """One project is one app: later turns build up the same Stitch project, not a new one."""
    provider, client = stitch
    service = DesignService()
    project = await _project("alpha")

    await service.generate_from_text(project, "a todo app")
    await service.generate_from_text(project, "…and a settings screen")

    assert project.id is not None
    workspace = await service.workspace_for(project.id, "stitch")
    assert client.generated_into == [None, workspace]  # created once, then reused


async def test_the_workspace_is_recorded_on_the_design_artifact(
    stitch: tuple[StitchDesignProvider, RecordingClient],
) -> None:
    service = DesignService()
    project = await _project("alpha")

    artifact = await service.generate_from_text(project, "a todo app")

    assert artifact.meta[META_WORKSPACE] == "new-proj-1"


async def test_a_new_stitch_project_is_named_after_the_forgeflow_project(
    stitch: tuple[StitchDesignProvider, RecordingClient],
) -> None:
    """Otherwise every project in Stitch is called "ForgeFlow" and they cannot be told apart."""
    provider, client = stitch

    await DesignService().generate_from_text(await _project("recipe-box"), "a recipe app")

    assert client.titles == ["recipe-box"]


async def test_another_providers_workspace_is_never_reused(
    stitch: tuple[StitchDesignProvider, RecordingClient],
) -> None:
    """A container handle means nothing to a different backend — same rule as ``external_ref``."""
    service = DesignService()
    project = await _project("alpha")
    await service.generate_from_text(project, "a todo app")  # produced by stitch

    assert project.id is not None
    assert await service.workspace_for(project.id, "figma") is None


async def test_the_requirements_app_name_names_the_stitch_project(
    stitch: tuple[StitchDesignProvider, RecordingClient],
) -> None:
    """The name the user reviewed in requirements is the one that shows up in Stitch."""
    provider, client = stitch
    project = await _project("untitled-7")
    assert project.id is not None
    await RequirementsService().save(
        project.id,
        RequirementSpecInput(
            app_name="Focus",
            features=[
                FeatureInput(
                    name="Todos", acceptance_criteria=[CriterionInput(text="can add a todo")]
                )
            ],
        ),
    )

    await DesignService().generate_from_text(project, "a todo app")

    assert client.titles == ["Focus"]  # …not the project's placeholder name


async def test_without_a_spec_name_the_project_name_is_used(
    stitch: tuple[StitchDesignProvider, RecordingClient],
) -> None:
    """Requirements are skippable (D12), so the title must not depend on them."""
    provider, client = stitch

    await DesignService().generate_from_text(await _project("recipe-box"), "a recipe app")

    assert client.titles == ["recipe-box"]
