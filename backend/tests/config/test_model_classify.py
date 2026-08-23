"""MODEL_CLASSIFY routing (phase-60).

Classification earns its own dial because it is the highest-volume cheap call — every refine hits
it — and on an OpenAI-compatible endpoint the cheapest capable model for a one-word verdict is often
not the one you want doing routing. Blank must behave exactly as before.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.agents.models import TaskKind, route
from app.core.config import reset_config


@pytest.fixture(autouse=True)
def _clean_config() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def test_blank_classify_falls_back_to_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing deployment must change nothing when it does not set the new key."""
    monkeypatch.setenv("MODEL_ROUTING", "cheap-model")
    monkeypatch.delenv("MODEL_CLASSIFY", raising=False)
    reset_config()

    assert route(TaskKind.classify) == "cheap-model"


def test_a_set_classify_model_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_ROUTING", "cheap-model")
    monkeypatch.setenv("MODEL_CLASSIFY", "cheaper-model")
    reset_config()

    assert route(TaskKind.classify) == "cheaper-model"


def test_whitespace_only_classify_is_treated_as_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_ROUTING", "cheap-model")
    monkeypatch.setenv("MODEL_CLASSIFY", "   ")
    reset_config()

    assert route(TaskKind.classify) == "cheap-model"


def test_classify_never_reaches_the_codegen_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cost discipline (Golden Rule 7): a one-word verdict must not be billed at codegen rates."""
    monkeypatch.setenv("MODEL_CODEGEN", "expensive-model")
    monkeypatch.setenv("MODEL_ROUTING", "cheap-model")
    monkeypatch.delenv("MODEL_CLASSIFY", raising=False)
    reset_config()

    assert route(TaskKind.classify) != "expensive-model"


def test_other_cheap_tasks_still_use_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only classify moved; summaries/routing/titles are unchanged."""
    monkeypatch.setenv("MODEL_ROUTING", "cheap-model")
    monkeypatch.setenv("MODEL_CLASSIFY", "cheaper-model")
    reset_config()

    assert route(TaskKind.summarize) == "cheap-model"
    assert route(TaskKind.route) == "cheap-model"
    assert route(TaskKind.title) == "cheap-model"


def test_real_work_still_uses_the_codegen_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_CODEGEN", "expensive-model")
    monkeypatch.setenv("MODEL_CLASSIFY", "cheaper-model")
    reset_config()

    assert route(TaskKind.codegen) == "expensive-model"
    assert route(TaskKind.repair) == "expensive-model"
    assert route(TaskKind.testgen) == "expensive-model"
