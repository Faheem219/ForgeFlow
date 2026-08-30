"""The import walk that tells the repair context what a failing test exercises (phase-63).

Pure: no workspace, no model, no DB. Reads arrive through an injected callable, so the whole module
graph is a dict literal here — which is the point of keeping it free of I/O.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from app.agents.import_graph import (
    at_depth,
    deeper_than,
    import_specifiers,
    reachable_sources,
    resolve_specifier,
)

SPEC = "frontend/src/App.test.tsx"
PAGE = "frontend/src/pages/HomePage.tsx"
FORM = "frontend/src/components/TodoForm.tsx"
API = "frontend/src/lib/api.ts"


def _reader(files: dict[str, str]) -> Callable[[str], Awaitable[str | None]]:
    async def read(path: str) -> str | None:
        return files.get(path)

    return read


# --------------------------------------------------------------------- specifiers


def test_every_import_form_is_recognised_in_source_order() -> None:
    source = """
    import React from 'react'
    import { HomePage } from './pages/HomePage'
    import type { Todo } from './types'
    import './index.css'
    export { Layout } from './components/Layout'
    export * from './hooks'
    const mod = await import('./lazy/Chart')
    const legacy = require('./legacy/util')
    """

    assert import_specifiers(source) == [
        "react",
        "./pages/HomePage",
        "./types",
        "./index.css",
        "./components/Layout",
        "./hooks",
        "./lazy/Chart",
        "./legacy/util",
    ]


def test_repeated_specifiers_appear_once() -> None:
    source = "import { a } from './x'\nimport { b } from './x'"

    assert import_specifiers(source) == ["./x"]


def test_a_source_with_no_imports_yields_nothing() -> None:
    assert import_specifiers("export const answer = 42") == []
    assert import_specifiers("") == []


# --------------------------------------------------------------------- resolution


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("./pages/HomePage", PAGE),  # extension probed
        ("./pages/HomePage.tsx", PAGE),  # extension written out
        ("../src/lib/api", API),  # `..` normalised
        ("@/lib/api", API),  # the `@/` alias → <half>/src/…
        ("./lib/api.js", API),  # TS ESM: the emitted extension names a .ts source
        ("react", None),  # a package is never a workspace file
        ("@testing-library/react", None),  # …including a scoped one
        ("../../../../etc/passwd", None),  # never escapes the workspace
        ("./nope", None),  # not in the tree
    ],
)
def test_specifier_resolution(spec: str, expected: str | None) -> None:
    tree = {SPEC, PAGE, FORM, API}

    assert resolve_specifier(SPEC, spec, tree) == expected


def test_a_directory_import_resolves_to_its_index_file() -> None:
    tree = {SPEC, "frontend/src/features/todos/index.ts"}

    assert (
        resolve_specifier(SPEC, "./features/todos", tree) == "frontend/src/features/todos/index.ts"
    )


def test_a_node_modules_path_is_unreachable_because_bare_specifiers_never_resolve() -> None:
    tree = {SPEC, "node_modules/react/index.js"}

    assert resolve_specifier(SPEC, "react", tree) is None


# --------------------------------------------------------------------- the walk


async def test_the_reported_case_the_spec_names_the_component_it_renders() -> None:
    """`App.test.tsx` → `pages/HomePage.tsx` → `TodoForm.tsx`: the fix lives two hops out."""
    files = {
        SPEC: (
            "import { render } from '@testing-library/react'\n"
            "import { HomePage } from './pages/HomePage'"
        ),
        PAGE: "import { TodoForm } from '../components/TodoForm'",
        FORM: "export function TodoForm() { return null }",
    }

    found = await reachable_sources(SPEC, _reader(files), set(files), max_depth=3, max_files=10)

    assert found == [(PAGE, 1), (FORM, 2)]
    assert at_depth(found, 1) == [PAGE]
    assert deeper_than(found, 1) == [FORM]


async def test_the_walk_stops_at_the_depth_cap() -> None:
    files = {
        SPEC: "import { HomePage } from './pages/HomePage'",
        PAGE: "import { TodoForm } from '../components/TodoForm'",
        FORM: "import { api } from '../lib/api'",
        API: "export const api = {}",
    }

    found = await reachable_sources(SPEC, _reader(files), set(files), max_depth=1, max_files=10)

    assert found == [(PAGE, 1)]


async def test_the_walk_stops_at_the_file_cap() -> None:
    files = {
        SPEC: "import './a'\nimport './b'\nimport './c'",
        "frontend/src/a.ts": "",
        "frontend/src/b.ts": "",
        "frontend/src/c.ts": "",
    }

    found = await reachable_sources(SPEC, _reader(files), set(files), max_depth=3, max_files=2)

    assert [path for path, _ in found] == ["frontend/src/a.ts", "frontend/src/b.ts"]


async def test_a_cycle_terminates() -> None:
    files = {
        SPEC: "import './a'",
        "frontend/src/a.ts": "import './b'",
        "frontend/src/b.ts": "import './a'\nimport '../src/App.test'",
    }

    found = await reachable_sources(SPEC, _reader(files), set(files), max_depth=5, max_files=10)

    assert [path for path, _ in found] == ["frontend/src/a.ts", "frontend/src/b.ts"]


async def test_an_unreadable_file_is_skipped_not_raised() -> None:
    files = {SPEC: "import './a'\nimport './b'", "frontend/src/b.ts": "export const b = 1"}
    tree = {SPEC, "frontend/src/a.ts", "frontend/src/b.ts"}  # `a` is listed but cannot be read

    found = await reachable_sources(SPEC, _reader(files), tree, max_depth=3, max_files=10)

    # It is still offered as a candidate — only its *own* imports are lost.
    assert [path for path, _ in found] == ["frontend/src/a.ts", "frontend/src/b.ts"]


async def test_a_test_file_reached_through_an_import_is_still_filtered_downstream() -> None:
    """The walk reports what it reaches; ``is_test_file`` in the widener decides what is admitted.

    Keeping the filter out of the walk is deliberate: a spec that imports a shared spec must not
    lose *its* subject, and the oracle can never become editable regardless (see
    ``test_suite_repair_context``).
    """
    helper = "frontend/src/helpers.test.ts"
    files = {SPEC: "import './helpers.test'", helper: "import './pages/HomePage'"}

    found = await reachable_sources(
        SPEC, _reader(files), {SPEC, helper, PAGE}, max_depth=3, max_files=10
    )

    assert found == [(helper, 1), (PAGE, 2)]


async def test_disabling_the_walk_returns_nothing() -> None:
    files = {SPEC: "import './pages/HomePage'", PAGE: ""}

    assert await reachable_sources(SPEC, _reader(files), set(files), max_depth=0, max_files=9) == []
    assert await reachable_sources(SPEC, _reader(files), set(files), max_depth=3, max_files=0) == []
