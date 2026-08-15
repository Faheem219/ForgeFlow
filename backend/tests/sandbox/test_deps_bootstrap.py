"""A workspace must have its dependencies before anything can run there.

The skeleton is copied in as source only, so a preview started on a fresh workspace has no vite and
no express to run. This is the deterministic guard for that: install when the markers are missing,
never install twice, and never turn a failed install into an exception the preview can't explain.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from beanie import PydanticObjectId

from app.db.models import Project
from app.sandbox.deps import INSTALL_CMD, dependencies_installed, ensure_dependencies
from app.sandbox.exec import ExecOutcome
from app.sandbox.runtime import LocalRuntime

pytestmark = pytest.mark.usefixtures("mongo_db")


class FakeExec:
    def __init__(self, exit_code: int = 0) -> None:
        self.calls: list[tuple[list[str], float | None]] = []
        self._exit_code = exit_code

    async def run(
        self,
        project: Project,
        cmd: list[str],
        cwd: str = "",
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture: bool = True,
    ) -> ExecOutcome:
        self.calls.append((cmd, timeout))
        return ExecOutcome(
            exec_id="e1",
            exit_code=self._exit_code,
            duration_s=0.1,
            timed_out=False,
            cancelled=False,
        )


async def _project() -> Project:
    return await Project(user_id=PydanticObjectId(), name="app", app_db_name="db").insert()


def _skeleton(root: Path, *, installed: bool) -> LocalRuntime:
    (root / "package.json").write_text("{}", encoding="utf-8")
    if installed:
        (root / "node_modules").mkdir()
        (root / "node_modules" / ".modules.yaml").write_text("", encoding="utf-8")
        for pkg in ("frontend", "backend"):
            (root / pkg / "node_modules").mkdir(parents=True)
    return LocalRuntime(str(root))


async def test_a_fresh_workspace_is_installed(tmp_path: Path) -> None:
    runtime = _skeleton(tmp_path, installed=False)
    exec_service = FakeExec()

    outcome = await ensure_dependencies(await _project(), runtime, exec_service=exec_service)

    # Returns the install's ExecOutcome so the caller can classify it (phase-55), not just a bool.
    assert outcome is not None and outcome.exit_code == 0
    cmd, timeout = exec_service.calls[0]
    assert cmd == INSTALL_CMD
    # Installs get the longer wall clock; the default per-command one would kill a cold install.
    assert timeout is not None and timeout > 600


async def test_an_installed_workspace_is_left_alone(tmp_path: Path) -> None:
    runtime = _skeleton(tmp_path, installed=True)
    exec_service = FakeExec()

    assert await dependencies_installed(runtime) is True
    assert await ensure_dependencies(await _project(), runtime, exec_service=exec_service) is None
    assert exec_service.calls == []


@pytest.mark.parametrize("missing", ["node_modules/.modules.yaml", "frontend/node_modules"])
async def test_a_partial_install_is_completed(tmp_path: Path, missing: str) -> None:
    """A half-installed workspace (interrupted install, added package dir) must be repaired."""
    runtime = _skeleton(tmp_path, installed=True)
    target = tmp_path / missing
    target.unlink() if target.is_file() else target.rmdir()

    exec_service = FakeExec()
    outcome = await ensure_dependencies(await _project(), runtime, exec_service=exec_service)
    assert outcome is not None


async def test_an_uninstantiated_workspace_is_not_an_install(tmp_path: Path) -> None:
    runtime = LocalRuntime(str(tmp_path))  # no skeleton at all
    exec_service = FakeExec()

    assert await ensure_dependencies(await _project(), runtime, exec_service=exec_service) is None
    assert exec_service.calls == []


async def test_a_failed_install_is_reported_not_raised(tmp_path: Path) -> None:
    runtime = _skeleton(tmp_path, installed=False)
    exec_service = FakeExec(exit_code=1)

    # The dev server that follows fails with the real reason; this must not mask it with its own.
    # The failing outcome is returned (not raised) so the caller can classify it (phase-55).
    outcome = await ensure_dependencies(await _project(), runtime, exec_service=exec_service)
    assert outcome is not None and outcome.exit_code == 1


async def test_force_reinstalls_an_installed_workspace(tmp_path: Path) -> None:
    runtime = _skeleton(tmp_path, installed=True)
    exec_service = FakeExec()

    outcome = await ensure_dependencies(
        await _project(), runtime, exec_service=exec_service, force=True
    )
    assert outcome is not None
    assert len(exec_service.calls) == 1
