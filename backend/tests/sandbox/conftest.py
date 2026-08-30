"""Shared guards for the sandbox suite.

:class:`~app.sandbox.runtime.LocalRuntime` is the tests-only backend that stands in for
:class:`~app.sandbox.runtime.DockerRuntime`. It is deliberately *real*: real processes, real
``git``, a real pty — which makes it POSIX-only (pty, ``fcntl``, process groups, and
forward-slash path semantics inside the workspace guard). The production backend runs on Linux
(Docker via Colima), so on a Windows host these modules have nothing meaningful to assert; they
are skipped rather than left failing, so a red suite still means a real regression.

Modules that exercise the Docker backend through fakes are unaffected and keep running everywhere.
"""

from __future__ import annotations

import sys

import pytest

requires_posix_runtime = pytest.mark.skipif(
    sys.platform == "win32",
    reason="LocalRuntime is POSIX-only (pty, process groups, POSIX path semantics)",
)
