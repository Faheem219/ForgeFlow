"""Reading a provider's own 400 (phase-57).

The reported failure — `gpt-5.4-mini` over the official OpenAI endpoint — is the first case here,
verbatim. The provider states the offending parameter and its replacement outright, so the fix is
to *read the error* rather than to keep a table of which model wants which spelling.

The load-bearing safety property is the negative half: anything that cannot be attributed to a
parameter **we actually sent** returns ``None`` and stays fatal. Guessing there would turn a fast,
clear failure into a slow, confusing one, and could mask a real bug in our own request building.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.agents.provider_compat import Adaptation, diagnose_param_error

openai_sdk = pytest.importorskip("openai", reason="the openai SDK is optional")

#: A body shaped like the ones `build_openai_params` produces, used as "what we actually sent".
SENT: dict[str, Any] = {
    "model": "gpt-5.4-mini",
    "max_tokens": 4096,
    "temperature": 0.2,
    "messages": [{"role": "system", "content": "be helpful"}, {"role": "user", "content": "hi"}],
    "stream": True,
    "stream_options": {"include_usage": True},
}


def _error(status: int, message: str, body: dict[str, Any] | None = None) -> Any:
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com/v1"))
    return openai_sdk.APIStatusError(message, response=response, body=body)


# ---------------------------------------------------------------- the reported failure


def test_the_reported_gpt5_max_tokens_error_becomes_a_rename() -> None:
    """The exact payload from the bug report. This is the case the phase exists for."""
    message = (
        "Unsupported parameter: 'max_tokens' is not supported with this model. "
        "Use 'max_completion_tokens' instead."
    )
    exc = _error(
        400,
        f"Error code: 400 - {{'error': {{'message': \"{message}\", "
        "'type': 'invalid_request_error', 'param': 'max_tokens', "
        "'code': 'unsupported_parameter'}}",
        body={
            "message": message,
            "type": "invalid_request_error",
            "param": "max_tokens",
            "code": "unsupported_parameter",
        },
    )

    repair = diagnose_param_error(exc, SENT)

    assert repair is not None
    assert repair.adaptation is Adaptation.rename
    assert repair.param == "max_tokens"
    assert repair.replacement == "max_completion_tokens"


def test_the_same_error_is_read_from_the_message_when_param_is_absent() -> None:
    """Not every compatible endpoint populates `error.param`; the text must still be enough."""
    exc = _error(
        400,
        "Unsupported parameter: 'max_tokens' is not supported with this model. "
        "Use 'max_completion_tokens' instead.",
    )

    repair = diagnose_param_error(exc, SENT)

    assert repair is not None
    assert repair.adaptation is Adaptation.rename
    assert repair.replacement == "max_completion_tokens"


# ---------------------------------------------------------------- the other classes


def test_an_unsupported_value_becomes_a_drop() -> None:
    """Reasoning models fix temperature at its default; there is no replacement to rename to."""
    exc = _error(
        400,
        "Unsupported value: 'temperature' does not support 0.2 with this model.",
        body={"param": "temperature", "code": "unsupported_value"},
    )

    repair = diagnose_param_error(exc, SENT)

    assert repair is not None
    assert repair.adaptation is Adaptation.drop
    assert repair.param == "temperature"


def test_mistrals_refused_usage_trailer_becomes_a_drop() -> None:
    """The behaviour phase-53 special-cased, now reached through the general mechanism."""
    exc = _error(
        422,
        'Error code: 422 - {"detail":[{"type":"extra_forbidden","loc":["body","stream_options"],'
        '"msg":"Extra inputs are not permitted"}]}',
    )

    repair = diagnose_param_error(exc, SENT)

    assert repair is not None
    assert repair.adaptation is Adaptation.drop
    assert repair.param == "stream_options"


def test_a_streaming_refusal_becomes_the_non_streaming_fallback() -> None:
    """`stream` must never be *dropped* — dropping it leaves stream=True's default behaviour."""
    exc = _error(
        400,
        "Your organization must be verified to stream this model.",
        body={"param": "stream", "code": "unsupported_value"},
    )

    repair = diagnose_param_error(exc, SENT)

    assert repair is not None
    assert repair.adaptation is Adaptation.no_stream


def test_a_refused_system_role_becomes_a_message_shape_repair() -> None:
    """`messages` must never be dropped — the whole transcript lives there."""
    exc = _error(
        400,
        "Invalid value for 'messages[0].role': 'system' is not supported with this model. "
        "Use 'developer' instead.",
        body={"param": "messages.0.role", "code": "unsupported_value"},
    )

    repair = diagnose_param_error(exc, SENT)

    assert repair is not None
    assert repair.adaptation is Adaptation.developer_role
    assert repair.param == "messages"


# ---------------------------------------------------------------- the safety half


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (400, "Invalid request"),  # no parameter named at all
        (400, "Invalid API key provided"),  # a credentials problem, not a shape problem
        (401, "Incorrect API key provided"),
        (429, "Rate limit exceeded, please try again"),
        (500, "Internal server error"),  # an outage — the transient ladder's job
        (503, "The server is busy right now"),
    ],
)
def test_unattributable_failures_are_not_repairable(status: int, message: str) -> None:
    """Returning None here is a feature: an unrepairable 400 must stay fast and fatal."""
    assert diagnose_param_error(_error(status, message), SENT) is None


def test_a_500_naming_a_parameter_is_still_not_repairable() -> None:
    """A server error that happens to mention a field is an outage, not a rejected field."""
    assert diagnose_param_error(_error(500, "max_tokens blew up"), SENT) is None


def test_a_parameter_we_never_sent_is_not_ours_to_repair() -> None:
    """Otherwise a provider's prose could send us editing a request that was never the problem."""
    exc = _error(
        400,
        "Unsupported parameter: 'reasoning_effort' is not supported.",
        body={"param": "reasoning_effort", "code": "unsupported_parameter"},
    )

    assert diagnose_param_error(exc, SENT) is None


def test_the_longest_matching_parameter_wins() -> None:
    """`max_tokens` must not be shadowed by a shorter key that happens to be a substring."""
    sent = {**SENT, "tokens": 1}
    exc = _error(400, "Unsupported parameter: max_tokens is not supported with this model.")

    repair = diagnose_param_error(exc, sent)

    assert repair is not None
    assert repair.param == "max_tokens"
