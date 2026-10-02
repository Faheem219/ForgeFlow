"""One model turn through ForgeFlow's own seam — the transport every agent uses.

The benchmarks call the model exactly as the product does: the model id comes from role routing
(``MODEL_CODEGEN`` for code and tool use), the request goes through the provider transport
(phase-20/53/57: OpenRouter routing, parameter repairs, retries), and the ₹ cost comes from the
product's pricing table. Only the sampling temperature differs, because pass@1 is defined greedy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agents.anthropic_client import (
    MessageRequest,
    ToolUse,
    Transport,
    TurnComplete,
    default_transport,
)
from app.agents.cost import Usage
from app.agents.models import TaskKind, route
from app.agents.pricing import price_inr
from app.core.config import get_config


@dataclass
class Spend:
    """Running totals for one suite; ``budget_inr`` stops *new* work once crossed (rule 7)."""

    budget_inr: float | None = None
    #: The whole run's totals; a suite's spend also counts toward it, and its budget binds too.
    parent: Spend | None = None
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    inr: float = 0.0

    def add(self, model: str, usage: Usage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.reasoning_tokens += usage.reasoning_tokens
        self.inr += price_inr(model, usage.input_tokens, usage.output_tokens)
        if self.parent is not None:
            self.parent.add(model, usage)

    @property
    def exhausted(self) -> bool:
        if self.parent is not None and self.parent.exhausted:
            return True
        return self.budget_inr is not None and self.inr >= self.budget_inr

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "tokens": self.input_tokens + self.output_tokens,
            "inr": round(self.inr, 4),
        }


@dataclass
class Reply:
    text: str
    tool_uses: list[ToolUse] = field(default_factory=list)
    stop_reason: str = "end_turn"


class BenchLLM:
    def __init__(
        self,
        transport: Transport | None = None,
        *,
        role: TaskKind = TaskKind.codegen,
        max_tokens: int | None = None,
    ) -> None:
        self._transport = transport
        self._role = role
        self._max_tokens = max_tokens

    @property
    def model(self) -> str:
        return route(self._role)

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        spend: Spend,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = 0.0,
    ) -> Reply:
        model = self.model
        transport = self._transport or default_transport()
        request = MessageRequest(
            model=model,
            system=system,
            messages=messages,
            tools=tools or [],
            max_tokens=self._max_tokens or int(get_config().get("anthropic_max_output_tokens")),
            temperature=temperature,
        )
        final: TurnComplete | None = None
        async for event in transport.stream(request):
            if isinstance(event, TurnComplete):
                final = event
        if final is None:
            return Reply(text="", stop_reason="empty")
        spend.add(model, final.usage)
        return Reply(
            text=final.text, tool_uses=list(final.tool_uses), stop_reason=final.stop_reason
        )


__all__ = ["BenchLLM", "Reply", "Spend"]
