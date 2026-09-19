"""A stale skeleton demo test never reaches the repair loop (root-cause guard).

The failure this pins down, observed on a real project: the skeleton ships two example tests that
assert its placeholder page is on screen, while ``build_verify``'s placeholder gate *fails* any
build that still contains that copy. Both can never hold at once, and ``agents/repair.py``'s
``is_test_file`` bars the loop from editing a test to break the tie — so the loop patched pages
that were never at fault, its suite re-run never reached zero failures, and it escalated on
no-progress having spent ~125k tokens.

So this is classified *before* any synthetic run is built, exactly like an ``environment`` verdict:
``stop_reason="demo_test_left"`` and **zero** repair iterations, asserted as
``ScriptedAgent.iterations == []``.

DB-free: the guard returns before persisting anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from beanie import PydanticObjectId

from app.db.models.enums import Stage
from app.orchestrator.stages.build_verify import (
    STOP_DEMO_TEST,
    BootProbe,
    BuildVerificationController,
    StepProbe,
    _default_demo_test,
)
from app.orchestrator.stages.repair import RepairLoopController
from tests.orchestrator.stages.repair_loop_fakes import ScriptedAgent, StubAnalyzer

if TYPE_CHECKING:  # the stubs below are structural stand-ins; nothing here touches a DB
    from app.agents.tools.context import ToolContext
    from app.db.models import Project, Run

# The two files phase-54 requires codegen to rewrite in place.
APP_TEST = "frontend/src/App.test.tsx"
HOME_SPEC = "frontend/e2e/home.spec.ts"


async def _no_feature_findings(project: object, ctx: object, expect: object) -> list[str]:
    return []


class _StubProject:
    def __init__(self) -> None:
        self.id = PydanticObjectId()


class _Doc:
    def __init__(self, content: str) -> None:
        self.content = content


class _Workspace:
    """Only the ``read`` slice the scan uses."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    async def read(self, project: object, path: str) -> _Doc:
        from app.core.errors import NotFoundError

        if path not in self.files:
            raise NotFoundError(f"no such file: {path}")
        return _Doc(self.files[path])


class _Ctx:
    def __init__(self, files: dict[str, str]) -> None:
        self.workspace = _Workspace(files)


def _controller(*, stale: list[str], scripted: ScriptedAgent) -> BuildVerificationController:
    async def install(project: object, ctx: object) -> None:
        return None

    async def typecheck(project: object, ctx: object) -> StepProbe:
        return StepProbe(output="", exit_code=0)

    async def boot(project: object, ctx: object) -> BootProbe:
        return BootProbe(fe_status="running", be_status="running")

    async def placeholder(project: object, ctx: object) -> list[str]:
        return []

    async def demo_test(project: object, ctx: object) -> list[str]:
        return list(stale)

    async def container_state(project_id: str) -> str:
        return "running"

    def repair_factory(analyzer: Any) -> RepairLoopController:
        return RepairLoopController(
            agent=scripted, analyzer=analyzer, stage=Stage.build, owns_stage_status=False
        )

    return BuildVerificationController(
        install=install,
        typecheck=typecheck,
        boot=boot,
        placeholder=placeholder,
        feature_code=_no_feature_findings,  # phase-64's structural check: not this file's subject
        demo_test=demo_test,
        container_state=container_state,
        analyzer_factory=lambda written: StubAnalyzer(),
        repair_factory=repair_factory,
    )


@pytest.mark.asyncio
async def test_stale_demo_test_stops_without_spending_a_single_repair_iteration() -> None:
    scripted = ScriptedAgent([])
    controller = _controller(stale=[HOME_SPEC], scripted=scripted)

    report = await controller.run(
        cast("Project", _StubProject()),
        cast("Run", object()),
        ctx=cast("ToolContext", object()),
        channel="c",
        written_files=[],
    )

    assert report.ok is False
    assert report.stop_reason == STOP_DEMO_TEST
    # The whole point: the loop cannot win this one, so it is never entered.
    assert scripted.iterations == []


@pytest.mark.asyncio
async def test_the_report_names_the_offending_file_and_what_to_do() -> None:
    """A generic "tests failed" is what cost the real project its budget — be specific."""
    controller = _controller(stale=[APP_TEST, HOME_SPEC], scripted=ScriptedAgent([]))

    report = await controller.run(
        cast("Project", _StubProject()),
        cast("Run", object()),
        ctx=cast("ToolContext", object()),
        channel="c",
        written_files=[],
    )

    assert APP_TEST in report.message and HOME_SPEC in report.message
    assert "Rewrite" in report.hint
    # Deleting them is not the fix — an empty test run is itself a failure (phase-54).
    assert "never" in report.hint and "delete" in report.hint


@pytest.mark.asyncio
async def test_rewritten_demo_tests_let_verification_pass() -> None:
    controller = _controller(stale=[], scripted=ScriptedAgent([]))

    report = await controller.run(
        cast("Project", _StubProject()),
        cast("Run", object()),
        ctx=cast("ToolContext", object()),
        channel="c",
        written_files=[],
    )

    assert report.ok is True
    assert report.stop_reason is None


@pytest.mark.asyncio
async def test_scan_is_case_insensitive_because_the_copy_lives_in_a_regex_literal() -> None:
    """The exact hole that let this through: the source scan matches case-sensitively, but these
    files spell the placeholder lowercased inside `/your app starts here/i`."""
    ctx = _Ctx(
        {
            HOME_SPEC: (
                "test('home page renders', async ({ page }) => {\n"
                "  await expect(page.getByRole('heading', "
                "{ name: /your app starts here/i })).toBeVisible()\n"
                "})\n"
            )
        }
    )

    assert await _default_demo_test(cast("Project", object()), cast("ToolContext", ctx)) == [
        HOME_SPEC
    ]


@pytest.mark.asyncio
async def test_scan_passes_a_demo_test_that_asserts_real_app_content() -> None:
    ctx = _Ctx(
        {
            APP_TEST: "expect(screen.getByText(/mathpro calculator/i)).toBeInTheDocument()",
            HOME_SPEC: "await expect(page.locator('#root')).not.toBeEmpty()",
        }
    )

    assert await _default_demo_test(cast("Project", object()), cast("ToolContext", ctx)) == []


@pytest.mark.asyncio
async def test_scan_tolerates_a_workspace_missing_the_demo_tests() -> None:
    """A generated app may legitimately not have them; absence is not a finding."""
    assert await _default_demo_test(cast("Project", object()), cast("ToolContext", _Ctx({}))) == []
