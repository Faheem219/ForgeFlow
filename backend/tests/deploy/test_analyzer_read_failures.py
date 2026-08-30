"""A sandbox that fails to answer is not the same fact as a file that is not there.

``_read_text`` used to funnel every exception into ``None``, which the detection rules then read as
"this project has no frontend". One Docker hiccup on the first read — and the frontend's
``package.json`` is always the first read — was enough to produce a plan with ``fe=None``. The
deploy that followed shipped a backend-only app, recorded a topology with no frontend node, and
reported itself **live**.

Absent and unusable still mean ``None``: those are answers about the file. Anything else is the
workspace failing, and a loud failure is retryable in a way that a silently wrong plan is not.
"""

from __future__ import annotations

import pytest

from app.core.errors import NotFoundError, SystemError, UserError
from app.db.models import Project
from app.deploy.analyzer import InfraAnalyzer
from app.sandbox.schemas import FileContent
from tests.deploy.conftest import FakeAnalyzerWorkspace, fe_be_files, make_project

pytestmark = pytest.mark.usefixtures("mongo_db")

_FE_PKG = "frontend/package.json"


class WorkspaceFailingOn(FakeAnalyzerWorkspace):
    """Serves the fixed-stack skeleton, except one path which raises ``error``."""

    def __init__(self, path: str, error: Exception) -> None:
        super().__init__(fe_be_files(mongoose=True))
        self._path = path
        self._error = error

    async def read(self, project: Project, path: str) -> FileContent:
        if path == self._path:
            raise self._error
        return await super().read(project, path)


async def test_a_sandbox_failure_is_not_reported_as_a_missing_frontend() -> None:
    """The exact production shape: the container is unavailable on the very first read."""
    workspace = WorkspaceFailingOn(_FE_PKG, SystemError("Sandbox container unavailable"))
    project = await make_project()

    with pytest.raises(SystemError):
        await InfraAnalyzer(workspace).analyze(project, persist=False)


async def test_an_unexpected_error_is_not_swallowed_either() -> None:
    """Anything the taxonomy does not classify is still a failure, not an empty answer."""
    workspace = WorkspaceFailingOn(_FE_PKG, RuntimeError("docker: connection reset"))
    project = await make_project()

    with pytest.raises(RuntimeError):
        await InfraAnalyzer(workspace).analyze(project, persist=False)


async def test_a_genuinely_absent_file_still_means_absent() -> None:
    """The guard must not over-trigger: a missing package.json is a real answer, not an error."""
    workspace = WorkspaceFailingOn(_FE_PKG, NotFoundError("File not found"))
    project = await make_project()

    plan = await InfraAnalyzer(workspace).analyze(project, persist=False)

    assert plan.fe is None
    assert any("No frontend detected" in w for w in plan.warnings)
    assert plan.be is not None  # the rest of the workspace still classifies


async def test_an_unreadable_file_also_still_means_absent() -> None:
    """Present but unusable as config (too large, not UTF-8) is a verdict about the file."""
    workspace = WorkspaceFailingOn(_FE_PKG, UserError("File is not UTF-8 text"))
    project = await make_project()

    plan = await InfraAnalyzer(workspace).analyze(project, persist=False)

    assert plan.fe is None
    assert plan.be is not None


async def test_a_healthy_workspace_is_unaffected() -> None:
    workspace = FakeAnalyzerWorkspace(fe_be_files(mongoose=True))
    project = await make_project()

    plan = await InfraAnalyzer(workspace).analyze(project, persist=False)

    assert plan.fe is not None and plan.fe.dir == "frontend"
    assert plan.be is not None and plan.be.dir == "backend"
    assert plan.warnings == []
