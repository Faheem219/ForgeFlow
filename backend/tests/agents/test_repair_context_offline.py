"""The repair context carries what the sandbox's missing network explains (phase-65).

An unrepairable finding (no ``mongod`` for the test database) becomes ``environment`` — the loop
stops on it before any attempt. Repairable ones (a test calling the internet) become hints the
agent is shown. Ordinary failures get neither, so their contexts are exactly what they were.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.prompts.repair import repair_user_prompt
from app.agents.repair_context import RepairContext, RepairContextAnalyzer
from app.sandbox.offline import OfflineKind
from tests.agents.repair_fakes import (
    FakeContextWorkspace,
    configure_blobs,
    failing_result,
    make_project,
    make_test_run,
)

pytestmark = pytest.mark.usefixtures("mongo_db")

TEST_FILE = "backend/src/features/payments/payments.test.ts"
SOURCE_FILE = "backend/src/features/payments/payments.service.ts"
DB_UNAVAILABLE = (
    "[forgeflow:test-db-unavailable] The in-memory MongoDB could not start: MongoBinary.getPath: "
    "could not find an valid binary path!"
)
STRIPE_DOWN = "FetchError: request failed, reason: getaddrinfo ENOTFOUND api.stripe.com"


def _workspace() -> FakeContextWorkspace:
    return FakeContextWorkspace(
        {TEST_FILE: "it('[ac-pay] charges', …)", SOURCE_FILE: "export async function charge() {…}"}
    )


async def test_a_missing_test_database_is_recorded_as_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    run = await make_test_run(
        project,
        [
            failing_result("payments [ac-pay] charges", file=TEST_FILE, message=DB_UNAVAILABLE),
            failing_result("payments [ac-refund] refunds", file=TEST_FILE, message=DB_UNAVAILABLE),
        ],
    )

    context = await RepairContextAnalyzer(_workspace()).analyze(project, run)

    assert context.environment is not None
    assert context.environment.kind is OfflineKind.test_db_unavailable
    # Unrepairable findings are not hints: nothing the agent writes can supply a binary.
    assert context.sandbox_hints == []
    # …and they reach the persisted audit trail.
    assert context.to_dict()["environment"]["kind"] == "test_db_unavailable"


async def test_an_internet_call_becomes_one_hint_naming_the_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    run = await make_test_run(
        project,
        [
            failing_result("payments [ac-pay] charges", file=TEST_FILE, stack=STRIPE_DOWN),
            failing_result("payments [ac-refund] refunds", file=TEST_FILE, stack=STRIPE_DOWN),
        ],
    )

    context = await RepairContextAnalyzer(_workspace()).analyze(project, run)

    assert context.environment is None
    # Two failures, one cause, one sentence.
    assert len(context.sandbox_hints) == 1
    assert "`api.stripe.com`" in context.sandbox_hints[0]
    assert context.to_dict()["sandbox_hints"] == context.sandbox_hints


async def test_an_ordinary_failure_gets_neither(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure_blobs(monkeypatch, tmp_path)
    project = await make_project()
    run = await make_test_run(
        project, [failing_result("payments [ac-pay] charges", file=TEST_FILE)]
    )

    context = await RepairContextAnalyzer(_workspace()).analyze(project, run)

    assert context.environment is None
    assert context.sandbox_hints == []
    assert context.to_dict()["environment"] is None


def test_the_prompt_names_the_sandbox_only_when_there_is_something_to_say() -> None:
    plain = RepairContext()
    hinted = RepairContext(sandbox_hints=["The sandbox has no internet: mock `api.stripe.com`."])

    without = repair_user_prompt(plain, set(), 1)
    with_hint = repair_user_prompt(hinted, set(), 1)

    assert "## Sandbox environment" not in without
    assert "## Sandbox environment" in with_hint
    assert "mock `api.stripe.com`" in with_hint
    # Right after the failures it explains, before the rest of the context.
    assert with_hint.index("## Failing tests") < with_hint.index("## Sandbox environment")
    assert with_hint.index("## Sandbox environment") < with_hint.index("## Acceptance criteria")
    # Everything else is untouched: removing the section gives back the plain prompt.
    section = "## Sandbox environment\n- The sandbox has no internet: mock `api.stripe.com`.\n\n"
    assert with_hint.replace(section, "") == without
