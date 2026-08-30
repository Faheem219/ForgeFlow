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

    async def changed_paths(self, sha_a: str, sha_b: str) -> list[str]:
        """Workspace-relative paths that differ between two refs (``git diff --name-only``).

        Mirrors :meth:`diff`; used by the build-repair widening analyzer (phase-55) to seed an
        empty editable set from "what changed since it last worked".
        """
        code, out, err = await self._git("diff", "--name-only", sha_a, sha_b)
        if code != 0:
            raise SystemError(f"git diff --name-only failed: {err.strip()}")
        return [line.strip() for line in out.splitlines() if line.strip()]

    async def restore(self, sha: str) -> bool:
        """Reset the workspace tree back to ``sha``, discarding everything after it.

        Used by the repair loop to undo an attempt that made things **strictly worse** (§9): the
        loop is allowed to spend iterations, never to hand back an app more broken than it found.
        A hard reset is right here and nowhere else — this history is a per-project scratch anchor
        for diff-aware repair, not a shared branch, and the discarded attempt survives in its
        ``RepairAttempt.diff_ref`` blob, so the audit trail is not what is being thrown away.

        Returns whether the reset succeeded; a git failure is reported, never raised, so a botched
        undo cannot turn a recoverable escalation into a 500.
        """
        code, _, _ = await self._git("reset", "--hard", sha)
        return code == 0

    async def set_last_passing(self, sha: str) -> None:
        """Move the ``last-passing`` ref (called by the test runner in phase-28)."""
        code, _, err = await self._git("update-ref", LAST_PASSING_REF, sha)
        if code != 0:
            raise SystemError(f"git update-ref failed: {err.strip()}")

    async def last_passing(self) -> str | None:
        code, out, _ = await self._git("rev-parse", "--verify", "--quiet", LAST_PASSING_REF)
        return out.strip() if code == 0 and out.strip() else None
