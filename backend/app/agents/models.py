"""Model routing (phase-20) — the single place that maps a task to a model id (D4, cost discipline).

Code-shaped work (real generation/repair) goes to ``MODEL_CODEGEN``; cheap routing/summaries go to
``MODEL_ROUTING``; **classification** goes to ``MODEL_CLASSIFY``, falling back to ``MODEL_ROUTING``
when blank (phase-60). Classification earns its own dial because it is the highest-volume cheap call
— every refine hits it — and on an OpenAI-compatible endpoint the cheapest capable model for a
one-word verdict is often not the one you want doing routing.

Every id comes from the layered config, so the admin dashboard changes them without touching an
agent, and **no id is hardcoded to a vendor**: the defaults happen to be Claude models, but an
OpenAI-compatible endpoint is a first-class target (D4 as annotated, phase-60).
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
    config = get_config()
    if task_kind in _CODEGEN_TASKS:
        return str(config.get("model_codegen"))
    if task_kind is TaskKind.classify:
        # Blank -> MODEL_ROUTING, so an existing deployment behaves exactly as before.
        explicit = str(config.get("model_classify")).strip()
        if explicit:
            return explicit
    return str(config.get("model_routing"))
