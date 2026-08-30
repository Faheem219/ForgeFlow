"""The production frontend builder must read the exit code of the build it just ran.

It used to discard the ``ExecOutcome`` of both ``pnpm install`` and ``pnpm build`` and judge the
result purely by whether the output directory had files in it. A failing build therefore reached
the upload step as an empty ``dist/`` and was reported as "Frontend build produced no output to
deploy" — a description of the symptom that hid the compiler error, with the real output already
captured in the blob store and never shown.

These drive the real ``_WorkspaceFrontendBuilder`` through its two seams, so the ordering and the
error handling are exercised without a Docker daemon or a Node toolchain.
"""

from __future__ import annotations

import pytest

from app.core.errors import NotFoundError, UserError
from app.db.blobs import get_blob_store
from app.db.models import Project
from app.deploy.analyzer import FrontendPlan
from app.deploy.orchestrate import _WorkspaceFrontendBuilder
from app.sandbox.exec import ExecOutcome
from app.sandbox.schemas import FileContent, FileNode
from tests.deploy.conftest import make_project

pytestmark = pytest.mark.usefixtures("mongo_db", "blob_env")

_TSC_ERROR = b"src/App.tsx(12,7): error TS2322: Type 'number' is not assignable to type 'string'.\n"

#: A plausible built SPA, as the workspace would hand it back after `vite build`.
_BUNDLE = {
    "frontend/dist/index.html": "<!doctype html><div id=root></div>",
    "frontend/dist/assets/index-abc.js": "console.log(1)",
}


def _ok() -> ExecOutcome:
    return ExecOutcome(
        exec_id="x", exit_code=0, duration_s=1.0, timed_out=False, cancelled=False, output_ref=None
    )


def _failed(exit_code: int, ref: str | None = None, *, timed_out: bool = False) -> ExecOutcome:
    return ExecOutcome(
        exec_id="x",
        exit_code=exit_code,
        duration_s=1.0,
        timed_out=timed_out,
        cancelled=False,
        output_ref=ref,
    )


class ScriptedExec:
    """Hands back a pre-scripted outcome per call and records what it was asked to run."""

    def __init__(self, outcomes: list[ExecOutcome]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[list[str], str, dict[str, str]]] = []

    async def run(
        self,
        project: Project,
        cmd: list[str],
        cwd: str = "",
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        capture: bool = True,
    ) -> ExecOutcome:
        self.calls.append((list(cmd), cwd, dict(env or {})))
        return self._outcomes.pop(0)

    @property
    def commands(self) -> list[list[str]]:
        return [cmd for cmd, _cwd, _env in self.calls]


class FakeWorkspace:
    """A flat path → content map. ``unreadable`` paths raise, as a binary file really would."""

    def __init__(
        self, files: dict[str, str] | None = None, unreadable: dict[str, Exception] | None = None
    ) -> None:
        self.files = dict(files or {})
        self.unreadable = dict(unreadable or {})

    async def read(self, project: Project, path: str) -> FileContent:
        if path in self.unreadable:
            raise self.unreadable[path]
        if path not in self.files:
            raise NotFoundError(f"File not found: {path}")
        content = self.files[path]
        return FileContent(path=path, content=content, size=len(content.encode("utf-8")))

    async def tree(
        self, project: Project, path: str = ".", depth: int | None = None
    ) -> list[FileNode]:
        prefix = "" if path in (".", "") else path.rstrip("/") + "/"
        known = sorted(set(self.files) | set(self.unreadable))
        return [
            FileNode(path=p, type="file", size=len(self.files.get(p, "").encode("utf-8")))
            for p in known
            if p.startswith(prefix)
        ]


def _builder(workspace: FakeWorkspace, exec_service: ScriptedExec) -> _WorkspaceFrontendBuilder:
    return _WorkspaceFrontendBuilder(workspace=workspace, exec_service=exec_service)


async def test_a_failing_build_reports_the_command_and_its_output() -> None:
    project = await make_project()
    ref = await get_blob_store().put(_TSC_ERROR)
    scripted = ScriptedExec([_ok(), _failed(1, ref)])

    with pytest.raises(UserError) as err:
        await _builder(FakeWorkspace(), scripted)(project, FrontendPlan(), {})

    message = str(err.value)
    assert "pnpm build" in message
    assert "exited 1" in message
    assert "TS2322" in message  # the compiler's own words, which used to be dropped


async def test_a_failing_install_stops_before_the_build() -> None:
    """No point compiling against dependencies that were never installed."""
    project = await make_project()
    ref = await get_blob_store().put(b"ERR_PNPM_META_FETCH_FAIL  GET https://registry.npmjs.org/")
    scripted = ScriptedExec([_failed(1, ref)])

    with pytest.raises(UserError) as err:
        await _builder(FakeWorkspace(), scripted)(project, FrontendPlan(), {})

    assert "pnpm install" in str(err.value)
    assert "ERR_PNPM_META_FETCH_FAIL" in str(err.value)
    assert scripted.commands == [["pnpm", "install"]]  # the build was never attempted


