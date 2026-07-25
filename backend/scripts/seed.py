"""Seed/migration: ensure indexes and (optionally) create the dev admin user.

Run from the backend dir:  ``uv run python -m scripts.seed``  (or ``make seed``).
Also runnable as a file:    ``uv run python scripts/seed.py``.

Index creation happens via ``init_db`` (Beanie builds every model's declared indexes).

**No ``PlatformSetting`` rows are seeded.** The admin dashboard reads its catalog from the config
registry, so it has plenty to edit without them — and a seeded row *is* an admin override, which
would make a handful of keys report source ``admin`` on a fresh install when nobody chose anything.
Leaving the collection empty keeps the badge honest: ``admin`` means a human set it here.
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
from app.db.models.enums import UserRole  # noqa: E402
from app.db.mongo import close_client, init_db  # noqa: E402
from app.db.repos import UserRepo  # noqa: E402


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
    admin_status = await _seed_admin()

    print("seed complete: indexes ensured")
    print(f"admin user: {admin_status}")
    await close_client()


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
