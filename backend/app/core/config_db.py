"""The DB (admin) layer of the config resolver (phase-51).

This is the front of the resolver chain — ``admin(DB) > env > default`` — so a value written from
the admin dashboard wins over both. The design tension it resolves: ``ConfigResolver.get`` is
**synchronous** (every consumer calls ``get_config().get(key)`` inline), but reading a
``PlatformSetting`` is an **async** Mongo query. A synchronous ``get`` cannot await.

The answer is a **synchronous in-memory cache** that async code refreshes:

- ``get(key)`` reads the cache — no I/O, safe to call from anywhere.
- ``load()`` (async) rebuilds the cache from Mongo. Called at startup and after every admin write,
  so an admin change is visible **immediately, without a restart** (the acceptance criterion).
- ``ensure_fresh(ttl)`` reloads only if the cache has aged past its TTL — the admin read routes call
  it so an out-of-band DB edit is eventually picked up.

**Fail-soft is mandatory** (§7): if Mongo is unavailable, ``load`` keeps the last-known cache (empty
at worst) and never raises, so config resolution degrades to env/default rather than taking the
control plane down.

**Secrets never sit in the cache in plaintext.** A sensitive key (an API token set from the admin
panel) is stored as Fernet ciphertext; the cache holds the ciphertext and ``get`` decrypts on the
read — "decrypted at call time only" (§7). An undecryptable value (``FERNET_KEY`` rotated) resolves
as ``MISSING``, so the key falls back to env/default instead of erroring.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.core.config import MISSING, ConfigResolver, get_config

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 30.0


def _decrypt(ciphertext: Any) -> Any:
    """Plaintext for a stored secret, or ``MISSING`` when it cannot be decrypted.

    Imported lazily: the vault pulls in the DB models, and this module is imported during config
    bootstrap. Never raises and never logs the value.
    """
    try:
        from app.deploy.secrets import SecretVault

        plaintext = SecretVault().decrypt(str(ciphertext))
    except Exception:
        logger.warning("config: a stored secret could not be decrypted; falling back to env")
        return MISSING
    return MISSING if plaintext is None else plaintext


class DbSettingProvider:
    """A resolver provider backed by ``PlatformSetting`` docs, via a sync cache."""

    name = "db"

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        #: key -> (stored value, whether it is Fernet ciphertext)
        self._cache: dict[str, tuple[Any, bool]] = {}
        self._loaded_at: float | None = None
        self._ttl = ttl_seconds

    # -- the sync resolver interface --------------------------------------------------------

    def get(self, key: str) -> Any:
        """Return the admin override for ``key`` from the cache, or ``MISSING``.

        A secret is decrypted here — on the read — rather than being held in plaintext memory.
        """
        entry = self._cache.get(key)
        if entry is None:
            return MISSING
        value, encrypted = entry
        return _decrypt(value) if encrypted else value

    def is_overridden(self, key: str) -> bool:
        """Whether an admin override exists, without decrypting it."""
        return key in self._cache

    # -- async cache management -------------------------------------------------------------

    async def load(self) -> None:
        """Rebuild the cache from Mongo. Never raises — a DB problem leaves the last cache alone."""
        try:
            from app.db.repos import PlatformSettingRepo

            docs = await PlatformSettingRepo().all()
            # Only admin-editable keys are applied, so a stray/renamed DB row can't shadow a field
            # the code no longer reads; `locked` keys are refused on write but skipped here too, so
            # a hand-edited row can never shadow the bootstrap keys (§7).
            from app.core.config_registry import REGISTRY

            self._cache = {
                d.key: (d.value, bool(d.encrypted))
                for d in docs
                if d.key in REGISTRY and not REGISTRY[d.key].locked
            }
            self._loaded_at = time.monotonic()
        except Exception:
            logger.warning("config: DB layer unavailable; serving env/default", exc_info=True)

    async def ensure_fresh(self, ttl: float | None = None) -> None:
        """Reload if the cache has never loaded or has aged past its TTL."""
        window = self._ttl if ttl is None else ttl
        if self._loaded_at is None or (time.monotonic() - self._loaded_at) >= window:
            await self.load()

    def invalidate(self) -> None:
        """Force the next :meth:`ensure_fresh` to reload (used right before an explicit load)."""
        self._loaded_at = None


_provider: DbSettingProvider | None = None


def get_db_provider() -> DbSettingProvider | None:
    """The installed DB provider, or ``None`` when the admin layer is not wired up (e.g. a test)."""
    return _provider


async def install_db_provider(
    resolver: ConfigResolver | None = None, *, ttl_seconds: float = DEFAULT_TTL_SECONDS
) -> DbSettingProvider:
    """Create, load and prepend the DB provider to the resolver chain.

    Safe to call again after ``reset_config()`` re-created the resolver: the same provider instance
    is re-attached (``add_provider`` is idempotent) rather than duplicated.
    """
    global _provider
    target = resolver or get_config()
    if _provider is None:
        _provider = DbSettingProvider(ttl_seconds=ttl_seconds)
    target.add_provider(_provider)
    await _provider.load()
    return _provider


def reset_db_provider() -> None:
    """Drop the installed provider (test helper; also correct after a full config reset)."""
    global _provider
    _provider = None


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "DbSettingProvider",
    "get_db_provider",
    "install_db_provider",
    "reset_db_provider",
]
