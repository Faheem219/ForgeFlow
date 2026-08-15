"""The model's implement loop must not be able to rescaffold itself (phase-54).

`instantiate_skeleton` re-copies the skeleton and overwrites app.ts / routes.tsx, destroying the
routes a build just registered. It is safe as the deterministic step-0 action the agent runs
itself, and unsafe as a mid-build model choice — so it is withheld from the tool list handed to the
implement loop, while remaining resolvable through the full registry for step 0. No DB or model is
needed: this is pure tool-list plumbing.
"""

from __future__ import annotations

from app.agents.codegen import _MODEL_EXCLUDED_TOOLS, CodegenAgent
from app.agents.tools.definitions import default_registry

# The nine tools the implement loop keeps — everything except instantiate_skeleton.
_EXPECTED_MODEL_TOOLS = {
    "read_file",
    "write_file",
    "list_dir",
    "run_command",
    "install_deps",
    "run_tests",
    "start_preview",
    "restart_preview",
    "git_commit",
}


def test_model_tools_exclude_instantiate_skeleton() -> None:
    agent = CodegenAgent(registry=default_registry())
    names = {tool["name"] for tool in agent._model_tools()}

    assert "instantiate_skeleton" not in names
    assert names == _EXPECTED_MODEL_TOOLS


def test_step0_instantiate_skeleton_still_resolves_through_full_registry() -> None:
    # The agent holds the *full* default registry, so its own step-0 dispatch still finds the tool.
    registry = default_registry()
    assert registry.get("instantiate_skeleton") is not None
    assert _MODEL_EXCLUDED_TOOLS == frozenset({"instantiate_skeleton"})


def test_registry_exclude_is_the_only_thing_dropped() -> None:
    registry = default_registry()
    full = {tool["name"] for tool in registry.anthropic_tools()}
    model = {tool["name"] for tool in registry.anthropic_tools(exclude={"instantiate_skeleton"})}

    assert "instantiate_skeleton" in full  # default list is unchanged (phase-21 contract)
    assert full - model == {"instantiate_skeleton"}
