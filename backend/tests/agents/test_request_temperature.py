"""`MessageRequest.temperature`: only benchmarks set it; product requests stay byte-identical."""

from __future__ import annotations

from typing import Any

from app.agents.anthropic_client import MessageRequest, build_openai_params


def _request(**overrides: Any) -> MessageRequest:
    base: dict[str, Any] = {
        "model": "z-ai/glm-5.3-flash",
        "system": None,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [],
        "max_tokens": 1024,
    }
    base.update(overrides)
    return MessageRequest(**base)


def test_an_unset_temperature_sends_nothing() -> None:
    assert "temperature" not in build_openai_params(_request())


def test_a_set_temperature_is_sent_including_zero() -> None:
    assert build_openai_params(_request(temperature=0.0))["temperature"] == 0.0
    assert build_openai_params(_request(temperature=0.001))["temperature"] == 0.001
