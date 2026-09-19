"""A design provider's clarifying question, put to the user instead of failed over.

The live failure this covers: Stitch answered a perfectly good brief with "I propose a 5-screen
experience … shall I proceed?", ForgeFlow classified that as a provider failure, and the design
stage served a placeholder from the `fake` fallback. A question is not a failure — it stops the
chain, is parked as a `DesignQuestion`, and the user's next message answers it.

Driven end-to-end through the conductor with scripted providers, so the intent the UI actually
sends is what is exercised.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from beanie import PydanticObjectId
from httpx import ASGITransport, AsyncClient

from app.api.app import create_app
from app.core.errors import ProviderError
from app.db.models import DesignQuestion, DesignQuestionStatus, Project
from app.db.models.enums import Stage, StageStatus
from app.design.base import DesignResult, provider_error
from app.design.questions import DesignQuestionService
from app.design.registry import register_provider
from app.design.resilient import DesignCapability, invoke_with_fallback
from app.design.service import META_PROVIDER, META_PROVIDER_META
from app.orchestrator.conductor import Conductor
from app.orchestrator.schemas import Intent, IntentAction, IntentResponse
from app.projects.service import ProjectService
from tests.resilience.conftest import ScriptedDesignProvider

pytestmark = pytest.mark.usefixtures("mongo_db")

QUESTION = "Should I start with the public feed, or the admin tools?"
SUGGESTIONS = ["The public feed first", "The admin tools first"]


class ClarifyingProvider(ScriptedDesignProvider):
    """Asks one question, then designs — what Stitch does with a broad brief.

    Records every prompt and workspace it is handed, which is how the tests check that the answer
    carries the brief *and* the question, and lands in the container the asking call opened.
    """

    def __init__(self, key: str = "stitch", *, workspace: str = "stitch-proj-9") -> None:
        super().__init__(key)
        self._workspace = workspace
        self.prompts: list[str] = []
        self.workspaces: list[str | None] = []
        self.refine_prompts: list[tuple[str, str]] = []
        #: Flipped off after the first ask, so the answered call goes through.
        self.asking = True

    def _question(self) -> ProviderError:
        self.asking = False
        return provider_error(
            self.key,
            "asked for more detail instead of generating a design",
            detail={
                "kind": "clarification",
                "detail": {
                    "provider": self.key,
                    "question": QUESTION,
                    "suggestions": SUGGESTIONS,
                    "prompt": self.prompts[-1],
                    "workspace": self._workspace,
                },
            },
        )

    async def generate_from_text(
        self,
        prompt: str,
        *,
        workspace: str | None = None,
        workspace_title: str | None = None,
    ) -> DesignResult:
        self.prompts.append(prompt)
        self.workspaces.append(workspace)
        if self.asking:
            raise self._question()
        return DesignResult(
            provider=self.key,
            workspace=workspace or self._workspace,
            external_ref=f"{self._workspace}/screen-1",
            html="<h1>designed</h1>",
            css="",
            meta={"prompt": prompt},
        )

    async def refine(self, design_ref: str, instruction: str) -> DesignResult:
        self.refine_prompts.append((design_ref, instruction))
        if self.asking:
            self.prompts.append(instruction)
            raise self._question()
        return DesignResult(
            provider=self.key,
            workspace=self._workspace,
            external_ref=design_ref,
            html="<h1>refined</h1>",
            css="",
            meta={"prompt": instruction},
        )


def _install() -> ClarifyingProvider:
    """The default chain — stitch (which asks) → figma → fake."""
    provider = ClarifyingProvider()
    register_provider(provider)
    register_provider(ScriptedDesignProvider("figma"))
    register_provider(ScriptedDesignProvider("fake"))
    return provider


async def _project() -> tuple[PydanticObjectId, PydanticObjectId]:
    """``(project id, owner id)`` — the conductor transitions stages as the project's owner."""
    owner = PydanticObjectId()
    project = await ProjectService().create_project(owner, "p")
    assert project.id is not None
    return project.id, owner


def _intent(
    pid: PydanticObjectId,
    *,
    message: str | None = None,
    payload: dict[str, object] | None = None,
) -> Intent:
    return Intent(
        project_id=pid,
        stage=Stage.design,
        action=IntentAction.refine,
        message=message,
        payload=payload or {},
    )


async def _ask(pid: PydanticObjectId, owner: PydanticObjectId) -> IntentResponse:
    """Run the generate that ends in a question."""
    return await Conductor().handle_intent(
        owner, _intent(pid, payload={"text": "a blog and discussion site"})
    )


# ---------------------------------------------------------------- the chain stops


async def test_a_clarification_does_not_fall_back_to_another_provider() -> None:
    """The bug in one line: another provider cannot answer the question, and asking it to design
    something instead is how a fine brief became a placeholder design."""
    asking = _install()
    project = await ProjectService().create_project(PydanticObjectId(), "p")

    with pytest.raises(ProviderError) as exc:
        await invoke_with_fallback(
            project, DesignCapability.from_text, lambda p: p.generate_from_text("a blog")
        )

    assert exc.value.detail is not None
    assert exc.value.detail["kind"] == "clarification"
    assert asking.prompts == ["a blog"]  # and nothing downstream was asked to design instead


# ---------------------------------------------------------------- parking the question


