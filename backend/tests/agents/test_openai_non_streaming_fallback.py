"""The non-streaming fallback (phase-57 task 8).

Some models/organizations refuse `stream: true`. The `no_stream` repair is the only one that
changes the *response* path rather than the request, so the property that matters is that the loop
cannot tell the difference: the same `TextDelta` + `TurnComplete` events come out, carrying the same
text, tool calls, usage and stop reason. Only *live* token streaming is lost, which is strictly
better than the stage failing.
"""

from __future__ import annotations

from typing import Any

from app.agents.anthropic_client import TextDelta, TurnComplete, _from_completion


class _Function:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id: str, name: str, arguments: str) -> None:
        self.id = call_id
        self.function = _Function(name, arguments)


class _Message:
    def __init__(self, content: str | None, tool_calls: list[_ToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, message: _Message, finish_reason: str | None) -> None:
        self.message = message
        self.finish_reason = finish_reason


class _Usage:
    def __init__(self, prompt: int, completion: int) -> None:
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _Completion:
    def __init__(self, choices: list[_Choice], usage: Any = None) -> None:
        self.choices = choices
        self.usage = usage


def test_text_and_usage_fold_into_the_streamed_shape() -> None:
    response = _Completion([_Choice(_Message("hello world"), "stop")], usage=_Usage(120, 34))

    events = list(_from_completion(response))

    assert isinstance(events[0], TextDelta)
    assert events[0].text == "hello world"
    final = events[-1]
    assert isinstance(final, TurnComplete)
    assert final.text == "hello world"
    assert final.stop_reason == "end_turn"
    # Cost accounting must not silently read zero just because streaming was refused.
    assert (final.usage.input_tokens, final.usage.output_tokens) == (120, 34)


def test_tool_calls_survive_the_fallback() -> None:
    """Without this the agent loop would see a turn with no tools and stop mid-build."""
    response = _Completion(
        [
            _Choice(
                _Message(
                    None,
                    [
                        _ToolCall("call_1", "write_file", '{"path": "a.ts", "content": "x"}'),
                        _ToolCall("call_2", "git_commit", '{"message": "wip"}'),
                    ],
                ),
                "tool_calls",
            )
        ]
    )

    final = list(_from_completion(response))[-1]

    assert isinstance(final, TurnComplete)
    assert [t.name for t in final.tool_uses] == ["write_file", "git_commit"]
    assert final.tool_uses[0].input == {"path": "a.ts", "content": "x"}
    assert final.stop_reason == "tool_use"  # mapped, exactly as the streamed path maps it


def test_a_length_finish_maps_the_same_way_as_the_streamed_path() -> None:
    response = _Completion([_Choice(_Message("truncated"), "length")])

    final = list(_from_completion(response))[-1]

    assert isinstance(final, TurnComplete)
    assert final.stop_reason == "max_tokens"


def test_an_empty_response_yields_a_terminal_event_rather_than_nothing() -> None:
    """A turn that produces no events would hang the loop waiting for a TurnComplete."""
    events = list(_from_completion(_Completion([])))

    assert len(events) == 1
    assert isinstance(events[0], TurnComplete)


def test_a_toolless_text_turn_emits_exactly_one_delta_then_the_terminal_event() -> None:
    events = list(_from_completion(_Completion([_Choice(_Message("hi"), "stop")])))

    assert [type(e) for e in events] == [TextDelta, TurnComplete]


def test_missing_usage_degrades_to_zero_rather_than_raising() -> None:
    final = list(_from_completion(_Completion([_Choice(_Message("hi"), "stop")])))[-1]

    assert isinstance(final, TurnComplete)
    assert final.usage.input_tokens == 0
