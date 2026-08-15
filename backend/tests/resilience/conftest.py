"""Fault-injection harness for the resilience suite (phase-48).

The whole point of this epic is *what happens when things fail*, so the fixtures here are failure
generators: design providers that raise a chosen ``ProviderError`` kind, and exec runtimes whose
processes hang until killed. They live under ``tests/`` — fault injection is never a production path
(design note) — and drive the **real** error paths rather than mocking them away.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from app.core.errors import ProviderError
from app.design.base import (
    DesignCapabilities,
    DesignCode,
    DesignImage,
    DesignResult,
    ProviderHealth,
    provider_error,
)
from app.sandbox.runtime import ExecChunk


class ScriptedDesignProvider:
    """A design provider that succeeds or fails on command, per capability.

    ``fail_text`` / ``fail_image`` are error *kinds* (``"quota"``, ``"auth"``, ``"transient"``,
    ``"fatal"``, …) or ``None`` to succeed. ``from_image`` mirrors real providers (Figma can't do
    screenshots), so the fallback chain's capability filter is exercised honestly.
    """

    def __init__(
        self,
        key: str,
        *,
        fail_text: str | None = None,
        fail_image: str | None = None,
        from_image: bool = True,
        health: ProviderHealth = ProviderHealth.ok,
    ) -> None:
        self.key = key
        self._fail_text = fail_text
        self._fail_image = fail_image
        self._from_image = from_image
        self._health = health
        self.text_calls = 0
        self.image_calls = 0
        #: (design_ref, instruction) per refine — so tests can assert *which* provider served one.
        self.refine_calls: list[tuple[str, str]] = []

    def _boom(self, kind: str) -> ProviderError:
        return provider_error(self.key, f"{self.key} {kind} failure", detail={"kind": kind})

    async def generate_from_text(
        self,
        prompt: str,
        *,
        workspace: str | None = None,
        workspace_title: str | None = None,
    ) -> DesignResult:
        self.text_calls += 1
        if self._fail_text is not None:
            raise self._boom(self._fail_text)
        return DesignResult(
            provider=self.key, external_ref=f"{self.key}-t", html="<h1>ok</h1>", css=""
        )

    async def generate_from_image(
        self,
        images: list[DesignImage],
        prompt: str | None = None,
        *,
        workspace: str | None = None,
        workspace_title: str | None = None,
    ) -> DesignResult:
        self.image_calls += 1
        if self._fail_image is not None:
            raise self._boom(self._fail_image)
        return DesignResult(
            provider=self.key, external_ref=f"{self.key}-i", html="<h1>ok</h1>", css=""
        )

    async def refine(self, design_ref: str, instruction: str) -> DesignResult:
        self.refine_calls.append((design_ref, instruction))
        return DesignResult(
            provider=self.key, external_ref=f"{design_ref}-r", html="<h1>ok</h1>", css=""
        )

    async def fetch_code(self, design_ref: str) -> DesignCode:
        return DesignCode(html="<h1>ok</h1>", css="")

    def capabilities(self) -> DesignCapabilities:
        return DesignCapabilities(
            provider=self.key,
            from_text=True,
            from_image=self._from_image,
            refine=True,
            fetch_code=True,
            max_images=8 if self._from_image else 0,
        )

    async def health(self) -> ProviderHealth:
        return self._health


# --------------------------------------------------------------------- exec fault injection


class HangingExecHandle:
    """An exec that streams one line then blocks until killed — for timeout/cancel tests.

    Pure in-memory (no subprocess, no OS process groups), so the cancellation path is testable on
    every platform, unlike the real pty/killpg-backed runtime.
    """

    def __init__(self) -> None:
        self._released = threading.Event()
        self.killed = False
        self.waited = False

    def stream(self) -> Iterator[ExecChunk]:
        yield ExecChunk(stream="stdout", data=b"working...\n")
        # Block the pump thread until kill() (or an external release) lets it finish.
        self._released.wait(timeout=30)

    def wait(self) -> int:
        self.waited = True
        return 137 if self.killed else 0  # 137 = 128 + SIGKILL, the conventional "killed" code

    def kill(self) -> None:
        self.killed = True
        self._released.set()


class HangingRuntime:
    """A minimal runtime whose ``start_exec`` returns a :class:`HangingExecHandle`."""

    def __init__(self) -> None:
        self.handles: list[HangingExecHandle] = []

    def start_exec(
        self, argv: list[str], cwd: str = "", env: dict[str, str] | None = None
    ) -> HangingExecHandle:
        handle = HangingExecHandle()
        self.handles.append(handle)
        return handle
