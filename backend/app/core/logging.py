"""Structured (JSON) logging setup.

Kept dependency-free. Establishes the JSON format, a single configured root handler, and
**secret redaction** (phase-34): any structured extra whose key looks credential-ish is replaced
with :data:`REDACTED` before it reaches a handler.

Redaction is defense-in-depth, not the primary control — the vault (``app/deploy/secrets.py``)
never hands a plaintext secret to a logger in the first place. This catches the accidental
``logger.info(..., extra={"token": tok})`` that would otherwise leak.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

_RESERVED = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}

REDACTED = "***redacted***"

#: Key names whose *values* must never appear in logs.
SENSITIVE_KEY_RE = re.compile(
    r"(secret|token|password|passwd|api[_-]?key|apikey|authorization|auth_header"
    r"|credential|fernet|private[_-]?key|mongo(db)?_uri|connection[_-]?string)",
    re.IGNORECASE,
)

#: Numeric *counts* whose names contain a sensitive substring — ``tokens`` matches ``token``.
#: Redacting these would blind the cost trail (phase-46) while protecting nothing: a token **count**
#: is not a credential. The exemption is deliberately narrow — it applies only when the value is
#: actually a number, so ``tokens="sk-..."`` is still redacted.
SAFE_COUNT_KEY_RE = re.compile(
    r"^(total_|input_|output_|prompt_|completion_|cache_\w+_)?tokens(_\w+)?$", re.IGNORECASE
)

#: Bound on how deep redaction walks nested structures (logs should not be deep).
_MAX_DEPTH = 4


def _is_safe_count(key: str, value: Any) -> bool:
    return bool(SAFE_COUNT_KEY_RE.match(key)) and isinstance(value, (int, float))


def redact(key: str, value: Any, _depth: int = 0) -> Any:
    """Return ``value`` with credential-ish entries replaced by :data:`REDACTED`."""
    if SENSITIVE_KEY_RE.search(key) and not _is_safe_count(key, value):
        return REDACTED
    if _depth >= _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {k: redact(str(k), v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(key, v, _depth + 1) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    """Render log records as one-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Promote any structured extras passed via logger.*(..., extra={...}), redacting
        # anything that looks like a credential (phase-34 non-leakage).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload.setdefault(key, redact(key, value))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Install a single stdout handler on the root logger."""
    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
