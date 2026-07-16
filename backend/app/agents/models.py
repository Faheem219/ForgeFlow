"""Model routing (phase-20) — the single place that maps a task to a model id (D4, cost discipline).

Code-shaped work (real generation/repair) goes to ``MODEL_CODEGEN`` (Sonnet); cheap
routing/classification/summaries go to ``MODEL_ROUTING`` (Haiku). Both ids come from the layered
config, so the admin dashboard can change them (phase-51/52) without touching any agent.
"""

from __future__ import annotations

from enum import Enum

from app.core.config import get_config


class TaskKind(Enum):
    """Agent task categories. A plain Enum (not StrEnum) so a member like ``title`` can't shadow a
    ``str`` method; the values are internal routing keys, never serialized."""

    # Real work → MODEL_CODEGEN
    codegen = "codegen"
    testgen = "testgen"
    repair = "repair"
    # Cheap work → MODEL_ROUTING
    classify = "classify"
    summarize = "summarize"
    route = "route"
    title = "title"


_CODEGEN_TASKS: frozenset[TaskKind] = frozenset(
    {TaskKind.codegen, TaskKind.testgen, TaskKind.repair}
)


def route(task_kind: TaskKind) -> str:
    """Return the configured model id for a task kind."""
    key = "model_codegen" if task_kind in _CODEGEN_TASKS else "model_routing"
    return str(get_config().get(key))
