"""The concrete agent tools (phase-21): FS, exec, tests, preview, git, skeleton — all sandbox-bound.

Every tool's args are a pydantic model (single source of truth for validation + JSON schema). Path
safety is enforced by :mod:`app.sandbox.paths` (reused here for ``cwd``) and by the FS layer, so an
absolute/traversing path is rejected before anything touches the sandbox.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.agents.tools.context import ToolContext
from app.agents.tools.registry import Tool, ToolRegistry, tool_ok
from app.agents.tools.skeleton import copy_skeleton
from app.core.config import get_config
from app.db.blobs import get_blob_store
from app.sandbox.exec import ExecOutcome
from app.sandbox.paths import require_rel_path, safe_rel_path
from app.sandbox.schemas import PreviewInfo

# --------------------------------------------------------------------- arg models


class ReadFileArgs(BaseModel):
    path: str = Field(min_length=1, description="Workspace-relative file path")


class WriteFileArgs(BaseModel):
    path: str = Field(min_length=1, description="Workspace-relative file path")
    content: str = Field(description="Full UTF-8 file contents")


class ListDirArgs(BaseModel):
    path: str = Field(default=".", description="Workspace-relative directory (default: root)")


class RunCommandArgs(BaseModel):
    cmd: list[str] = Field(min_length=1, description='argv (no shell); e.g. ["pnpm", "build"]')
    cwd: str = Field(default="", description="Workspace-relative working dir (default: root)")
    timeout_s: float | None = Field(default=None, description="Wall-clock timeout override")


class InstallDepsArgs(BaseModel):
    manager: Literal["pnpm", "npm", "yarn"] = "pnpm"
    packages: list[str] = Field(default_factory=list, description="Packages; empty → install lock")
    dev: bool = Field(default=False, description="Install as devDependencies")


class RunTestsArgs(BaseModel):
    scope: Literal["unit", "e2e", "all"] = "all"
    filter: str | None = Field(default=None, description="Optional test name/path filter")


class GitCommitArgs(BaseModel):
    message: str = Field(min_length=1, max_length=500)


class NoArgs(BaseModel):
    pass


# --------------------------------------------------------------------- shared helpers


async def _read_exec_output(outcome: ExecOutcome) -> str:
    """Fetch a finished command's captured output (tail-truncated for the model)."""
    if not outcome.output_ref:
        return ""
    try:
        data = await get_blob_store().get(outcome.output_ref)
    except Exception:  # missing/broken blob must not fail the tool
        return ""
    text = data.decode("utf-8", errors="replace")
    limit = int(get_config().get("tool_output_max_chars"))
    if len(text) > limit:
        text = "…(truncated)…\n" + text[-limit:]
    return text


_TEST_COMMANDS: dict[str, list[str]] = {
    "unit": ["pnpm", "test:unit"],
    "e2e": ["pnpm", "test:e2e"],
    "all": ["pnpm", "test"],
}


# --------------------------------------------------------------------- handlers


async def _read_file(ctx: ToolContext, args: ReadFileArgs) -> str:
    path = require_rel_path(args.path)  # tool-level guardrail: workspace-relative only
    content = await ctx.workspace.read(ctx.project, path)
    return tool_ok(path=content.path, size=content.size, content=content.content)


async def _write_file(ctx: ToolContext, args: WriteFileArgs) -> str:
    path = require_rel_path(args.path)  # tool-level guardrail: workspace-relative only
    node = await ctx.workspace.write(ctx.project, path, args.content)
    return tool_ok(path=node.path, size=node.size)


async def _list_dir(ctx: ToolContext, args: ListDirArgs) -> str:
    path = safe_rel_path(args.path)  # tool-level guardrail (root allowed)
    nodes = await ctx.workspace.tree(ctx.project, path, depth=1)
    entries = [{"path": n.path, "type": n.type, "size": n.size} for n in nodes]
    return tool_ok(path=args.path, entries=entries)


async def _run_command(ctx: ToolContext, args: RunCommandArgs) -> str:
    cwd = safe_rel_path(args.cwd) if args.cwd else ""  # guardrail: workspace-relative only
    outcome = await ctx.exec_service.run(ctx.project, args.cmd, cwd=cwd, timeout=args.timeout_s)
    return tool_ok(
        exit_code=outcome.exit_code,
        timed_out=outcome.timed_out,
        output=await _read_exec_output(outcome),
    )


