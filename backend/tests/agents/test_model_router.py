"""Task → model routing honors config (phase-20)."""

from __future__ import annotations

import pytest

from app.agents.models import TaskKind, route
from app.core.config import reset_config


def test_code_tasks_route_to_codegen_model() -> None:
    for kind in (TaskKind.codegen, TaskKind.testgen, TaskKind.repair):
        assert route(kind) == "claude-sonnet-5"


def test_cheap_tasks_route_to_routing_model() -> None:
    for kind in (TaskKind.classify, TaskKind.summarize, TaskKind.route, TaskKind.title):
        assert route(kind) == "claude-haiku-4-5-20251001"


def test_routing_follows_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_CODEGEN", "claude-opus-4-8")
    monkeypatch.setenv("MODEL_ROUTING", "some-small-model")
    reset_config()

    assert route(TaskKind.codegen) == "claude-opus-4-8"
    assert route(TaskKind.classify) == "some-small-model"
