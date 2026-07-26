"""Bounded registry-egress windows for the sandbox.

A sandbox runs on its own ``internal`` network (phase-11) and, during preview, additionally on the
shared ``internal`` preview network (phase-15) — neither can reach the npm registry. But the
generated app's dependencies are *not* baked into the image, so ``pnpm install`` has to reach it
exactly once per new dependency set.
That gap was flagged as unanswered in ``plans/phase-15-live-preview.md``; this module is the answer.

The shape is deliberately the same one live validation already uses (phase-39): borrow the routed
``egress`` network for the duration of one command, then always hand it back. Two properties make
that safe to rely on:

- **Narrow in scope** — only commands that genuinely need the registry (:func:`needs_registry`)
  open a window; everything else keeps the no-network default.
- **Narrow in time** — the window is a context manager whose ``finally`` detaches, and windows are
  ref-counted per project so overlapping installs (up to ``sandbox_max_concurrent_execs``) cannot
  yank the route out from under each other.

It is still never the *host* network: the sandbox gets a routed bridge, not the host namespace.
Flagged for the phase-47 hardening review alongside the other two network relaxations.

Known limitation: while the sandbox is also attached to the *internal* preview network, the route
docker picks for outbound traffic is not guaranteed to be the egress one, so an install can still
fail to resolve. The pipeline avoids it by installing before the preview network is attached (see
:meth:`app.sandbox.preview.PreviewService.start`); a mid-preview install may need a preview restart.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

from app.core.config import get_config

logger = logging.getLogger(__name__)

#: Package managers whose *fetching* subcommands need the registry.
_MANAGERS = frozenset({"pnpm", "npm", "yarn", "corepack"})

#: Subcommands of the above that resolve/download packages.
_FETCHING = frozenset(
    {
        "add",
        "ci",
        "dedupe",
        "dlx",
        "fetch",
        "i",
        "import",
        "install",
        "link",
        "prepare",
        "rebuild",
        "up",
        "update",
        "upgrade",
    }
)

#: Commands that fetch on their own account, whatever their arguments.
_ALWAYS = frozenset({"npx", "pnpx"})

#: Flags pnpm/npm accept *before* the subcommand (``pnpm -r install``, ``pnpm --filter x add y``).
_PRE_SUBCOMMAND_FLAGS_WITH_VALUE = frozenset({"--filter", "-C", "--dir", "--workspace-root"})


class _EgressManager(Protocol):
    """The slice of the sandbox manager a window needs (tests inject a double)."""

    async def attach_egress_network(self, project_id: str) -> bool: ...

    async def detach_egress_network(self, project_id: str) -> None: ...


def needs_registry(argv: list[str]) -> bool:
    """Whether this command has to reach the npm registry to succeed.

    Conservative by design: an unrecognised command gets no network. A false negative is a clear
    ``ENOTFOUND`` in the user's terminal; a false positive would hand generated code the internet.
    """
    if not argv:
        return False
    program = argv[0].rsplit("/", 1)[-1]
    if program in _ALWAYS:
        return True
    if program not in _MANAGERS:
        return False
    skip_next = False
    for token in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if token in _PRE_SUBCOMMAND_FLAGS_WITH_VALUE:
            skip_next = True
            continue
        if token.startswith("-"):
            continue  # a flag, not the subcommand
        return token in _FETCHING
    # Bare `yarn` is an alias for `yarn install`; bare pnpm/npm/corepack just print help.
    return program == "yarn"


def egress_enabled() -> bool:
    return bool(get_config().get("sandbox_install_network"))


# project_id → number of open windows. Guarded by ``_lock``; a window only attaches when it is the
# first and only detaches when it is the last, so concurrent installs share one route.
_holders: dict[str, int] = {}
_lock = asyncio.Lock()


def open_windows(project_id: str) -> int:
    """How many egress windows are currently open for a project (diagnostics/tests)."""
    return _holders.get(project_id, 0)


def _manager() -> Any:
    from app.sandbox.manager import get_manager

    return get_manager()


@asynccontextmanager
async def egress_window(
    project_id: str, *, manager: _EgressManager | None = None
) -> AsyncIterator[bool]:
    """Hold the routed network for the duration of the block. Yields whether it was attached.

    Attach failures are logged, not raised: the command still runs and fails with a registry error
    the user can read, which beats replacing it with a docker traceback.
    """
    sandbox = manager if manager is not None else _manager()
    async with _lock:
        first = _holders.get(project_id, 0) == 0
        _holders[project_id] = _holders.get(project_id, 0) + 1

    attached = False
    try:
        if first:
            try:
                attached = await sandbox.attach_egress_network(project_id)
            except Exception:  # a broken attach must not swallow the command itself
                logger.warning("could not grant egress to sandbox %s", project_id, exc_info=True)
        yield attached
    finally:
        async with _lock:
            remaining = _holders.get(project_id, 1) - 1
            last = remaining <= 0
            if last:
                _holders.pop(project_id, None)
            else:
                _holders[project_id] = remaining
        if last:
            # Always hand the route back — including on timeout/cancel/crash. The sandbox must not
            # keep internet access past the command that needed it.
            try:
                await sandbox.detach_egress_network(project_id)
            except Exception:
                logger.warning("could not revoke egress from sandbox %s", project_id, exc_info=True)


def reset_windows() -> None:
    """Drop the ref-count table (test helper; also correct after a manager swap)."""
    _holders.clear()


__all__ = [
    "egress_enabled",
    "egress_window",
    "needs_registry",
    "open_windows",
    "reset_windows",
]