async def _install_deps(ctx: ToolContext, args: InstallDepsArgs) -> str:
    if args.packages:
        subcmd = "install" if args.manager == "npm" else "add"
        cmd = [args.manager, subcmd, *(["-D"] if args.dev else []), *args.packages]
    else:
        cmd = [args.manager, "install"]
    # Installs get their own, longer wall clock: a cold install of the skeleton pulls hundreds of
    # packages and the default per-command timeout would kill it mid-flight. The registry-egress
    # window this needs is opened by the exec service (app.sandbox.network).
    outcome = await ctx.exec_service.run(
        ctx.project, cmd, timeout=float(get_config().get("sandbox_install_timeout_s"))
    )
    return tool_ok(cmd=cmd, exit_code=outcome.exit_code, output=await _read_exec_output(outcome))


async def _run_tests(ctx: ToolContext, args: RunTestsArgs) -> str:
    cmd = list(_TEST_COMMANDS[args.scope])
    if args.filter:
        cmd.append(args.filter)
    outcome = await ctx.exec_service.run(ctx.project, cmd)
    return tool_ok(
        scope=args.scope,
        passed=outcome.exit_code == 0,
        exit_code=outcome.exit_code,
        output=await _read_exec_output(outcome),
    )


async def _start_preview(ctx: ToolContext, _args: NoArgs) -> str:
    info = await ctx.preview.start(ctx.project)
    return _preview_result(info)


async def _restart_preview(ctx: ToolContext, _args: NoArgs) -> str:
    info = await ctx.preview.restart(ctx.project)
    return _preview_result(info)


def _preview_result(info: PreviewInfo) -> str:
    """Include any environment warning, so the agent reports it instead of a healthy-looking URL."""
    fields: dict[str, object] = {
        "fe_status": info.fe_status,
        "be_status": info.be_status,
        "fe_url": info.fe_url,
    }
    if info.warning:
        fields["warning"] = info.warning
    return tool_ok(**fields)


async def _git_commit(ctx: ToolContext, args: GitCommitArgs) -> str:
    sha, committed = await ctx.workspace.commit(ctx.project, args.message)
    return tool_ok(sha=sha, committed=committed)


async def _instantiate_skeleton(ctx: ToolContext, _args: NoArgs) -> str:
    written = await copy_skeleton(ctx.workspace, ctx.project)
    return tool_ok(files=len(written), paths=written[:50])


# --------------------------------------------------------------------- registry


ALL_TOOLS: list[Tool] = [
    Tool(
        "read_file",
        "Read a UTF-8 text file from the workspace.",
        ReadFileArgs,
        _read_file,
        guardrails="Workspace-relative paths only; size-capped.",
    ),
    Tool(
        "write_file",
        "Create or overwrite a UTF-8 text file in the workspace.",
        WriteFileArgs,
        _write_file,
        guardrails="Workspace-relative paths only; size-capped; emits fs.write.",
    ),
    Tool("list_dir", "List a workspace directory (one level).", ListDirArgs, _list_dir),
    Tool(
        "run_command",
        "Run a command (argv, no shell) inside the sandbox; streams output, bounded by a timeout.",
        RunCommandArgs,
        _run_command,
        guardrails="Sandboxed non-root; workspace-relative cwd; time + concurrency capped.",
    ),
    Tool(
        "install_deps",
        "Install dependencies with a package manager inside the sandbox.",
        InstallDepsArgs,
        _install_deps,
    ),
    Tool(
        "run_tests",
        "Run the test suite (unit/e2e/all) inside the sandbox; returns pass/fail + output.",
        RunTestsArgs,
        _run_tests,
        guardrails="Thin wrapper; the full runner/parser lands in phase-28.",
    ),
    Tool("start_preview", "Start the live preview dev servers.", NoArgs, _start_preview),
    Tool("restart_preview", "Restart the live preview dev servers.", NoArgs, _restart_preview),
    Tool("git_commit", "Stage everything and commit the workspace.", GitCommitArgs, _git_commit),
    Tool(
        "instantiate_skeleton",
        "Copy the prebuilt fixed-stack app skeleton into the workspace (never regenerate it).",
        NoArgs,
        _instantiate_skeleton,
    ),
]


def default_registry() -> ToolRegistry:
    return ToolRegistry(ALL_TOOLS)
