"""FastAPI application factory.

Phase 01 mounts only ``/health`` + cross-cutting middleware (CORS, error envelope, logging).
Later phases register routers here (auth, projects, orchestrator, realtime, deploy, …).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.router import router as auth_router
from app.core.config import get_config
from app.core.cost_router import cost_router
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.observability import install_correlation_filter
from app.data_browser.router import data_router
from app.db.mongo import close_client, init_db
from app.deploy.credentials_router import credentials_router
from app.deploy.deploy_router import deploy_router
from app.deploy.router import infra_router
from app.design.router import router as design_router
from app.eval.router import eval_router
from app.orchestrator.router import intents_router, requirements_router
from app.orchestrator.router import router as artifacts_router
from app.projects.router import router as projects_router
from app.realtime.router import router as realtime_router
from app.sandbox.exec_router import router as exec_router
from app.sandbox.fs_router import router as workspace_router
from app.sandbox.manager import get_manager
from app.sandbox.preview_router import router as preview_router
from app.sandbox.router import router as sandbox_router
from app.testing.repair_router import repair_router
from app.testing.router import tests_router
from app.testing.validate_router import validate_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Best-effort DB init: a control plane without Mongo is degraded, but /health must still
    # answer so operators can see the process is up. Hard failures surface in logs.
    try:
        await init_db()
    except Exception:
        logger.warning("init_db failed at startup; continuing degraded", exc_info=True)

    manager = get_manager()
    # Adopt any surviving sandbox containers / clear stale mappings. Best-effort: a control
    # plane without a reachable Docker daemon still serves everything except sandbox ops.
    try:
        await manager.reconcile()
    except Exception:
        logger.info("sandbox reconcile skipped (docker unavailable)", exc_info=True)

    stop_event = asyncio.Event()
    reaper_task = asyncio.create_task(manager.run_reaper(stop_event=stop_event))
    try:
        yield
    finally:
        stop_event.set()
        reaper_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reaper_task
        await close_client()


def create_app() -> FastAPI:
    config = get_config()
    configure_logging(config.get("log_level"), config.get("log_json"))
    # Every log line emitted inside a traced run carries its project/run ids (phase-46).
    install_correlation_filter()

    app = FastAPI(
        title="ForgeFlow Control Plane",
        version=config.get("app_version"),
        lifespan=lifespan,
    )

    origins = [o.strip() for o in str(config.get("cors_origins")).split(",") if o.strip()]
    allow_all = origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=not allow_all,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(realtime_router)
    app.include_router(projects_router)
    app.include_router(sandbox_router)
    app.include_router(workspace_router)
    app.include_router(exec_router)
    app.include_router(preview_router)
    app.include_router(artifacts_router)
    app.include_router(intents_router)
    app.include_router(requirements_router)
    app.include_router(design_router)
    app.include_router(tests_router)
    app.include_router(repair_router)
    app.include_router(validate_router)
    app.include_router(infra_router)
    app.include_router(deploy_router)
    app.include_router(credentials_router)
    app.include_router(data_router)
    app.include_router(eval_router)
    app.include_router(cost_router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "env": config.get("forgeflow_env"),
            "version": config.get("app_version"),
        }

    return app


app = create_app()
