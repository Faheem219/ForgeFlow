"""Design clarifying questions — parking a provider's question until the user answers it.

A design provider can answer a brief with a question instead of a design ("who is this app for?",
"shall I start with the public feed or the admin tools?"). The Stitch transport confirms a plain
proposal itself, but a real question is a decision, and ForgeFlow used to resolve it by failing
over to the next provider — which produced a design nobody asked for, from a brief that was fine.

This module is the other half of that: the question is recorded as a
:class:`~app.db.models.design_question.DesignQuestion`, the stage goes ``awaiting_user``, and the
user's next message is sent back to the provider as the answer. Nothing here is Stitch-specific —
the question arrives as a classified :class:`ProviderError` (``detail["kind"] == "clarification"``)
and any provider that learns to ask one gets this behaviour for free.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from beanie import PydanticObjectId

from app.core.errors import ProviderError
from app.db.models import DesignQuestion, DesignQuestionStatus
from app.db.models.common import utcnow

logger = logging.getLogger(__name__)

#: ``detail["kind"]`` marking a provider error as "the provider is waiting on the user".
CLARIFICATION_KIND = "clarification"

#: How much of the question/answer to carry back into the provider prompt. A prompt is not a
#: transcript: the brief is the bulk of it, and an unbounded quote of either half buries it.
MAX_QUESTION_CHARS = 2000
MAX_ANSWER_CHARS = 2000
#: Cap on the stored brief. Each answered round folds the previous prompt, the question and the
#: answer into the next one, so a user and a provider that keep talking would grow it without end.
MAX_PROMPT_CHARS = 8000


def clarification_detail(exc: ProviderError) -> dict[str, Any] | None:
    """The clarification payload carried by ``exc``, or ``None`` if it is an ordinary failure.

    Shape (see ``app/design/stitch.py``)::

        {"kind": "clarification",
         "detail": {"question": …, "suggestions": [...], "prompt": …, "workspace": …}}
    """
    body = exc.detail
    if not isinstance(body, Mapping) or str(body.get("kind")) != CLARIFICATION_KIND:
        return None
    detail = body.get("detail")
    return dict(detail) if isinstance(detail, Mapping) else {}


def answered_prompt(question: DesignQuestion, answer: str) -> str:
    """The brief, the provider's question, and the user's answer as one self-contained prompt.

    Provider generate tools are stateless — Stitch's takes a project id and a prompt, with no handle
    on the conversation that asked — so the answer cannot simply be sent as a reply. It has to
    carry the original brief and the question it answers, or the provider designs from the answer
    alone ("the admin tools first") having forgotten what app that was about.
    """
    parts: list[str] = []
    brief = question.prompt.strip()
    if brief:
        parts.append(brief)
    parts.append(
        f"You asked:\n{question.question.strip()[:MAX_QUESTION_CHARS]}\n\n"
        f"The answer is:\n{answer.strip()[:MAX_ANSWER_CHARS]}"
    )
    parts.append(
        "Generate the screens now, following that answer. Do not ask anything further — "
        "make sensible design decisions for whatever it leaves open."
    )
    return "\n\n".join(parts)


class DesignQuestionService:
    """Read/write the pending question for a project. One pending question at a time.

    A second question supersedes the first rather than queueing: they come from the same stalled
    generate, and a backlog of stale questions is worse than none — only the newest reflects what
    the provider is actually waiting on.
    """

    async def pending(self, project_id: PydanticObjectId) -> DesignQuestion | None:
        return (
            await DesignQuestion.find(
                {"project_id": project_id, "status": DesignQuestionStatus.pending}
            )
            .sort("-created_at", "-_id")
            .first_or_none()
        )

    async def record(
        self,
        project_id: PydanticObjectId,
        provider: str,
        detail: Mapping[str, Any],
        *,
        prompt: str = "",
        source: str = "text",
        design_ref: str = "",
    ) -> DesignQuestion:
        """Park a provider's question, superseding any earlier pending one."""
        await self._close_pending(project_id, DesignQuestionStatus.dismissed)
        suggestions = [str(s).strip() for s in (detail.get("suggestions") or []) if str(s).strip()]
        question = DesignQuestion(
            project_id=project_id,
            provider=provider,
            question=str(detail.get("question") or "").strip(),
            suggestions=suggestions,
            # The provider echoes the prompt it actually received; the caller's own copy is the
            # fallback for a provider that does not.
            prompt=(str(detail.get("prompt") or "").strip() or prompt.strip())[:MAX_PROMPT_CHARS],
            workspace=str(detail.get("workspace") or "").strip(),
            source=source,
            design_ref=design_ref,
        )
        await question.insert()
        logger.info("design: parked a clarifying question from %s for the user", provider)
        return question

    async def answer(self, question: DesignQuestion, answer: str) -> None:
        question.status = DesignQuestionStatus.answered
        question.answer = answer.strip()
        question.updated_at = utcnow()
        await question.save()

    async def dismiss(self, project_id: PydanticObjectId) -> DesignQuestion | None:
        """Close the pending question without answering it. Returns the one that was closed."""
        return await self._close_pending(project_id, DesignQuestionStatus.dismissed)

    async def _close_pending(
        self, project_id: PydanticObjectId, status: DesignQuestionStatus
    ) -> DesignQuestion | None:
        existing = await self.pending(project_id)
        if existing is None:
            return None
        existing.status = status
        existing.updated_at = utcnow()
        await existing.save()
        return existing


__all__ = [
    "CLARIFICATION_KIND",
    "DesignQuestionService",
    "answered_prompt",
    "clarification_detail",
]
