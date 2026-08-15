"""Build-failure classification (phase-55 task 9).

Table-driven over the whole signal corpus. The two load-bearing cases: ``exit_code=0`` with
``container_alive=False`` must be ``environment`` (the null-ExitCode trap — a dead container's exit
code lies), and a ``TS2339`` typecheck line must be ``code`` (a real bug the loop repairs). Only
``code`` is repairable; every env/provider/budget verdict short-circuits before the repair loop.
"""

from __future__ import annotations

import pytest

from app.agents.build_errors import BuildFailureKind, classify_exception, classify_output
from app.core.errors import ProviderError, SystemError, UserError  # noqa: A004 - taxonomy name
from app.sandbox.exec import EXIT_TIMEOUT
from app.sandbox.runtime import EXIT_UNKNOWN

E = BuildFailureKind.environment
C = BuildFailureKind.code


@pytest.mark.parametrize(
    ("text", "kwargs", "expected"),
    [
        # The null-ExitCode trap: exit 0 but the container is dead → environment, never code.
        ("all good", {"exit_code": 0, "container_alive": False}, E),
        ("", {"exit_code": EXIT_UNKNOWN}, E),
        ("oci runtime exec failed: exec failed", {}, E),
        ("<--- JavaScript heap out of memory --->", {}, E),
        ("killed", {"exit_code": 137}, E),
        ("killed", {"exit_code": -9}, E),
        ("Error: spawn ENOMEM", {}, E),
        ("sh: fork: resource temporarily unavailable", {}, E),
        ("ENOSPC: no space left on device, write", {}, E),
        ("npm ERR! getaddrinfo ENOTFOUND registry.npmjs.org", {}, E),
        ("MongooseError: buffering timed out after 10000ms", {}, E),
        ("still starting", {"timed_out": True}, E),
        ("slow", {"exit_code": EXIT_TIMEOUT}, E),
        # A real typecheck error → code (repairable). This is the case that must NOT read as env.
        ("src/features/todos/todos.controller.ts(12,5): error TS2339: Property 'x' ...", {}, C),
        # A healthy container with a plain non-zero exit and no env signature → code.
        ("AssertionError: expected 2 to equal 3", {"exit_code": 1, "container_alive": True}, C),
        ("", {}, C),
    ],
)
def test_classify_output(text: str, kwargs: dict[str, object], expected: BuildFailureKind) -> None:
    diagnosis = classify_output(text, **kwargs)  # type: ignore[arg-type]
    assert diagnosis.kind is expected
    assert diagnosis.repairable is (expected is C)
    if expected is E:
        assert diagnosis.hint  # env verdicts always carry an actionable hint
        assert diagnosis.signal  # …and name the signal that matched


def test_classify_output_is_case_insensitive() -> None:
    assert classify_output("JAVASCRIPT HEAP OUT OF MEMORY").kind is E


def test_classify_exception_maps_taxonomy_to_kinds() -> None:
    assert classify_exception(ProviderError("quota")).kind is BuildFailureKind.provider
    assert classify_exception(UserError("Budget cap reached (₹10 of ₹10)")).kind is (
        BuildFailureKind.budget
    )
    assert classify_exception(SystemError("Sandbox runtime unavailable")).kind is E
    # An unexpected error errs toward code so it still gets a repair attempt.
    assert classify_exception(ValueError("boom")).kind is C


def test_provider_exception_carries_its_fallback_hint() -> None:
    diagnosis = classify_exception(ProviderError("down", fallback_hint="try later"))
    assert diagnosis.kind is BuildFailureKind.provider
    assert diagnosis.hint == "try later"
    assert diagnosis.repairable is False
