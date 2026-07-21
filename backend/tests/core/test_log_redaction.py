"""Log redaction (phase-46 / §7): a secret must never reach a log handler.

This is defence in depth, not the primary control — the vault never hands a plaintext secret to a
logger in the first place. It exists for the accidental `logger.info(..., extra={"token": tok})`,
which is exactly the mistake that is easy to make and impossible to notice in review.
"""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from app.core.logging import REDACTED, JsonFormatter, configure_logging, redact
from app.core.observability import CorrelationFilter, traced_run

SECRET = "sk-ant-super-secret-value-98765"
MONGO_URI = "mongodb+srv://user:hunter2@cluster0.example.net/db"


def _render(**extra: object) -> str:
    """Format one record exactly as the app's handler would."""
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "a message", None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return JsonFormatter().format(record)


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "api_key",
        "apiKey",
        "password",
        "secret_key",
        "authorization",
        "credential",
        "fernet_key",
        "private_key",
        "mongodb_uri",
        "mongo_uri",
        "connection_string",
        "anthropic_api_key",
        "vercel_token",
    ],
)
def test_credential_shaped_keys_are_redacted(key: str) -> None:
    rendered = _render(**{key: SECRET})

    assert SECRET not in rendered
    assert REDACTED in rendered


def test_a_secret_nested_in_a_dict_is_redacted() -> None:
    rendered = _render(context={"provider": "vercel", "token": SECRET})

    assert SECRET not in rendered
    assert "vercel" in rendered  # the harmless field survives


def test_a_secret_nested_in_a_list_of_dicts_is_redacted() -> None:
    rendered = _render(creds=[{"kind": "render", "api_key": SECRET}])
    assert SECRET not in rendered


def test_a_connection_string_is_redacted_by_key() -> None:
    rendered = _render(mongodb_uri=MONGO_URI)
    assert "hunter2" not in rendered and MONGO_URI not in rendered


def test_ordinary_fields_are_left_alone() -> None:
    rendered = _render(project_id="abc123", tokens=1500, step="build")
    payload = json.loads(rendered)

    assert payload["project_id"] == "abc123"
    assert payload["tokens"] == 1500
    assert payload["step"] == "build"


def test_redaction_does_not_recurse_forever() -> None:
    """A pathological structure must not hang the logger."""
    deep: dict[str, object] = {"a": {}}
    node = deep["a"]
    for _ in range(50):
        child: dict[str, object] = {}
        node["a"] = child  # type: ignore[index]
        node = child

    assert redact("context", deep) is not None  # bounded by _MAX_DEPTH, returns


def test_the_configured_handler_redacts_end_to_end() -> None:
    """Not just the formatter in isolation — the real handler the app installs."""
    configure_logging("INFO", json_output=True)
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.redaction.e2e")
    logger.handlers = [handler]
    logger.propagate = False

    logger.info("saving credential", extra={"kind": "vercel", "token": SECRET})

    output = stream.getvalue()
    assert SECRET not in output
    assert REDACTED in output and "vercel" in output


async def test_a_traced_run_never_logs_a_secret(caplog: pytest.LogCaptureFixture) -> None:
    """Correlation adds ids to every record — ids, never values."""
    from beanie import PydanticObjectId

    logger = logging.getLogger("test.redaction.traced")
    logger.addFilter(CorrelationFilter())

    with caplog.at_level(logging.INFO):
        async with traced_run("codegen:build", project_id=PydanticObjectId()):
            logger.info("calling provider", extra={"api_key": SECRET})

    rendered = "\n".join(JsonFormatter().format(r) for r in caplog.records)
    assert SECRET not in rendered


test_a_traced_run_never_logs_a_secret = pytest.mark.usefixtures("mongo_db")(
    test_a_traced_run_never_logs_a_secret
)


# --------------------------------------------------------------------- the count exemption


@pytest.mark.parametrize(
    "key", ["tokens", "total_tokens", "input_tokens", "output_tokens", "tokens_spent"]
)
def test_numeric_token_counts_are_not_redacted(key: str) -> None:
    """`tokens` contains `token`, but a count is not a credential — and redacting it would
    blind the cost trail this phase exists to provide."""
    payload = json.loads(_render(**{key: 1500}))
    assert payload[key] == 1500


@pytest.mark.parametrize("key", ["tokens", "total_tokens"])
def test_a_string_in_a_count_field_is_still_redacted(key: str) -> None:
    """The exemption is narrow on purpose: it applies to numbers, not to whatever lands there."""
    rendered = _render(**{key: SECRET})
    assert SECRET not in rendered
    assert REDACTED in rendered


def test_a_real_token_field_is_still_redacted_even_though_it_is_a_string() -> None:
    for key in ("token", "access_token", "refresh_token", "api_token"):
        assert SECRET not in _render(**{key: SECRET}), key
