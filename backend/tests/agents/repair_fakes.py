"""Shared fakes for the phase-29 repair-context tests.

The analyzer's only I/O seam is a workspace (read + git_info + diff), so a seeded in-memory
workspace makes the whole assembly deterministic and Docker-free.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.core.errors import NotFoundError
from app.db.models import Project, TestRun
from app.sandbox.schemas import FileContent, GitInfo
from app.testing.models import Failure, TestResult, TestStatus


def configure_blobs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Filesystem blobs so the persisted context never touches the shared meta DB."""
    monkeypatch.setenv("BLOB_BACKEND", "filesystem")
    monkeypatch.setenv("BLOB_FS_DIR", str(tmp_path / "blobs"))
    reset_config()


class FakeContextWorkspace:
    """In-memory workspace: seeded files + a canned diff; records how diff() was called."""

    def __init__(
        self,
        files: dict[str, str],
        *,
        current_sha: str | None = "sha-head",
        last_passing: str | None = "sha-green",
        diff_text: str = "",
    ) -> None:
        self.files = files
        self.current_sha = current_sha
        self.last_passing = last_passing
        self.diff_text = diff_text
        self.diff_calls: list[dict[str, Any]] = []

    async def read(self, project: Project, path: str) -> FileContent:
        if path not in self.files:
            raise NotFoundError(f"File not found: {path}")
        content = self.files[path]
        return FileContent(path=path, content=content, size=len(content.encode("utf-8")))

    async def git_info(self, project: Project) -> GitInfo:
        return GitInfo(current_sha=self.current_sha, last_passing=self.last_passing)

    async def diff(
        self, project: Project, sha_a: str, sha_b: str, paths: list[str] | None = None
    ) -> str:
        self.diff_calls.append({"a": sha_a, "b": sha_b, "paths": list(paths or [])})
        return self.diff_text


def failing_result(
    name: str,
    *,
    criterion_id: str | None = None,
    file: str | None = None,
    refs: list[str] | None = None,
    message: str = "expected true to be false",
    stack: str | None = None,
    framework: str = "jest",
) -> TestResult:
    return TestResult(
        name=name,
        status=TestStatus.failed,
        framework=framework,
        criterion_id=criterion_id,
        file=file,
        failure=Failure(
            message=message,
            assertion=name,
            stack=stack,
            files_referenced=list(refs or []),
        ),
    )


def passing_result(name: str, *, file: str | None = None) -> TestResult:
    return TestResult(name=name, status=TestStatus.passed, framework="jest", file=file)


async def make_project(name: str = "app") -> Project:
    return await Project(user_id=PydanticObjectId(), name=name, app_db_name="db").insert()


async def make_test_run(project: Project, results: list[TestResult]) -> TestRun:
    assert project.id is not None
    return await TestRun(
        project_id=project.id,
        results=[r.model_dump(mode="json") for r in results],
        failures=[r.model_dump(mode="json") for r in results if r.status is TestStatus.failed],
    ).insert()
