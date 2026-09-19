"""The template must be *replaced by feature code*, not re-worded (phase-64).

Observed on a real project: every planned phase after the first wrote nothing, the string-only
placeholder gate flagged the skeleton's copy, and the repair loop — minimal by design — produced the
smallest diff that satisfied the grep: ``ForgeFlow App → My App``, the demo page → ``Home /
Welcome to the app.``. The app was an empty shell and the report said 8/8 phases complete.

The structural check asks the questions the grep cannot: is there feature code on both sides, is a
feature router mounted, does the route table render any of it? A miss is ``no_feature_code`` and,
like ``demo_test_left``, is never handed to the repair loop — missing code is not a patch.

DB-free: the guard returns before persisting anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from beanie import PydanticObjectId

from app.db.models.enums import Stage
from app.orchestrator.stages.build_verify import (
    STOP_NO_FEATURE_CODE,
    BootProbe,
    BuildVerificationController,
    FeatureCodeExpectation,
    StepProbe,
    _default_feature_code,
    app_mounts_feature_router,
    feature_code_findings,
    feature_files,
    routes_render_feature_code,
)
from app.orchestrator.stages.repair import RepairLoopController
from app.sandbox.schemas import FileNode
from tests.orchestrator.stages.repair_loop_fakes import ScriptedAgent, StubAnalyzer

if TYPE_CHECKING:
    from app.agents.tools.context import ToolContext
    from app.db.models import Project, Run

SKELETON = Path(__file__).resolve().parents[4] / "templates" / "app-skeleton"
APP_TS = "backend/src/app.ts"
ROUTES = "frontend/src/routes.tsx"
HOME = "frontend/src/pages/HomePage.tsx"


def _skeleton(path: str) -> str:
    return (SKELETON / path).read_text(encoding="utf-8")


# ---------------------------------------------------------------- the pure checks


def test_the_real_skeleton_is_the_template_on_every_count() -> None:
    findings = feature_code_findings(
        paths=["backend/src/features/README.md", "frontend/src/features/README.md"],
        app_src=_skeleton(APP_TS),
        routes_src=_skeleton(ROUTES),
        index_page_src=_skeleton(HOME),
        expect=FeatureCodeExpectation(),
    )
    assert findings == [
        "backend/src/features/ holds no feature code",
        "backend/src/app.ts mounts no feature router",
        "frontend/src/features/ holds no feature code",
        "frontend/src/routes.tsx does not render feature code",
    ]


def test_the_reworded_template_from_the_report_is_still_the_template() -> None:
    """Exactly what commit 887c09c left behind: no `ForgeFlow`, no `Your app starts here`, and no
    app either."""
    routes = (
        "import { createBrowserRouter } from 'react-router-dom'\n"
        "import { Layout } from './components/Layout'\n"
        "import { HomePage } from './pages/HomePage'\n"
        "export const router = createBrowserRouter([\n"
        "  { path: '/', element: <Layout />,\n"
        "    children: [{ index: true, element: <HomePage /> }] },\n"
        "])\n"
    )
    home = (
        "export function HomePage() {\n"
        "  return <section><h1>Home</h1><p>Welcome to the app.</p></section>\n"
        "}\n"
    )
    findings = feature_code_findings(
        paths=["backend/src/models/User.ts", "backend/src/features/README.md"],
        app_src=_skeleton(APP_TS),
        routes_src=routes,
        index_page_src=home,
        expect=FeatureCodeExpectation(),
    )
    assert len(findings) == 4


def test_a_real_app_passes() -> None:
    app = _skeleton(APP_TS).replace(
        "import { healthRouter } from './routes/health'",
        "import { healthRouter } from './routes/health'\n"
        "import { postsRouter } from './features/posts/posts.routes'",
    )
    app = app.replace(
        "// FORGEFLOW:ROUTES", "app.use('/api/posts', postsRouter)\n  // FORGEFLOW:ROUTES"
    )
    routes = (
        "import { PostList } from './features/posts/PostList'\n"
        "children: [{ index: true, element: <PostList /> }]\n"
    )
    assert (
        feature_code_findings(
            paths=[
                "backend/src/features/posts/posts.routes.ts",
                "backend/src/features/posts/posts.test.ts",
                "frontend/src/features/posts/PostList.tsx",
            ],
            app_src=app,
            routes_src=routes,
            index_page_src=None,
            expect=FeatureCodeExpectation(),
        )
        == []
    )


def test_a_landing_page_that_renders_feature_code_counts() -> None:
    """Keeping `HomePage.tsx` as the landing page is fine when it is the app's page."""
    home = (
        "import { PostList } from '../features/posts/PostList'\n"
        "export function HomePage() { return <PostList /> }\n"
    )
    assert routes_render_feature_code(_skeleton(ROUTES), home) is True
    assert routes_render_feature_code(_skeleton(ROUTES), _skeleton(HOME)) is False


def test_the_skeletons_own_example_comment_does_not_count_as_a_mounted_router() -> None:
    """`//   app.use('/api/todos', todosRouter)` shows the model what to write; it is not code."""
    assert app_mounts_feature_router(_skeleton(APP_TS)) is False
    assert app_mounts_feature_router("app.use('/api/todos', todosRouter)") is True
    assert app_mounts_feature_router("/* app.use('/api/x', x) */ app.use('/health', h)") is False
    assert app_mounts_feature_router("import { r } from './features/x/x.routes'") is True


