"""Failures the sandbox's missing network explains are named, not re-derived (phase-65).

The reported session ended with a model reasoning it was *"unable to download mongod due to no
network access"* — after spending its attempts discovering it. These are the signatures that let
the loop say so up front: an unrepairable missing test database (stop), or code that reached for
the internet (repairable, with a hint naming the host).
"""

from __future__ import annotations

import pytest

from app.sandbox.offline import (
    TEST_DB_UNAVAILABLE_MARKER,
    OfflineKind,
    diagnose_offline,
)

# The exact errors, as the skeleton's helper and mongodb-memory-server print them.
_HELPER = (
    "[forgeflow:test-db-unavailable] The in-memory MongoDB could not start: MongoBinary.getPath: "
    'could not find an valid binary path! (Got: "undefined", RUNTIME_DOWNLOAD: "false")'
)
_DOWNLOAD = (
    'Download failed for url "https://fastdl.mongodb.org/linux/mongodb-linux-x86_64-debian12-8.2.6'
    '.tgz", Details:\ngetaddrinfo EAI_AGAIN fastdl.mongodb.org'
)


@pytest.mark.parametrize(
    "text",
    [
        _HELPER,
        _DOWNLOAD,
        'MongoBinary.getPath: could not find an valid binary path! (Got: "undefined")',
        'No Binary at path "/usr/local/bin/mongod" was found! (ENOENT)',
        "at MongoBinaryDownload.startDownload (node_modules/mongodb-memory-server-core/lib/...)",
    ],
)
def test_a_missing_test_database_is_an_unrepairable_environment_problem(text: str) -> None:
    diagnosis = diagnose_offline(text)

    assert diagnosis is not None
    assert diagnosis.kind is OfflineKind.test_db_unavailable
    assert diagnosis.repairable is False
    # It says what fixes it — and that code does not.
    assert "make sandbox-build" in diagnosis.hint
    assert "No code change" in diagnosis.hint


def test_the_helper_marker_is_the_one_the_skeleton_throws() -> None:
    """The contract with templates/app-skeleton/backend/src/test/db.ts — drift breaks detection."""
    from app.agents.tools.skeleton import skeleton_source_dir

    helper = (skeleton_source_dir() / "backend/src/test/db.ts").read_text(encoding="utf-8")
    assert TEST_DB_UNAVAILABLE_MARKER in helper


def test_a_failed_mongod_download_is_not_mistaken_for_an_external_call() -> None:
    """Its host does not resolve either — but mocking cannot fix a missing binary."""
    diagnosis = diagnose_offline(_DOWNLOAD)

    assert diagnosis is not None
    assert diagnosis.kind is OfflineKind.test_db_unavailable


@pytest.mark.parametrize(
    "text",
    [
        "Error: Could not find Chrome (ver. 131.0.6778.85). This can occur if ...",
        "Could not find Chromium (rev. 1108766). Run `npm install` to download it",
    ],
)
def test_a_runtime_browser_download_is_repairable(text: str) -> None:
    diagnosis = diagnose_offline(text)

    assert diagnosis is not None
    assert diagnosis.kind is OfflineKind.runtime_download
    assert diagnosis.repairable is True
    assert "Playwright" in diagnosis.hint


@pytest.mark.parametrize(
    ("text", "host"),
    [
        (
            "FetchError: request to https://api.stripe.com/v1 failed, reason: getaddrinfo "
            "ENOTFOUND api.stripe.com",
            "api.stripe.com",
        ),
        ("Error: getaddrinfo EAI_AGAIN hooks.slack.com", "hooks.slack.com"),
        # An SRV lookup names the cluster behind `_mongodb._tcp.`, which is what the user knows.
        (
            "MongoServerSelectionError: querySrv ENOTFOUND _mongodb._tcp.c0.ab1cd.mongodb.net",
            "c0.ab1cd.mongodb.net",
        ),
    ],
)
def test_a_call_to_the_internet_is_repairable_and_names_the_host(text: str, host: str) -> None:
    diagnosis = diagnose_offline(text)

    assert diagnosis is not None
    assert diagnosis.kind is OfflineKind.external_host
    assert diagnosis.repairable is True
    assert diagnosis.signal == host
    assert f"`{host}`" in diagnosis.hint
    assert "useTestDb()" in diagnosis.hint  # the data alternative, for a real-cluster connection


@pytest.mark.parametrize(
    "text",
    [
        # A docker service name: the preview's own database diagnostics own this one.
        "MongooseServerSelectionError: getaddrinfo EAI_AGAIN forgeflow-appdb",
        "getaddrinfo ENOTFOUND localhost",
        # Ordinary failures.
        "expect(received).toBe(expected) // Object.is equality\nExpected: 201\nReceived: 400",
        "TypeError: Cannot read properties of undefined (reading 'title')",
        "",
    ],
)
def test_ordinary_and_local_failures_are_left_alone(text: str) -> None:
    assert diagnose_offline(text) is None


def test_matching_is_case_insensitive() -> None:
    diagnosis = diagnose_offline("COULD NOT FIND AN VALID BINARY PATH")

    assert diagnosis is not None
    assert diagnosis.kind is OfflineKind.test_db_unavailable


def test_the_diagnosis_serialises_for_the_audit_trail() -> None:
    diagnosis = diagnose_offline(_HELPER)
    assert diagnosis is not None

    assert diagnosis.to_dict() == {
        "kind": "test_db_unavailable",
        "repairable": False,
        "reason": diagnosis.reason,
        "signal": TEST_DB_UNAVAILABLE_MARKER,
        "hint": diagnosis.hint,
    }
