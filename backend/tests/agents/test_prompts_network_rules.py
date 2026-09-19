"""Every sandbox agent is told the network rules; test authors are told how to get a DB (phase-65).

A model that does not know the sandbox is offline learns it by failing — the reported session spent
its repair attempts concluding it was "unable to download mongod due to no network access". These
pin the sentences that prevent that, where each agent actually reads them.
"""

from __future__ import annotations

from app.agents.prompts import BASE_SYSTEM, PROMPT_VERSION, SANDBOX_NETWORK_RULES, system_prompt
from app.agents.prompts.codegen import CODEGEN_SYSTEM_EXTRA, PHASE_IMPLEMENT_SYSTEM_EXTRA
from app.agents.prompts.repair import REPAIR_SYSTEM_EXTRA
from app.agents.prompts.testgen import TESTGEN_SYSTEM_EXTRA


def test_every_agent_prompt_carries_the_network_rules() -> None:
    assert SANDBOX_NETWORK_RULES in BASE_SYSTEM
    for extra in (PHASE_IMPLEMENT_SYSTEM_EXTRA, TESTGEN_SYSTEM_EXTRA, REPAIR_SYSTEM_EXTRA):
        assert SANDBOX_NETWORK_RULES in system_prompt(extra)


def test_the_rules_say_what_is_offline_and_what_is_already_inside() -> None:
    rules = SANDBOX_NETWORK_RULES
    assert "NO internet" in rules
    assert "`install_deps`" in rules  # the one thing that does reach the registry
    assert "downloads at run time" in rules
    assert "MongoDB server binary" in rules and "`mongodb-memory-server`" in rules
    assert "Playwright" in rules


def test_test_authors_must_use_the_real_test_database_for_data() -> None:
    prompt = TESTGEN_SYSTEM_EXTRA
    assert "`useTestDb()`" in prompt
    assert "import { useTestDb } from '../../test/db'" in prompt
    assert "Do NOT mock Mongoose" in prompt
    assert "do NOT connect to `MONGODB_URI`" in prompt
    # The phase-27 wording that let every data test mock the database is gone.
    assert "do not require a live DB" not in prompt


def test_test_authors_have_a_safe_fallback_for_workspaces_without_the_helper() -> None:
    """The test author has no install tool, so it must not be told to add a database library."""
    assert "predates the helper" in TESTGEN_SYSTEM_EXTRA
    assert "do not install" in TESTGEN_SYSTEM_EXTRA


def test_codegen_treats_the_helper_as_scaffold() -> None:
    assert "`backend/src/test/`" in CODEGEN_SYSTEM_EXTRA
    assert "`useTestDb()`" in CODEGEN_SYSTEM_EXTRA


def test_the_prompt_version_was_bumped() -> None:
    """Eval runs record it, so a prompt change must be attributable."""
    assert PROMPT_VERSION >= "2026-09-19.2"
