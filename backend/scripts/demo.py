"""Demo seed + reset (phase-50) — set up, or tear down, the hero-demo starting state.

Run from the backend dir:
    uv run python -m scripts.demo seed     # create the demo account + a pre-loaded project
    uv run python -m scripts.demo reset     # return to a clean pre-demo state
    uv run python -m scripts.demo status    # show what's currently seeded

`seed` pre-creates the demo user and a "Hero Demo" project with the hero-todo requirements already
captured, so a rehearsal or live demo starts at the interesting part (design → build → the repair
moment → deploy) instead of re-typing a spec. `reset` deletes the demo user's projects — cascading
their stage state, conversation and artifacts — and best-effort reaps each project's sandbox and app
database, so the next run starts clean.

Credentials come from config (`DEMO_EMAIL` / `DEMO_PASSWORD`); the script refuses to seed without an
explicit password rather than shipping a known one (§7).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow running as a bare file by putting backend/ on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.service import hash_password  # noqa: E402
from app.core.config import get_config  # noqa: E402
from app.db.models import Project, User  # noqa: E402
from app.db.models.enums import Stage, StageStatus, UserRole  # noqa: E402
from app.db.mongo import close_client, init_db  # noqa: E402
from app.db.repos import StageStateRepo, UserRepo  # noqa: E402
from app.eval.specs_loader import SPECS_DIR, load_spec  # noqa: E402
from app.orchestrator.requirements import RequirementsService  # noqa: E402
from app.projects.service import ProjectService  # noqa: E402

logger = logging.getLogger("demo")

DEMO_PROJECT_NAME = "Hero Demo"
HERO_SPEC = "hero-todo"


def _credentials() -> tuple[str, str]:
    config = get_config()
    email = str(config.get("demo_email")).strip().lower()
    password = str(config.get("demo_password"))
    return email, password


async def _demo_user() -> User | None:
    email, _ = _credentials()
    return await UserRepo().get_by_email(email)


# The DB-operating cores take no responsibility for connection lifecycle, so they are testable
# against a disposable DB; the CLI wrappers below add init_db()/close_client().


async def seed_demo() -> str:
    email, password = _credentials()
    if not password:
        return (
            "refusing to seed: set DEMO_PASSWORD (a known default credential is a security risk)."
        )

    users = UserRepo()
    user = await users.get_by_email(email)
    created_user = user is None
    if user is None:
        user = await users.insert(
            User(email=email, hashed_password=hash_password(password), role=UserRole.user)
        )
    assert user.id is not None

    existing = [
        p for p in await ProjectService().list_for_user(user.id) if p.name == DEMO_PROJECT_NAME
    ]
    if existing:
        return f"'{DEMO_PROJECT_NAME}' already present ({len(existing)}); run `reset` first."

    project = await ProjectService().create_project(user.id, DEMO_PROJECT_NAME)
    assert project.id is not None

    spec = load_spec(SPECS_DIR / f"{HERO_SPEC}.yaml", specs_dir=SPECS_DIR)
    await RequirementsService().save(project.id, spec.requirements)
    # Requirements captured → start the demo at design/build, the part worth watching live.
    await StageStateRepo().set_status(project.id, Stage.requirements, StageStatus.complete)

    prefix = "created demo user + " if created_user else ""
    return f"{prefix}seeded '{DEMO_PROJECT_NAME}' ({project.id}) with the {HERO_SPEC} requirements"


async def reset_demo() -> int:
    """Delete the demo user's projects (cascading their data). Returns how many were removed."""
    user = await _demo_user()
    if user is None or user.id is None:
        return 0
    projects = await ProjectService().list_for_user(user.id)
    for project in projects:
        await _teardown_resources(project)  # best-effort sandbox + app DB
        assert project.id is not None
        await ProjectService().delete(project.id, user.id)  # cascades DB docs
    return len(projects)


async def seed() -> None:
    await init_db()
    print(await seed_demo())
    email, _ = _credentials()
    print(f"\nlog in as {email} and drive: design → build → repair → deploy → validate")
    await close_client()


async def reset() -> None:
    await init_db()
    email, _ = _credentials()
    count = await reset_demo()
    print(f"reset: deleted {count} project(s) for {email}; account kept.")
    await close_client()


async def status() -> None:
    await init_db()
    email, password = _credentials()
    user = await _demo_user()
    if user is None or user.id is None:
        print(f"demo user {email}: absent · password {'set' if password else 'UNSET'}")
        await close_client()
        return
    projects = await ProjectService().list_for_user(user.id)
    print(f"demo user {email}: present · {len(projects)} project(s)")
    for project in projects:
        print(f"  - {project.name} ({project.id}) — stage: {project.current_stage}")
    await close_client()


async def _teardown_resources(project: Project) -> None:
    """Reap a project's sandbox + app DB. Best-effort: unavailable infra must not block a reset."""
    try:
        from app.sandbox.manager import get_manager

        await get_manager().destroy(project, remove_volume=True)
    except Exception:
        logger.info("demo reset: sandbox teardown skipped for %s", project.id, exc_info=True)
    try:
        from app.deploy.db_provision import DbProvisioner

        await DbProvisioner().teardown(project, drop_data=True)
    except Exception:
        logger.info("demo reset: app DB teardown skipped for %s", project.id, exc_info=True)


_COMMANDS = {"seed": seed, "reset": reset, "status": status}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed or reset the hero-demo state.")
    parser.add_argument("command", choices=sorted(_COMMANDS), help="what to do")
    args = parser.parse_args(argv)
    asyncio.run(_COMMANDS[args.command]())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
