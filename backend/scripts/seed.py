"""Seed/migration: ensure indexes and seed default platform settings.

Run from the backend dir:  ``uv run python -m scripts.seed``  (or ``make seed``).
Also runnable as a file:    ``uv run python scripts/seed.py``.

Index creation happens via ``init_db`` (Beanie builds every model's declared indexes).
Default ``PlatformSetting`` rows mirror the env/default config values so the admin dashboard
(phase-52) has something to edit. A dev admin *user* is intentionally not seeded here — password
hashing lands in phase-03.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running as a bare file (`python scripts/seed.py`) by putting backend/ on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.auth.service import hash_password  # noqa: E402
from app.core.config import get_config  # noqa: E402
from app.db.models import User  # noqa: E402
from app.db.models.enums import SettingCategory, UserRole  # noqa: E402
from app.db.mongo import close_client, init_db  # noqa: E402
from app.db.repos import PlatformSettingRepo, UserRepo  # noqa: E402

# (config key, category) — keys match Settings field names so the phase-51 DB provider can
# override them by the same name.
DEFAULT_SETTINGS: list[tuple[str, SettingCategory]] = [
    ("model_codegen", SettingCategory.models),
    ("model_routing", SettingCategory.models),
    ("anthropic_base_url", SettingCategory.models),
    ("design_provider", SettingCategory.providers),
    ("deploy_default_mode", SettingCategory.deploy),
    ("sandbox_image", SettingCategory.sandbox),
    ("sandbox_idle_timeout_s", SettingCategory.sandbox),
]


async def _seed_admin() -> str:
    """Create a dev admin user when SEED_ADMIN_EMAIL/PASSWORD are configured."""
    config = get_config()
    email = str(config.get("seed_admin_email")).strip().lower()
    password = str(config.get("seed_admin_password"))
    if not email or not password:
        return "skipped (set SEED_ADMIN_EMAIL + SEED_ADMIN_PASSWORD to enable)"

    users = UserRepo()
    if await users.get_by_email(email) is not None:
        return f"already exists ({email})"
    await users.insert(
        User(email=email, hashed_password=hash_password(password), role=UserRole.admin)
    )
    return f"created admin ({email})"


async def seed() -> None:
    await init_db()  # ensures indexes on all collections
    config = get_config()
    repo = PlatformSettingRepo()

    created = 0
    for key, category in DEFAULT_SETTINGS:
        if await repo.get_by_key(key) is None:
            await repo.upsert(key, config.get(key), category)
            created += 1

    admin_status = await _seed_admin()

    print(f"seed complete: indexes ensured; {created} default platform settings created")
    print(f"admin user: {admin_status}")
    await close_client()


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
