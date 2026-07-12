"""FastAPI application factory.

Phase 01 mounts only ``/health`` + cross-cutting middleware (CORS, error envelope, logging).
Later phases register routers here (auth, projects, orchestrator, realtime, deploy, …).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_config
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    config = get_config()
    configure_logging(config.get("log_level"), config.get("log_json"))

    app = FastAPI(
        title="ForgeFlow Control Plane",
        version=config.get("app_version"),
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
