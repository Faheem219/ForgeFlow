"""Shared fixtures + fakes for the test-runner suite (phase-28).

The runner's dependencies (exec, workspace, blobs) are injected behind Protocols, so its
orchestration is tested without Docker: a :class:`ScriptedExec` returns per-framework exit codes and
canned stdout, and a :class:`FakeRunnerWorkspace` serves the reporter JSON it reads back.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from beanie import PydanticObjectId

from app.core.config import reset_config
from app.core.errors import NotFoundError
from app.db.blobs import get_blob_store
from app.db.models import Project
from app.sandbox.exec import ExecOutcome
from app.sandbox.schemas import FileContent, GitInfo


@pytest.fixture
def blob_env(monkeypatch: pytest.MonkeyPatch, tmp_path: str) -> Iterator[None]:
    """Filesystem blobs so captured stdout never touches the shared meta DB."""
    monkeypatch.setenv("BLOB_BACKEND", "filesystem")
    monkeypatch.setenv("BLOB_FS_DIR", str(tmp_path))
    reset_config()
    yield


def _framework_of(cmd: list[str]) -> str:
    for name in ("vitest", "jest", "playwright"):
        if name in cmd:
            return name
    return "unknown"


class ScriptedExec:
    """Fake ExecService: exit code + canned stdout per framework; records calls."""

    def __init__(
        self,
        exit_codes: dict[str, int] | None = None,
        stdout: dict[str, bytes] | None = None,
    ) -> None:
        self.exit_codes = exit_codes or {}
        self.stdout = stdout or {}
        self.calls: list[dict[str, object]] = []

    async def run(
        self,
        project: Project,
        cmd: list[str],
        cwd: str = "",
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture: bool = True,
    ) -> ExecOutcome:
        framework = _framework_of(cmd)
        self.calls.append({"framework": framework, "cmd": cmd, "cwd": cwd, "env": env})
        ref = None
        data = self.stdout.get(framework, b"")
        if data:
            ref = await get_blob_store().put(data)
        return ExecOutcome(
            exec_id="e1",
            exit_code=self.exit_codes.get(framework, 0),
            duration_s=0.0,
            timed_out=False,
            cancelled=False,
            output_ref=ref,
        )


class FakeRunnerWorkspace:
    """In-memory workspace: read() serves seeded reporter JSON; tracks the last-passing ref."""

    def __init__(self, files: dict[str, str], current_sha: str | None = "sha-head") -> None:
        self.files = files
        self.current_sha = current_sha
        self.last_passing: str | None = None

    async def read(self, project: Project, path: str) -> FileContent:
        if path not in self.files:
            raise NotFoundError(f"File not found: {path}")
        content = self.files[path]
        return FileContent(path=path, content=content, size=len(content.encode("utf-8")))

    async def git_info(self, project: Project) -> GitInfo:
        return GitInfo(current_sha=self.current_sha, last_passing=self.last_passing)

    async def set_last_passing(self, project: Project, sha: str) -> None:
        self.last_passing = sha


class RecordingEmitter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    async def __call__(
        self, channel: str, event_type: object, payload: dict[str, object], stage: object = None
    ) -> None:
        self.events.append((str(event_type), payload))


async def make_project(name: str = "app") -> Project:
    return await Project(user_id=PydanticObjectId(), name=name, app_db_name="db").insert()


# --------------------------------------------------------------------- reporter JSON fixtures


def jest_report(*, failing: bool = True) -> str:
    assertions = [
        {
            "ancestorTitles": ["todos"],
            "title": "[ac-add] adds a todo",
            "fullName": "todos [ac-add] adds a todo",
            "status": "passed",
            "duration": 5,
            "failureMessages": [],
        }
    ]
    if failing:
        assertions.append(
            {
                "ancestorTitles": ["todos"],
                "title": "[ac-empty] rejects an empty title",
                "fullName": "todos [ac-empty] rejects an empty title",
                "status": "failed",
                "duration": 3,
                "failureMessages": [
                    "Error: expect(received).toBe(expected)\n"
                    "    at Object.<anonymous> "
                    "(/workspace/backend/src/features/todos/todos.controller.ts:22:18)"
                ],
            }
        )
    return json.dumps(
        {
            "numTotalTests": len(assertions),
            "success": not failing,
            "testResults": [
                {
                    "name": "/workspace/backend/src/features/todos/todos.test.ts",
                    "assertionResults": assertions,
                }
            ],
        }
    )


def vitest_report() -> str:
    return json.dumps(
        {
            "numTotalTests": 1,
            "testResults": [
                {
                    "name": "src/features/counter/Counter.test.tsx",
                    "assertionResults": [
                        {
                            "title": "[ac-inc] increments the counter",
                            "fullName": "Counter [ac-inc] increments the counter",
                            "status": "passed",
                            "duration": 12,
                            "failureMessages": [],
                        }
                    ],
                }
            ],
        }
    )


def playwright_report(*, failing: bool = True) -> str:
    specs = [
        {
            "title": "[ac-list] shows todos",
            "file": "e2e/todos.spec.ts",
            "tests": [{"results": [{"status": "passed", "duration": 120}]}],
            "ok": True,
        }
    ]
    if failing:
        specs.append(
            {
                "title": "[ac-uiadd] adds a todo via the UI",
                "file": "e2e/todos.spec.ts",
                "tests": [
                    {
                        "results": [
                            {
                                "status": "failed",
                                "duration": 300,
                                "error": {
                                    "message": "Error: expect(locator).toBeVisible() failed",
                                    "stack": "at e2e/todos.spec.ts:8:20",
                                },
                            }
                        ]
                    }
                ],
                "ok": False,
            }
        )
    return json.dumps(
        {
            "suites": [{"title": "todos.spec.ts", "file": "e2e/todos.spec.ts", "specs": specs}],
            "stats": {"expected": 1, "unexpected": 1 if failing else 0, "skipped": 0},
        }
    )