def test_tests_readmes_and_configs_are_not_feature_code() -> None:
    paths = [
        "backend/src/features/README.md",
        "backend/src/features/posts/posts.test.ts",
        "backend/src/features/posts/posts.spec.ts",
        "backend/src/features/posts/__tests__/x.ts",
        "backend/src/features/posts/schema.json",
        "backend/src/features/posts/posts.routes.ts",
    ]
    assert feature_files(paths, "backend/src/features") == [
        "backend/src/features/posts/posts.routes.ts"
    ]


def test_only_the_expected_halves_are_demanded() -> None:
    findings = feature_code_findings(
        paths=["frontend/src/features/p/P.tsx"],
        app_src=_skeleton(APP_TS),
        routes_src="import { P } from './features/p/P'",
        index_page_src=None,
        expect=FeatureCodeExpectation(backend=False),
    )
    assert findings == []


# ---------------------------------------------------------------- the controller


class _StubProject:
    def __init__(self) -> None:
        self.id = PydanticObjectId()


def _controller(*, findings: list[str], scripted: ScriptedAgent) -> BuildVerificationController:
    async def install(project: object, ctx: object) -> None:
        return None

    async def typecheck(project: object, ctx: object) -> StepProbe:
        return StepProbe(output="", exit_code=0)

    async def boot(project: object, ctx: object) -> BootProbe:
        return BootProbe(fe_status="running", be_status="running")

    async def placeholder(project: object, ctx: object) -> list[str]:
        return ["frontend/src/routes.tsx"]  # the string gate ALSO fires — and must not win

    async def demo_test(project: object, ctx: object) -> list[str]:
        return []

    async def feature_code(project: object, ctx: object, expect: object) -> list[str]:
        return list(findings)

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
        demo_test=demo_test,
        feature_code=feature_code,
        container_state=container_state,
        analyzer_factory=lambda written: StubAnalyzer(),
        repair_factory=repair_factory,
    )


@pytest.mark.asyncio
async def test_no_feature_code_stops_before_the_repair_loop_is_ever_entered() -> None:
    scripted = ScriptedAgent([])
    controller = _controller(
        findings=["backend/src/features/ holds no feature code"], scripted=scripted
    )

    report = await controller.run(
        cast("Project", _StubProject()),
        cast("Run", object()),
        ctx=cast("ToolContext", object()),
        channel="c",
        written_files=[],
        expect=FeatureCodeExpectation(),
        phases_wrote_nothing=7,
    )

    assert report.ok is False
    assert report.stop_reason == STOP_NO_FEATURE_CODE
    assert report.feature_code_ok is False
    assert "still the template" in report.message
    assert "backend/src/features/ holds no feature code" in report.message
    assert "7 phase(s) wrote nothing" in report.hint
    # The whole point: the loop would only re-word the template, so it is never entered — even
    # though the placeholder gate produced a repairable-looking finding of its own.
    assert scripted.iterations == []


@pytest.mark.asyncio
async def test_the_hint_without_a_phase_count_still_names_the_resume_path() -> None:
    controller = _controller(findings=["x"], scripted=ScriptedAgent([]))
    report = await controller.run(
        cast("Project", _StubProject()),
        cast("Run", object()),
        ctx=cast("ToolContext", object()),
        channel="c",
    )
    assert "Run Build again" in report.hint


@pytest.mark.asyncio
async def test_the_default_step_reads_the_workspace() -> None:
    """The real step: a tree listing of the two feature roots plus three source reads."""

    class _Doc:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Workspace:
        def __init__(self, files: dict[str, str]) -> None:
            self.files = files

        async def read(self, project: object, path: str) -> _Doc:
            from app.core.errors import NotFoundError

            if path not in self.files:
                raise NotFoundError(path)
            return _Doc(self.files[path])

        async def tree(
            self, project: object, path: str, depth: int | None = None
        ) -> list[FileNode]:
            return [
                FileNode(path=p, type="file", size=len(c))
                for p, c in self.files.items()
                if p.startswith(path.rstrip("/") + "/")
            ]

    class _Ctx:
        def __init__(self, files: dict[str, str]) -> None:
            self.workspace = _Workspace(files)

    template = _Ctx(
        {
            APP_TS: _skeleton(APP_TS),
            ROUTES: _skeleton(ROUTES),
            HOME: _skeleton(HOME),
            "backend/src/features/README.md": "#",
            "frontend/src/features/README.md": "#",
        }
    )
    findings = await _default_feature_code(
        cast("Project", _StubProject()), cast("ToolContext", template), FeatureCodeExpectation()
    )
    assert len(findings) == 4

    built = _Ctx(
        {
            APP_TS: "import { r } from './features/posts/posts.routes'\napp.use('/api/posts', r)",
            ROUTES: "import { PostList } from './features/posts/PostList'",
            "backend/src/features/posts/posts.routes.ts": "export {}",
            "frontend/src/features/posts/PostList.tsx": "export {}",
        }
    )
    assert (
        await _default_feature_code(
            cast("Project", _StubProject()), cast("ToolContext", built), FeatureCodeExpectation()
        )
        == []
    )
