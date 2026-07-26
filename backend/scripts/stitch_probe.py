"""Ask the live Stitch endpoint what it actually exposes.

The Stitch MCP tool/argument names are not contractually documented, so when a design call comes
back with ``INVALID_ARGUMENT`` this is the fastest way to see the truth: it performs the real
handshake with your configured credential and prints every tool with its input schema.

    cd backend && uv run --env-file ../.env python -m scripts.stitch_probe
    cd backend && uv run --env-file ../.env python -m scripts.stitch_probe --generate "a todo app"

``--generate`` additionally runs a create-project + generate round trip and dumps the raw result,
which is what you want if the *response* shape (not the request) is the problem. It consumes one
Stitch generation from the monthly quota.

Nothing here is a secret: the credential is read through the config resolver (admin panel > env)
and only ever sent as a header — it is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# Allow running as a bare file (`python scripts/stitch_probe.py`) by putting backend/ on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_config  # noqa: E402
from app.core.errors import ProviderError  # noqa: E402
from app.design.stitch import HttpStitchClient  # noqa: E402
from app.design.stitch_auth import StitchAuth, stitch_credentials_present  # noqa: E402


def _use_utf8_stdout() -> None:
    """Windows consoles default to cp1252, which cannot encode the arrows/→ this script prints.

    Without this the probe dies mid-report with a UnicodeEncodeError — on the exact platform where
    someone is most likely to be running it because a design call just failed.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _dump(label: str, value: Any) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(value, indent=2, ensure_ascii=False)[:8000])


async def probe(prompt: str | None) -> int:
    if not await stitch_credentials_present():
        print(
            "No Stitch credentials configured.\n"
            "Set STITCH_API_KEY (Stitch → Settings → API keys) in .env, "
            "or in /admin → Design providers."
        )
        return 2

    print(f"endpoint: {get_config().get('stitch_mcp_url')}")
    client = HttpStitchClient()
    headers = await StitchAuth().headers()
    print(f"auth header: {', '.join(sorted(headers))}")

    try:
        # The handshake also populates the tool schemas.
        await client._ensure_session(headers)  # noqa: SLF001 - a diagnostic, by design
    except ProviderError as exc:
        print(f"\nhandshake failed: {exc.message}")
        return 1

    schemas = client._schemas  # noqa: SLF001
    if not schemas:
        print("\nThe server did not answer tools/list — nothing to adapt to.")
        return 1

    print(f"\n{len(schemas)} tools exposed: {', '.join(sorted(schemas))}")
    for name in sorted(schemas):
        schema = schemas[name]
        properties = schema.get("properties")
        required = schema.get("required")
        print(f"\n--- {name}")
        print(f"    accepts : {sorted(properties) if isinstance(properties, dict) else '?'}")
        print(f"    requires: {sorted(map(str, required)) if isinstance(required, list) else '?'}")

    print("\nForgeFlow will call:")
    for canonical in ("create_project", "generate_screen_from_text", "edit_screens", "get_screen"):
        resolved = client._tool_name(canonical)  # noqa: SLF001
        mark = "ok" if resolved in schemas else "MISSING"
        print(f"    {canonical:<28} → {resolved:<28} [{mark}]")

    if prompt:
        print(f"\nGenerating (consumes one quota unit): {prompt!r}")
        payload = await client.text_to_ui(prompt, headers=headers)
        _dump("design payload", payload.model_dump())

    return 0


def main() -> None:
    _use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generate",
        metavar="PROMPT",
        help="also run a real generation with this prompt (spends one generation)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(probe(args.generate)))


if __name__ == "__main__":
    main()
