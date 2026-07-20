"""Encrypted credential vault (phase-34, §7 secrets posture, D11 dual-mode creds).

Every provider token ForgeFlow holds — Stitch, Figma, Vercel, Render, a Mongo URI — is read
through this module. The invariants:

- **Encrypted at rest.** Values are Fernet-encrypted with ``FERNET_KEY``; Mongo only ever sees
  ciphertext (``Credential.encrypted_secret``).
- **Decrypted at call time only.** Plaintext exists in-process, for the duration of one call. It
  is never cached, never logged, and never placed on a response model.
- **Metadata is the only public surface.** :class:`CredentialMeta` (kind/scope/created_at/last4)
  is what the API may return; there is deliberately no "read my secret back" path.

Resolution order (D11) — :func:`SecretVault.resolve`::

    BYO credential for this user  >  platform credential in the vault  >  platform value in config

so a user's own token always wins when present, and the platform still works for everyone else.

**Rotating ``FERNET_KEY`` invalidates every stored secret** — ciphertext written under the old key
can no longer be decrypted and must be re-entered. Decryption failures degrade (``None``) rather
than raising, so a rotated key surfaces as "credential missing", not a 500.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from beanie import PydanticObjectId
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_config
from app.core.errors import SystemError, UserError  # noqa: A004 - taxonomy name fixed by the plan
from app.db.models import Credential
from app.db.models.enums import CredentialKind, CredentialScope

logger = logging.getLogger(__name__)

#: Platform fallback: the config key holding each kind's platform-owned value. Consulted only
#: after the vault yields nothing, so an operator can migrate a plaintext env token into the
#: vault without touching call sites.
CONFIG_FALLBACK: dict[CredentialKind, str] = {
    CredentialKind.vercel: "vercel_token",
    CredentialKind.render: "render_api_key",
    CredentialKind.mongo_uri: "mongodb_uri",
    CredentialKind.stitch: "stitch_client_secret",
    CredentialKind.figma: "figma_token",
}


@dataclass(frozen=True)
class CredentialMeta:
    """Everything the API may expose about a stored credential — never the secret itself."""

    kind: CredentialKind
    scope: CredentialScope
    created_at: datetime
    #: Last four characters, so a user can tell two tokens apart without revealing either.
    last4: str | None = None


_fernet_cache: tuple[str, Fernet] | None = None


def _fernet() -> Fernet:
    """The process Fernet, rebuilt whenever the configured key changes."""
    global _fernet_cache
    key = str(get_config().get("fernet_key")).strip()
    if not key:
        raise SystemError("FERNET_KEY is not configured; secrets cannot be stored or read")
    if _fernet_cache is not None and _fernet_cache[0] == key:
        return _fernet_cache[1]
    try:
        fernet = Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise SystemError("FERNET_KEY is not a valid Fernet key") from exc
    _fernet_cache = (key, fernet)
    return fernet


def reset_vault() -> None:
    """Drop the cached Fernet (test helper; also correct after a key rotation)."""
    global _fernet_cache
    _fernet_cache = None


def _last4(secret: str) -> str | None:
    return secret[-4:] if len(secret) >= 4 else None


class SecretVault:
    """Store, read and resolve provider credentials. The only place secrets are decrypted."""

    # -- crypto -----------------------------------------------------------------------------

    def encrypt(self, secret: str) -> str:
        return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str | None:
        """Plaintext, or ``None`` when the ciphertext predates the current ``FERNET_KEY``."""
        try:
            return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError, TypeError):
            # Never log the ciphertext or any fragment of the secret.
            logger.warning("credential could not be decrypted (FERNET_KEY rotated?)")
            return None

    # -- storage ----------------------------------------------------------------------------

    async def put_credential(
        self,
        user_id: PydanticObjectId,
        kind: CredentialKind,
        secret: str,
        scope: CredentialScope = CredentialScope.byo,
    ) -> CredentialMeta:
        """Encrypt and store, replacing any existing credential of the same (user, kind, scope)."""
        value = secret.strip()
        if not value:
            raise UserError("A credential value must not be empty")

        await Credential.find({"user_id": user_id, "kind": kind, "scope": scope}).delete()
        doc = await Credential(
            user_id=user_id,
            kind=kind,
            scope=scope,
            encrypted_secret=self.encrypt(value),
        ).insert()
        logger.info("credential stored", extra={"kind": str(kind), "scope": str(scope)})
        return CredentialMeta(
            kind=doc.kind, scope=doc.scope, created_at=doc.created_at, last4=_last4(value)
        )

    async def get_credential(
        self,
        user_id: PydanticObjectId,
        kind: CredentialKind,
        scope: CredentialScope = CredentialScope.byo,
    ) -> str | None:
        """Decrypt one credential **at call time**. Returns ``None`` when absent/undecryptable."""
        doc = await Credential.find_one({"user_id": user_id, "kind": kind, "scope": scope})
        return self.decrypt(doc.encrypted_secret) if doc is not None else None

    async def list_kinds(self, user_id: PydanticObjectId) -> list[CredentialMeta]:
        """Metadata for a user's credentials — **never** the values."""
        docs = await Credential.find({"user_id": user_id}).sort("+kind").to_list()
        metas: list[CredentialMeta] = []
        for doc in docs:
            plaintext = self.decrypt(doc.encrypted_secret)
            metas.append(
                CredentialMeta(
                    kind=doc.kind,
                    scope=doc.scope,
                    created_at=doc.created_at,
                    last4=_last4(plaintext) if plaintext else None,
                )
            )
        return metas

    async def delete(
        self,
        user_id: PydanticObjectId,
        kind: CredentialKind,
        scope: CredentialScope = CredentialScope.byo,
    ) -> bool:
        """Remove a credential. ``False`` when there was nothing to remove."""
        doc = await Credential.find_one({"user_id": user_id, "kind": kind, "scope": scope})
        if doc is None:
            return False
        await doc.delete()
        logger.info("credential deleted", extra={"kind": str(kind), "scope": str(scope)})
        return True

    # -- resolution -------------------------------------------------------------------------

    async def resolve(
        self, kind: CredentialKind, user_id: PydanticObjectId | None = None
    ) -> str | None:
        """The effective secret for ``kind``: **BYO > platform (vault) > platform (config)**.

        Fail-soft by design: this sits on provider call paths, so a vault/DB problem degrades to
        the configured platform value instead of taking the provider down.
        """
        if user_id is not None:
            byo = await self._safe_lookup({"user_id": user_id, "kind": kind, "scope": "byo"})
            if byo:
                return byo

        platform = await self._safe_lookup({"kind": kind, "scope": "platform"})
        if platform:
            return platform

        config_key = CONFIG_FALLBACK.get(kind)
        if config_key is None:
            return None
        return str(get_config().get(config_key)) or None

    async def platform_secret(self, kind: CredentialKind) -> str | None:
        """A platform-scoped secret straight from the vault (no config fallback). Fail-soft.

        Unlike :meth:`resolve`, this never consults ``CONFIG_FALLBACK`` — a caller that wants the
        platform *vault* value distinct from a config default (e.g. phase-36's app-DB cluster) can
        layer its own precedence.
        """
        return await self._safe_lookup({"kind": kind, "scope": "platform"})

    async def _safe_lookup(self, query: dict[str, object]) -> str | None:
        try:
            doc = await Credential.find_one(query)
        except Exception:  # DB unreachable / Beanie not initialised — fall through to config.
            logger.warning("vault lookup failed; falling back to platform config", exc_info=True)
            return None
        return self.decrypt(doc.encrypted_secret) if doc is not None else None


async def resolve_secret(
    kind: CredentialKind, user_id: PydanticObjectId | None = None
) -> str | None:
    """Module-level shorthand for :meth:`SecretVault.resolve` (the provider-facing entry point)."""
    return await SecretVault().resolve(kind, user_id)


__all__ = [
    "CONFIG_FALLBACK",
    "CredentialMeta",
    "SecretVault",
    "reset_vault",
    "resolve_secret",
]
