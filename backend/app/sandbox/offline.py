"""Recognise failures caused by the sandbox having no network (phase-65).

A sandbox has no internet while generated code runs, builds or is tested — only package installs
borrow a registry route (:mod:`app.sandbox.network`). Code that assumes otherwise fails in ways a
model reads as a puzzle and then re-derives, attempt after attempt: the reported case was a repair
session that ended with the model reasoning it was *"unable to download mongod due to no network
access"*. This module names those failures deterministically so nobody pays to rediscover them.

Two verdicts, deliberately different:

- **not repairable** — the sandbox itself lacks something no patch can supply (the test database's
  ``mongod``). The repair loop stops before spending an attempt (``REASON_ENVIRONMENT``).
- **repairable** — the *code* reached for the network (an external API, a runtime browser
  download). The agent gets a precise hint and fixes the code.

Pure and table-driven, like :mod:`app.agents.build_errors`: substrings are matched
case-insensitively against a failing test's message/stack, most specific first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class OfflineKind(StrEnum):
    test_db_unavailable = "test_db_unavailable"  # no mongod to run tests against
    runtime_download = "runtime_download"  # a dependency tried to fetch a binary at run time
    external_host = "external_host"  # code/tests called a host on the internet


@dataclass(frozen=True)
class OfflineDiagnosis:
    """A failure explained by the sandbox's network posture, and what to do about it."""

    kind: OfflineKind
    repairable: bool
    reason: str
    signal: str
    hint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": str(self.kind),
            "repairable": self.repairable,
            "reason": self.reason,
            "signal": self.signal,
            "hint": self.hint,
        }


#: The marker the skeleton's test helper puts in its error (app-skeleton/backend/src/test/db.ts).
TEST_DB_UNAVAILABLE_MARKER = "[forgeflow:test-db-unavailable]"

#: Evidence that the in-memory test database could not get a ``mongod``. The helper's own marker
#: first; the rest are mongodb-memory-server's messages for "no binary, and downloading it failed or
#: is disabled", for a workspace that uses the library directly.
_TEST_DB_SIGNALS: tuple[str, ...] = (
    TEST_DB_UNAVAILABLE_MARKER,
    "could not find an valid binary path",  # sic — the library's wording
    "no binary at path",
    "fastdl.mongodb.org",
    "mongobinarydownload",
)
_TEST_DB_REASON = "the sandbox has no MongoDB binary for tests"
_TEST_DB_HINT = (
    "The in-memory test database could not start because the sandbox image has no baked `mongod` "
    "(it predates phase-65, or was built without it) — and a sandbox has no internet to download "
    "one. Rebuild the image with `make sandbox-build`; each sandbox is recreated from the new "
    "image on next use, keeping its code. No code change can fix this."
)

_BROWSER_SIGNALS: tuple[str, ...] = (
    "could not find chrome",
    "could not find chromium",
    "could not find expected browser",
)
_BROWSER_REASON = "a dependency tried to download a browser at run time"
_BROWSER_HINT = (
    "A dependency (Puppeteer) tried to download a browser while the code ran, and the sandbox has "
    "no internet. Use Playwright instead: its Chromium is already inside the sandbox and the e2e "
    "setup uses it."
)

#: ``getaddrinfo ENOTFOUND api.example.com`` / ``querySrv EAI_AGAIN _mongodb._tcp.x.mongodb.net``.
_UNRESOLVED_HOST = re.compile(
    r"\b(?:getaddrinfo|querysrv)\s+(?:enotfound|eai_again)\s+([a-z0-9_.-]+)", re.IGNORECASE
)
#: Never "the internet": the container itself. Dot-less names (docker services such as
#: ``forgeflow-appdb``) are excluded separately — the preview's database diagnostics own those.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"})
_EXTERNAL_REASON = "the code called a host on the internet, which the sandbox cannot reach"
_EXTERNAL_HINT = (
    "The sandbox has no internet while code runs or tests execute, so `{host}` cannot be reached — "
    "by design, not by accident. Tests must not call external services: mock the client "
    "(`jest.mock` / `vi.mock`) or inject it so a test can pass a fake. For data, use the test "
    "database (`useTestDb()` from `backend/src/test/db`), never a real cluster."
)


def _external_host(text: str) -> str | None:
    for match in _UNRESOLVED_HOST.finditer(text):
        host = match.group(1).strip(".").lower()
        host = host.removeprefix("_mongodb._tcp.")  # an SRV lookup names the cluster behind it
        if host in _LOCAL_HOSTS or "." not in host:
            continue
        return host
    return None


def diagnose_offline(text: str) -> OfflineDiagnosis | None:
    """Explain ``text`` (a failing test's message/stack) by the sandbox's network posture, if able.

    Returns ``None`` for an ordinary failure. Precedence is most-specific first: a mongod download
    that failed *because* its host did not resolve is the missing test database, not an external
    call the code should mock.
    """
    if not text:
        return None
    lowered = text.lower()
    for signal in _TEST_DB_SIGNALS:
        if signal in lowered:
            return OfflineDiagnosis(
                OfflineKind.test_db_unavailable, False, _TEST_DB_REASON, signal, _TEST_DB_HINT
            )
    for signal in _BROWSER_SIGNALS:
        if signal in lowered:
            return OfflineDiagnosis(
                OfflineKind.runtime_download, True, _BROWSER_REASON, signal, _BROWSER_HINT
            )
    host = _external_host(text)
    if host is not None:
        return OfflineDiagnosis(
            OfflineKind.external_host,
            True,
            _EXTERNAL_REASON,
            host,
            _EXTERNAL_HINT.format(host=host),
        )
    return None


__all__ = [
    "OfflineDiagnosis",
    "OfflineKind",
    "TEST_DB_UNAVAILABLE_MARKER",
    "diagnose_offline",
]
