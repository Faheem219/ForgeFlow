"""Git-backed workspace helpers (phase-12).

A git history in the sandbox is the anchor for the diff-aware repair loop (D7, §9): the failure
analyzer (phase-29) computes "diff since the last passing run", where *last passing* is a moving
ref updated by the test runner (phase-28). This module owns the primitives; the producers live in
their own phases.
"""

from __future__ import annotations

import asyncio

# SystemError shadows the builtin (taxonomy name fixed by the plan); A004 suppressed on-line.
from app.core.errors import SystemError  # noqa: A004
from app.sandbox.runtime import WorkspaceRuntime

LAST_PASSING_REF = "refs/forgeflow/last-passing"

_GIT_USER_EMAIL = "bot@forgeflow.local"
_GIT_USER_NAME = "ForgeFlow"


class WorkspaceGit:
    def __init__(self, runtime: WorkspaceRuntime) -> None:
        self._rt = runtime

    async def _git(self, *args: str, stdin: bytes | None = None) -> tuple[int, str, str]:
        result = await asyncio.to_thread(self._rt.run_git, list(args), stdin)
        return result.exit_code, result.text(), result.stderr.decode("utf-8", errors="replace")

    async def is_initialized(self) -> bool:
        return await asyncio.to_thread(self._rt.exists, ".git")

    async def ensure_init(self) -> None:
        """Initialize + identity-configure the workspace repo (idempotent)."""
        if await self.is_initialized():
            return
        code, _, err = await self._git("init", "-q")
        if code != 0:
            raise SystemError(f"git init failed: {err.strip()}")
        await self._git("config", "user.email", _GIT_USER_EMAIL)
        await self._git("config", "user.name", _GIT_USER_NAME)
        await self._git("config", "commit.gpgsign", "false")

    async def current_sha(self) -> str | None:
        code, out, _ = await self._git("rev-parse", "HEAD")
        return out.strip() if code == 0 else None

    async def commit(self, message: str) -> tuple[str | None, bool]:
        """Stage everything and commit.

        Returns ``(sha, created)`` where ``created`` is ``True`` only when a new commit was made.
        A no-op (nothing staged) returns the existing HEAD without creating an empty commit.
        """
        await self.ensure_init()
        await self._git("add", "-A")
        _, status, _ = await self._git("status", "--porcelain")
        head = await self.current_sha()
        if not status.strip():
            return head, False  # nothing to commit (None if the repo has no commits yet)
        code, _, err = await self._git("commit", "-m", message)
        if code != 0:
            raise SystemError(f"git commit failed: {err.strip()}")
        return await self.current_sha(), True

    async def diff(self, sha_a: str, sha_b: str, paths: list[str] | None = None) -> str:
        args = ["diff", sha_a, sha_b]
        if paths:
            args += ["--", *paths]
        code, out, err = await self._git(*args)
        if code != 0:
            raise SystemError(f"git diff failed: {err.strip()}")
        return out

    async def set_last_passing(self, sha: str) -> None:
        """Move the ``last-passing`` ref (called by the test runner in phase-28)."""
        code, _, err = await self._git("update-ref", LAST_PASSING_REF, sha)
        if code != 0:
            raise SystemError(f"git update-ref failed: {err.strip()}")

    async def last_passing(self) -> str | None:
        code, out, _ = await self._git("rev-parse", "--verify", "--quiet", LAST_PASSING_REF)
        return out.strip() if code == 0 and out.strip() else None
