"""FastAPI application factory.

Phase 01 mounts only ``/health`` + cross-cutting middleware (CORS, error envelope, logging).
Later phases register routers here (auth, projects, orchestrator, realtime, deploy, …).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_config
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.db.mongo import close_client, init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Best-effort DB init: a control plane without Mongo is degraded, but /health must still
    # answer so operators can see the process is up. Hard failures surface in logs.
    try:
        await init_db()
    except Exception:
        logger.warning("init_db failed at startup; continuing degraded", exc_info=True)
    try:
        yield
    finally:
        await close_client()


def create_app() -> FastAPI:
    config = get_config()
    configure_logging(config.get("log_level"), config.get("log_json"))

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

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "env": config.get("forgeflow_env"),
            "version": config.get("app_version"),
        }

    return app


app = create_app()