async def test_a_timed_out_build_says_so_rather_than_reporting_an_exit_code() -> None:
    project = await make_project()
    scripted = ScriptedExec([_ok(), _failed(124, None, timed_out=True)])

    with pytest.raises(UserError) as err:
        await _builder(FakeWorkspace(), scripted)(project, FrontendPlan(), {})

    assert "timed out" in str(err.value)


async def test_a_failure_with_no_captured_output_still_names_the_command() -> None:
    """The blob store can be empty or unreachable; the message must survive that."""
    project = await make_project()
    scripted = ScriptedExec([_ok(), _failed(2, None)])

    with pytest.raises(UserError) as err:
        await _builder(FakeWorkspace(), scripted)(project, FrontendPlan(), {})

    assert str(err.value) == "The frontend build failed: `pnpm build` exited 2."


async def test_a_successful_build_returns_the_bundle_relative_to_the_output_dir() -> None:
    """The happy path is unchanged — workspace-relative going in, bundle-relative coming out."""
    project = await make_project()
    scripted = ScriptedExec([_ok(), _ok()])

    files = await _builder(FakeWorkspace(_BUNDLE), scripted)(
        project, FrontendPlan(), {"VITE_API_BASE_URL": "https://api.example.com"}
    )

    assert set(files) == {"index.html", "assets/index-abc.js"}
    assert files["assets/index-abc.js"] == "console.log(1)"
    # Both commands ran in the plan's directory, with the env the SPA is compiled against.
    assert scripted.commands == [["pnpm", "install"], ["pnpm", "build"]]
    assert {cwd for _cmd, cwd, _env in scripted.calls} == {"frontend"}
    assert scripted.calls[1][2]["VITE_API_BASE_URL"] == "https://api.example.com"


async def test_the_install_is_not_told_this_is_a_production_build() -> None:
    """pnpm reads NODE_ENV=production as ``--prod`` and strips devDependencies.

    vite and typescript are devDependencies, so an install under that flag removes the toolchain the
    very next command needs. Worse, pnpm asks before wiping ``node_modules`` and a sandbox exec has
    no stdin, so it takes EOF for an answer and exits 0 having installed nothing at all.
    """
    project = await make_project()
    scripted = ScriptedExec([_ok(), _ok()])

    await _builder(FakeWorkspace(_BUNDLE), scripted)(
        project,
        FrontendPlan(),
        {"NODE_ENV": "production", "VITE_API_BASE_URL": "https://api.example.com"},
    )

    install_env, build_env = scripted.calls[0][2], scripted.calls[1][2]
    assert "NODE_ENV" not in install_env
    assert build_env["NODE_ENV"] == "production"  # the build still compiles for production
    # Everything else the SPA is compiled against reaches both.
    assert install_env["VITE_API_BASE_URL"] == "https://api.example.com"


async def test_a_build_that_succeeds_but_writes_nothing_names_the_directory() -> None:
    """Still an error — but now it can only mean what it says, since the build really did pass."""
    project = await make_project()
    scripted = ScriptedExec([_ok(), _ok()])

    with pytest.raises(UserError) as err:
        await _builder(FakeWorkspace({"frontend/src/main.tsx": "export {}"}), scripted)(
            project, FrontendPlan(), {}
        )

    assert "frontend/dist" in str(err.value)
    assert "pnpm build" in str(err.value)


async def test_a_binary_asset_in_the_bundle_is_named() -> None:
    """A bundle ships as text, so a PNG cannot go up — say which file, not "no output"."""
    project = await make_project()
    workspace = FakeWorkspace(
        _BUNDLE, unreadable={"frontend/dist/logo.png": UserError("File is not UTF-8 text")}
    )
    scripted = ScriptedExec([_ok(), _ok()])

    with pytest.raises(UserError) as err:
        await _builder(workspace, scripted)(project, FrontendPlan(), {})

    assert "frontend/dist/logo.png" in str(err.value)
    assert "not UTF-8" in str(err.value)


async def test_a_custom_output_dir_from_the_plan_is_honoured() -> None:
    """`vite.config.ts` can move the bundle; the analyzer reads it and the builder must follow."""
    project = await make_project()
    workspace = FakeWorkspace({"frontend/build/index.html": "<!doctype html>"})
    scripted = ScriptedExec([_ok(), _ok()])

    files = await _builder(workspace, scripted)(project, FrontendPlan(output_dir="build"), {})

    assert set(files) == {"index.html"}
