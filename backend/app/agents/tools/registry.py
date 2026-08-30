"""Tool registry + dispatcher (phase-21).

Validates a model's tool call against the tool's schema, dispatches to the sandbox-bound handler,
records the call on the ``Run`` trace, and returns a structured JSON result string fed back to the
model. Errors (invalid args, unsafe paths, service failures) come back as ``{"ok": false, ...}`` so
the model can correct — the loop never crashes on a bad call.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agents.tools.context import ToolContext
from app.core.errors import NotFoundError, ProviderError, UserError
from app.db.models import Run
from app.db.models.common import utcnow

ToolHandler = Callable[[ToolContext, Any], Awaitable[str]]
# The phase-20 client's tool dispatcher signature.
ToolDispatch = Callable[[str, dict[str, Any]], Awaitable[str]]


def tool_ok(**data: Any) -> str:
    return json.dumps({"ok": True, **data})


def tool_err(message: str, **data: Any) -> str:
    return json.dumps({"ok": False, "error": message, **data})


def to_input_schema(model: type[BaseModel]) -> dict[str, Any]:
    """A flat Anthropic ``input_schema`` (object) from a pydantic args model."""
    schema = model.model_json_schema()
    properties = schema.get("properties", {})
    for prop in properties.values():
        prop.pop("title", None)  # noise; Anthropic doesn't need per-field titles
    return {
        "type": "object",
        "properties": properties,
        "required": schema.get("required", []),
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler
    guardrails: str = field(default="")  # human note for the tool catalog

    def anthropic_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": to_input_schema(self.args_model),
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {t.name: t for t in tools}

    def names(self) -> list[str]:
        return sorted(self._tools)

    def tools(self) -> list[Tool]:
        """The registered tools, in registration order (a caller may extend them per call)."""
        return list(self._tools.values())

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def anthropic_tools(self, exclude: set[str] | None = None) -> list[dict[str, Any]]:
        """The tool list to hand the phase-20 client, optionally dropping tools by name.

        ``exclude`` lets a caller withhold a tool that is safe to run deterministically but unsafe
        as a model choice — the build loop uses it to hide ``instantiate_skeleton`` (phase-54),
        which re-copies the skeleton and overwrites app.ts/routes.tsx if the model calls it again.
        """
        excluded = exclude or set()
        return [
            tool.anthropic_schema() for tool in self._tools.values() if tool.name not in excluded
        ]

    async def dispatch(self, ctx: ToolContext, name: str, raw_args: dict[str, Any]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return tool_err(f"Unknown tool: {name!r}", available=self.names())

        try:
            args = tool.args_model.model_validate(raw_args or {})
        except ValidationError as exc:
            await _record(ctx.run, name, ok=False)
            return tool_err(
                "Invalid arguments",
                issues=[f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()],
            )

        ok = True
        try:
            result = await tool.handler(ctx, args)
        except (UserError, NotFoundError, ProviderError) as exc:
            ok = False
            result = tool_err(str(exc))
        await _record(ctx.run, name, ok=ok)
        return result

    def catalog_markdown(self) -> str:
        """A tool catalog (schemas + guardrails) for prompt authors."""
        lines = ["# ForgeFlow agent tools\n"]
        for name in self.names():
            tool = self._tools[name]
            lines.append(f"## `{name}`\n")
            lines.append(f"{tool.description}\n")
            if tool.guardrails:
                lines.append(f"**Guardrails:** {tool.guardrails}\n")
            lines.append("```json")
            lines.append(json.dumps(to_input_schema(tool.args_model), indent=2))
            lines.append("```\n")
        return "\n".join(lines)


async def _record(run: Run, name: str, *, ok: bool) -> None:
    run.tool_calls.append({"tool": name, "ok": ok, "at": utcnow().isoformat()})
    await run.save()


def make_tool_dispatch(registry: ToolRegistry, ctx: ToolContext) -> ToolDispatch:
    """Bind a registry + context into the ``(name, args) -> result`` dispatcher the client uses."""

    async def dispatch(name: str, raw_args: dict[str, Any]) -> str:
        return await registry.dispatch(ctx, name, raw_args)

    return dispatch
