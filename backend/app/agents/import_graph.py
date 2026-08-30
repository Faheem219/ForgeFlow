"""What a failing test actually exercises: its import graph (phase-63).

The repair context (phase-29) is built from the paths a failure's *stack* names, and the Test-stage
widener (:mod:`app.agents.suite_context`) tops that up with the sources sitting **beside** the
spec. Directory adjacency is a proxy for "the code under test" that holds on the generated backend
(``todos.test.ts`` beside ``todos.controller.ts``) and fails on the generated frontend, where the
skeleton deliberately separates the shell from the features: ``frontend/src/App.test.tsx`` renders
a page that lives in ``frontend/src/pages/``, so the only neighbours are ``main.tsx`` and
``routes.tsx`` — and the repair agent is handed a file that cannot possibly contain the bug.

The evidence naming the right file is in the spec itself: ``import { HomePage } from
'./pages/HomePage'``. This module reads it. Given a spec and a workspace listing it walks the
module graph breadth-first and returns the workspace sources the test reaches, nearest first.

It is pure path arithmetic over text — no I/O of its own (reads arrive through an injected
callable), no model call, no framework knowledge — which is what makes it exhaustively testable and
keeps the walk *narrowing*: bare specifiers (``react``, ``@testing-library/react``) resolve to
nothing, so ``node_modules`` can never enter a repair context, and the walk is bounded by an
explicit depth and file cap before the phase-29 char budget even applies.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Awaitable, Callable

#: Extensions the fixed generated stack (D2) compiles. Order matters: candidates are probed in it.
SOURCE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts")

#: Compiled-output extensions a TypeScript ESM specifier may carry for a source that is ``.ts``
#: (``import './db.js'`` in a ``.ts`` file). Probed against the TS siblings when the literal misses.
_JS_EXTS = (".js", ".jsx", ".mjs", ".cjs")
_TS_SIBLINGS = (".ts", ".tsx", ".mts", ".cts")

#: One pass over the source, in source order:
#:   1. ``import … from 'x'`` / ``export … from 'x'`` (covers ``import type``, ``export *``)
#:   2. ``import 'x'`` (side-effect import)
#:   3. ``import('x')`` / ``require('x')``
#: Regex rather than a parser: the specifier is a string literal in every form that matters, and a
#: missed exotic form degrades to the pre-phase-63 behaviour instead of breaking anything.
_SPECIFIER_RE = re.compile(
    r"""\bfrom\s*['"]([^'"\n]+)['"]"""
    r"""|\bimport\s+['"]([^'"\n]+)['"]"""
    r"""|\b(?:import|require)\s*\(\s*['"]([^'"\n]+)['"]"""
)

#: How the app-skeleton's two halves are rooted, for ``@/…`` alias resolution.
_ALIAS_PREFIX = "@/"

# The read seam: ``path -> contents`` (``None`` when unreadable). The caller owns caching, so a
# file read during the walk is not read again when it is admitted to the context.
ReadFile = Callable[[str], Awaitable[str | None]]


def import_specifiers(source: str) -> list[str]:
    """Every module specifier in a TS/JS source, in source order, deduplicated."""
    out: list[str] = []
    for match in _SPECIFIER_RE.finditer(source or ""):
        spec = next((group for group in match.groups() if group), "").strip()
        if spec and spec not in out:
            out.append(spec)
    return out


def resolve_specifier(from_path: str, spec: str, tree: set[str]) -> str | None:
    """Resolve ``spec`` (as written in ``from_path``) to a workspace path, or ``None``.

    Only *workspace-local* specifiers resolve: relative (``./``, ``../``) and the ``@/`` alias.
    A bare specifier is a package — returning ``None`` for it is what keeps ``node_modules`` out of
    every repair context, structurally rather than by filtering afterwards.
    """
    base = _base_path(from_path, spec)
    if base is None:
        return None
    for candidate in _candidates(base):
        if candidate in tree:
            return candidate
    return None


async def reachable_sources(
    entry: str,
    read: ReadFile,
    tree: set[str],
    *,
    max_depth: int,
    max_files: int,
) -> list[tuple[str, int]]:
    """Workspace sources reachable from ``entry``, breadth-first, as ``(path, depth)``.

    Depth 1 is "imported by the spec itself" — the strongest signal after the same-stem sibling, so
    the caller can rank direct imports above directory neighbours and transitive ones below them.
    Bounded twice (``max_depth``, ``max_files``); an unreadable file is skipped, never raised, so a
    flaky workspace degrades the context instead of sinking the analysis.
    """
    if max_depth <= 0 or max_files <= 0:
        return []

    found: list[tuple[str, int]] = []
    seen = {entry}
    frontier = [entry]

    for depth in range(1, max_depth + 1):
        next_frontier: list[str] = []
        for path in frontier:
            source = await read(path)
            if not source:
                continue
            for spec in import_specifiers(source):
                resolved = resolve_specifier(path, spec, tree)
                if resolved is None or resolved in seen:
                    continue
                seen.add(resolved)
                found.append((resolved, depth))
                if len(found) >= max_files:
                    return found
                next_frontier.append(resolved)
        if not next_frontier:
            break
        frontier = next_frontier
    return found


def at_depth(reachable: list[tuple[str, int]], depth: int) -> list[str]:
    """The paths found at exactly ``depth`` (1 = directly imported by the spec)."""
    return [path for path, found_at in reachable if found_at == depth]


def deeper_than(reachable: list[tuple[str, int]], depth: int) -> list[str]:
    """The paths found beyond ``depth``, still in breadth-first order."""
    return [path for path, found_at in reachable if found_at > depth]


# --------------------------------------------------------------------- pure helpers


def _base_path(from_path: str, spec: str) -> str | None:
    """The workspace path a specifier points at (extension optional), or ``None`` if it escapes."""
    spec = spec.rstrip("/")
    if not spec:
        return None
    if spec.startswith((".", "./", "../")):
        base = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec))
    elif spec.startswith(_ALIAS_PREFIX):
        # `@/x` is conventionally `<half>/src/x` — the app-skeleton's own layout.
        half = from_path.split("/", 1)[0]
        base = posixpath.normpath(f"{half}/src/{spec[len(_ALIAS_PREFIX) :]}")
    else:
        return None  # a bare specifier is a package, never a workspace file
    # `../../..` out of the workspace root is not a file we could ever patch.
    return None if base.startswith("..") or base in (".", "") else base


def _candidates(base: str) -> list[str]:
    """Every workspace path ``base`` could name, best first (literal → extension → ``index``)."""
    stem, ext = posixpath.splitext(base)
    out: list[str] = []
    if ext in SOURCE_EXTS:
        out.append(base)
    if ext in _JS_EXTS:
        # TypeScript ESM writes the *emitted* extension: `./db.js` means `./db.ts` in source.
        out.extend(f"{stem}{sibling}" for sibling in _TS_SIBLINGS)
    out.extend(f"{base}{source_ext}" for source_ext in SOURCE_EXTS)
    out.extend(f"{base}/index{source_ext}" for source_ext in SOURCE_EXTS)
    seen: list[str] = []
    for candidate in out:
        if candidate not in seen:
            seen.append(candidate)
    return seen


__all__ = [
    "SOURCE_EXTS",
    "ReadFile",
    "at_depth",
    "deeper_than",
    "import_specifiers",
    "reachable_sources",
    "resolve_specifier",
]
