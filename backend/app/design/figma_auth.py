"""Figma auth (phase-18) — a single static access token, no OAuth refresh (unlike Stitch).

Since phase-34 the token is read through the encrypted vault: a platform-scoped vault entry wins
over the plaintext ``FIGMA_TOKEN`` in config, and the value is decrypted **at call time only**.
**Never logged.**
"""

from __future__ import annotations

from beanie import PydanticObjectId

from app.db.models.enums import CredentialKind
from app.deploy.secrets import resolve_secret
from app.design.figma_errors import FigmaErrorKind, figma_error


async def figma_token_present(user_id: PydanticObjectId | None = None) -> bool:
    return bool(await resolve_secret(CredentialKind.figma, user_id))


async def get_figma_token(user_id: PydanticObjectId | None = None) -> str:
    """Return the effective Figma token (BYO > platform vault > config), or raise an auth error."""
    token = await resolve_secret(CredentialKind.figma, user_id)
    if not token:
        raise figma_error(FigmaErrorKind.auth, "Figma token is not configured")
    return token