async def test_the_question_is_parked_and_put_to_the_user() -> None:
    pid, owner = await _project()
    _install()

    resp = await _ask(pid, owner)

    # No design was produced, so the stage waits on the user rather than showing a fallback.
    assert resp.to_status is StageStatus.awaiting_user
    assert resp.artifacts == []
    assert QUESTION in resp.messages[-1].content  # the question is in the conversation, verbatim

    pending = await DesignQuestionService().pending(pid)
    assert pending is not None
    assert pending.provider == "stitch"
    assert pending.question == QUESTION
    assert pending.suggestions == SUGGESTIONS
    assert pending.prompt == "a blog and discussion site"  # the brief, kept for the resumed call
    assert pending.workspace == "stitch-proj-9"
    assert pending.source == "text"


# ---------------------------------------------------------------- answering it


async def test_the_next_message_answers_it_and_resumes_the_same_provider() -> None:
    pid, owner = await _project()
    asking = _install()
    await _ask(pid, owner)

    resp = await Conductor().handle_intent(owner, _intent(pid, message="The public feed first"))

    assert len(resp.artifacts) == 1
    assert resp.artifacts[0].meta[META_PROVIDER] == "stitch"  # answered by whoever asked
    answered = asking.prompts[-1]
    # Self-contained: the provider's generate tool is stateless, so the answer alone would design
    # "the public feed" for an app it has forgotten.
    assert "a blog and discussion site" in answered
    assert QUESTION in answered
    assert "The public feed first" in answered
    # Back into the container the asking call opened, not a second one beside it.
    assert asking.workspaces[-1] == "stitch-proj-9"

    assert await DesignQuestionService().pending(pid) is None
    stored = await DesignQuestion.find_one(DesignQuestion.project_id == pid)
    assert stored is not None
    assert stored.status is DesignQuestionStatus.answered
    assert stored.answer == "The public feed first"


async def test_answering_a_question_asked_during_a_refine_refines() -> None:
    """A question raised by a refine resumes as a refine — a generate would start a new screen."""
    pid, owner = await _project()
    asking = _install()
    # A design to refine, produced before the provider starts asking.
    asking.asking = False
    await Conductor().handle_intent(owner, _intent(pid, payload={"text": "seed"}))
    asking.asking = True

    await Conductor().handle_intent(owner, _intent(pid, message="make it dark"))
    pending = await DesignQuestionService().pending(pid)
    assert pending is not None
    assert pending.source == "refine"

    resp = await Conductor().handle_intent(owner, _intent(pid, message="Dark navy"))

    assert len(resp.artifacts) == 1
    ref, instruction = asking.refine_prompts[-1]
    assert ref == "stitch-proj-9/screen-1"
    assert "make it dark" in instruction
    assert "Dark navy" in instruction


# ---------------------------------------------------------------- the escape hatch


async def test_dismissing_designs_without_the_provider_that_asked() -> None:
    """ "Design without Stitch" — the fail-soft path for a user who would rather not answer."""
    pid, owner = await _project()
    asking = _install()
    await _ask(pid, owner)
    before = len(asking.prompts)

    resp = await Conductor().handle_intent(owner, _intent(pid, payload={"dismiss_question": True}))

    assert len(resp.artifacts) == 1
    assert resp.artifacts[0].meta[META_PROVIDER] == "figma"  # the chain, minus the asker
    assert len(asking.prompts) == before  # stitch was not asked again
    assert await DesignQuestionService().pending(pid) is None


async def test_a_new_prompt_supersedes_the_question() -> None:
    """Typing a fresh brief is the user moving on — the old question must not eat that message."""
    pid, owner = await _project()
    _install()
    await _ask(pid, owner)

    resp = await Conductor().handle_intent(
        owner, _intent(pid, payload={"text": "actually, a recipe app"})
    )

    assert len(resp.artifacts) == 1
    provider_meta = resp.artifacts[0].meta[META_PROVIDER_META]
    assert isinstance(provider_meta, dict)
    # The new brief was generated from as-is, not folded into an answer to a stale question.
    assert provider_meta["prompt"] == "actually, a recipe app"
    assert await DesignQuestionService().pending(pid) is None


async def test_a_second_question_supersedes_the_first() -> None:
    pid, owner = await _project()
    asking = _install()
    await _ask(pid, owner)
    asking.asking = True  # it asks again when the answer comes back

    await Conductor().handle_intent(owner, _intent(pid, message="The feed"))

    pending = await DesignQuestionService().pending(pid)
    assert pending is not None
    assert await DesignQuestion.find(DesignQuestion.project_id == pid).count() == 2
    # Only the newest is open; the answered one keeps its answer rather than being reopened.
    assert pending.answer == ""
    # A follow-up resumes what the first one did (a generate) and still carries the whole
    # conversation, so the next answer does not design for an app the provider has forgotten.
    assert pending.source == "text"
    assert "a blog and discussion site" in pending.prompt
    assert "The feed" in pending.prompt


# ---------------------------------------------------------------- the HTTP surface


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_the_endpoint_reports_the_pending_question(client: AsyncClient) -> None:
    registered = await client.post(
        "/auth/register", json={"email": "q@example.com", "password": "password123"}
    )
    headers = {"Authorization": "Bearer " + registered.json()["access_token"]}
    created = await client.post("/projects", json={"name": "p"}, headers=headers)
    pid = PydanticObjectId(created.json()["id"])
    project = await Project.get(pid)
    assert project is not None
    _install()

    empty = await client.get(f"/projects/{pid}/design/question", headers=headers)
    assert empty.status_code == 200
    assert empty.json() is None  # nothing is waiting on the user

    await _ask(pid, project.user_id)

    resp = await client.get(f"/projects/{pid}/design/question", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "stitch"
    assert body["question"] == QUESTION
    assert body["suggestions"] == SUGGESTIONS
    assert body["source"] == "text"
