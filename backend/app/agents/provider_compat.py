"""Provider-agnostic request compatibility (phase-57).

:class:`~app.agents.anthropic_client.MessageRequest` is **Anthropic-shaped by construction**, and
``build_openai_params`` translates it into a Chat Completions body. That translation encodes one
vendor's spelling of the schema — which breaks the moment an endpoint disagrees:

    400 Unsupported parameter: 'max_tokens' is not supported with this model.
        Use 'max_completion_tokens' instead.

This module makes the transport adapt instead of fail. It is deliberately **pure and I/O-free** —
every decision is a function of the provider's own error and the body we sent — so the whole
mechanism is unit-tested without a network, an API key, or an SDK client.

Two halves, per the user's decision:

- **Reactive** (:func:`diagnose_param_error`) — the load-bearing half. The provider's error is the
  source of truth: OpenAI-shaped bodies carry ``error.param`` and ``error.code``, so the offending
  parameter and often its replacement are stated outright. This is what makes the transport work
  against models that did not exist when this was written — a static table is a guess about the
  future, and being wrong about the future is exactly how the reported bug arrived.
- **Seed** (:func:`seed_repairs`) — a deliberately tiny table for known families, purely to spare a
  wasted first call. It is a *starting guess* and never a gate: a wrong seed costs one reactive
  correction, so the table falling behind can no longer break anything.

The safety property is :func:`diagnose_param_error` returning ``None`` for anything it cannot
attribute to a parameter we actually sent. An unattributable 400 stays fatal and fast, rather than
becoming a blind retry that masks a genuine bug in our own request construction.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class Adaptation(StrEnum):
    """What to do to the request so the provider will accept it."""

    rename = "rename"  # the parameter moved: same value, new key
    drop = "drop"  # the parameter is not supported at all
    developer_role = "developer_role"  # the system prompt must be a `developer` message
    no_stream = "no_stream"  # this model/org refuses streaming


@dataclass(frozen=True)
class Repair:
    """One adaptation to apply to a Chat Completions body."""

    adaptation: Adaptation
    param: str = ""
    replacement: str | None = None  # `rename` only
    reason: str = ""  # the provider's own words — logged, and stored with the memo

    @property
    def signature(self) -> tuple[str, str]:
        """Identity for novelty checks: applying the same repair twice means it did not work."""
        return (str(self.adaptation), self.param)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adaptation": str(self.adaptation),
            "param": self.param,
            "replacement": self.replacement,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Repair | None:
        """Parse one persisted repair. Returns ``None`` for anything unrecognised — a stale or
        hand-edited memo must degrade, never raise on the model call path."""
        raw = str(data.get("adaptation", ""))
        if raw not in tuple(Adaptation):
            return None
        replacement = data.get("replacement")
        return cls(
            adaptation=Adaptation(raw),
            param=str(data.get("param", "")),
            replacement=str(replacement) if isinstance(replacement, str) else None,
            reason=str(data.get("reason", "")),
        )


# --------------------------------------------------------------------- diagnosis (reactive)

#: OpenAI's phrasing when a parameter was renamed: "Use 'max_completion_tokens' instead."
_REPLACEMENT = re.compile(r"use\s+['\"`]?([A-Za-z_][A-Za-z0-9_]*)['\"`]?\s+instead", re.IGNORECASE)

#: `error.code` values that mean "this parameter is not accepted here".
_PARAM_CODES = frozenset(
    {
        "unsupported_parameter",
        "unknown_parameter",
        "unsupported_value",
        "invalid_parameter",
        "extra_forbidden",
    }
)

#: Message markers that mean the same thing on endpoints that do not populate `error.code`
#: (Mistral answers `422 extra_forbidden` / "Extra inputs are not permitted"; Azure serverless and
#: several self-hosted gateways phrase it their own way).
_PARAM_MARKERS: tuple[str, ...] = (
    "unsupported parameter",
    "unsupported_parameter",
    "unknown parameter",
    "unknown_parameter",
    "unsupported value",
    "unrecognized",
    "unrecognised",
    "not supported with this model",
    "is not supported",
    "not permitted",
    "extra_forbidden",
    "extra inputs are not permitted",
    "additional properties",
    "invalid_request_error",
)

#: Markers for "this model/organization may not stream", which is a *capability* refusal rather than
#: a parameter-shape problem, so it maps to the non-streaming fallback.
_NO_STREAM_MARKERS: tuple[str, ...] = (
    "must be verified to stream",
    "stream is not supported",
    "streaming is not supported",
    "does not support streaming",
    "unsupported_value: 'stream'",
)

#: Sent as a message-shape choice, not a body parameter: a 400 naming these means the model wants
#: the system prompt delivered as a `developer` message instead.
_ROLE_MARKERS: tuple[str, ...] = (
    "developer",
    "'system' is not supported",
    "system messages are not supported",
    "unsupported role",
)


def _error_body(exc: BaseException) -> dict[str, Any]:
    """The provider's structured error payload, however the SDK surfaced it."""
    for attribute in ("body", "response"):
        raw = getattr(exc, attribute, None)
        if isinstance(raw, dict):
            error = raw.get("error")
            return error if isinstance(error, dict) else raw
        text = getattr(raw, "text", None)
        if isinstance(text, str) and text.strip().startswith("{"):
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, dict):
                error = parsed.get("error")
                return error if isinstance(error, dict) else parsed
    return {}


def _status_of(exc: BaseException) -> int:
    return int(getattr(exc, "status_code", 0) or getattr(exc, "status", 0) or 0)


def diagnose_param_error(exc: BaseException, params: dict[str, Any]) -> Repair | None:
    """The repair that would make ``params`` acceptable, or ``None`` if this is not repairable.

    ``params`` is required, not optional: a parameter named in an error but **absent from the body
    we sent** is not ours to fix, and treating it as ours would let a provider's prose send us
    editing a request that was never the problem.
    """
    status = _status_of(exc)
    # Only client-side rejections are repairable. A 5xx is an outage (the transient ladder's job)
    # and a 200-with-a-bad-body is a stream failure, not a request-shape failure.
    if status and not (400 <= status < 500):
        return None

    body = _error_body(exc)
    text = str(exc)
    lowered = text.lower()
    code = str(body.get("code") or body.get("type") or "").lower()
    message = str(body.get("message") or text)

    looks_like_param_error = code in _PARAM_CODES or any(m in lowered for m in _PARAM_MARKERS)

    # Streaming refusals first: they name `stream`, so a plain rename/drop reading would strip the
    # one parameter the loop cannot do without.
    if any(marker in lowered for marker in _NO_STREAM_MARKERS):
        return Repair(Adaptation.no_stream, param="stream", reason=message)

    named = str(body.get("param") or "").strip()
    # Providers report nested params as paths ("messages.0.role", "body.max_tokens").
    if named:
        named = named.split(".")[-1]
    if not named:
        named = _param_from_text(lowered, params)
    if not named:
        return None

    if named == "stream" and looks_like_param_error:
        return Repair(Adaptation.no_stream, param="stream", reason=message)

    # A complaint about the message *role* is a shape problem, not a body parameter — `messages`
    # itself must not be dropped.
    if named in ("role", "messages") or (
        "role" in lowered and any(marker in lowered for marker in _ROLE_MARKERS)
    ):
        if any(marker in lowered for marker in _ROLE_MARKERS):
            return Repair(Adaptation.developer_role, param="messages", reason=message)
        return None

    if named not in _sent_params(params):
        return None  # named something we never sent — not our request to repair
    if not looks_like_param_error:
        return None

    # Search BOTH the structured message and the exception text. SDKs differ over which carries the
    # full sentence — some truncate `error.message` to a summary and keep the detail in `str(exc)` —
    # and mistaking a rename for a drop would silently discard the parameter's value.
    for candidate in (message, text):
        match = _REPLACEMENT.search(candidate)
        if match is not None and match.group(1) != named:
            return Repair(
                Adaptation.rename, param=named, replacement=match.group(1), reason=message
            )
    return Repair(Adaptation.drop, param=named, reason=message)


def _sent_params(params: dict[str, Any]) -> set[str]:
    """Every parameter name on the wire: the top-level keys plus those inside ``extra_body``.

    The OpenAI SDK's ``create()`` has a closed signature, so anything provider-specific
    (OpenRouter's ``provider`` routing, the phase-64 ``reasoning`` hint) travels in ``extra_body``
    and is merged into the JSON body — a 400 names it by its merged name, not by ``extra_body``.
    """
    extra = params.get("extra_body")
    nested = set(extra) if isinstance(extra, dict) else set()
    return (set(params) - {"extra_body"}) | nested


def _param_from_text(lowered: str, params: dict[str, Any]) -> str:
    """The offending parameter read out of the message — matched only against keys we sent.

    Longest first, so ``max_tokens`` is never mistaken for a substring of another key we sent.
    """
    for key in sorted(_sent_params(params), key=len, reverse=True):
        if key.lower() in lowered:
            return key
    return ""


# --------------------------------------------------------------------- application


def apply_repairs(params: dict[str, Any], repairs: Iterable[Repair]) -> dict[str, Any]:
    """A copy of ``params`` with every repair applied. The only writer of the request body."""
    out = dict(params)
    for repair in repairs:
        if repair.adaptation is Adaptation.rename:
            # Rename, never drop: dropping an output cap would make the call succeed while
            # silently uncapping spend — a cost regression disguised as a fix (D13).
            if repair.param in out and repair.replacement:
                out[repair.replacement] = out.pop(repair.param)
        elif repair.adaptation is Adaptation.drop:
            out.pop(repair.param, None)
            extra = out.get("extra_body")
            if isinstance(extra, dict) and repair.param in extra:
                remaining = {k: v for k, v in extra.items() if k != repair.param}
                if remaining:
                    out["extra_body"] = remaining
                else:
                    out.pop("extra_body", None)
        elif repair.adaptation is Adaptation.developer_role:
            out["messages"] = _as_developer_role(out.get("messages") or [])
        elif repair.adaptation is Adaptation.no_stream:
            out["stream"] = False
            # The usage trailer is a streaming-only option; a non-streamed call reports usage
            # on the response itself.
            out.pop("stream_options", None)
    return out


def _as_developer_role(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deliver the system prompt as a ``developer`` message, leaving the transcript untouched."""
    return [{**m, "role": "developer"} if m.get("role") == "system" else m for m in messages]


# --------------------------------------------------------------------- seed table

#: Model-id patterns whose request shape is already known to differ. Deliberately tiny, and
#: explicitly allowed to be wrong or incomplete — a bad guess costs exactly one reactive
#: correction, which is why this table lagging behind new releases can no longer break a build.
_SEED_RULES: tuple[tuple[re.Pattern[str], tuple[Repair, ...]], ...] = (
    (
        # OpenAI's reasoning families: `max_tokens` was replaced by `max_completion_tokens`, and
        # sampling parameters are fixed at their defaults.
        re.compile(r"(^|/)(o[1-4]|gpt-5)", re.IGNORECASE),
        (
            Repair(
                Adaptation.rename,
                param="max_tokens",
                replacement="max_completion_tokens",
                reason="reasoning-family models take max_completion_tokens",
            ),
            Repair(Adaptation.drop, param="temperature", reason="fixed at the default"),
            Repair(Adaptation.drop, param="top_p", reason="fixed at the default"),
        ),
    ),
)


def seed_repairs(model: str) -> list[Repair]:
    """Known-good starting repairs for ``model`` — a guess, corrected reactively if wrong."""
    for pattern, repairs in _SEED_RULES:
        if pattern.search(model or ""):
            return list(repairs)
    return []


# --------------------------------------------------------------------- the memo


def memo_key(endpoint: str, model: str) -> str:
    """Learned repairs are keyed by **(endpoint, model)**, not endpoint alone.

    ``stream_options`` was an endpoint-wide schema fact, so the memo it replaces could be keyed by
    base URL. ``max_tokens`` is a *model* fact: the same base URL answers differently for ``gpt-4o``
    and ``gpt-5*``, so the coarser key would teach one model's quirk to another.
    """
    return f"{endpoint or 'default'}|{model or ''}"


class ParamMemo:
    """The learned request shape per (endpoint, model). In-process and synchronous, so the hot
    path never awaits; persisted separately so it survives a restart."""

    def __init__(self) -> None:
        self._learned: dict[str, list[Repair]] = {}

    def get(self, endpoint: str, model: str) -> list[Repair]:
        return list(self._learned.get(memo_key(endpoint, model), ()))

    def learn(self, endpoint: str, model: str, repair: Repair) -> bool:
        """Record ``repair``. Returns whether it was new (a duplicate means it did not work)."""
        entries = self._learned.setdefault(memo_key(endpoint, model), [])
        if any(existing.signature == repair.signature for existing in entries):
            return False
        entries.append(repair)
        return True

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return {
            key: [r.to_dict() for r in repairs] for key, repairs in self._learned.items() if repairs
        }

    def load(self, data: Any) -> None:
        """Replace the memo from persisted JSON. Anything unrecognised is skipped, never raised:
        a corrupt memo must degrade to "learn it again", never break a model call."""
        self._learned = dict(_parse_entries(data))

    def merge(self, data: Any) -> None:
        """Fold persisted entries in **without discarding** what this process has learned.

        The transport is resolved per turn (so an admin can switch providers with no restart), and
        each construction re-reads the setting. Replacing would throw away a repair learned seconds
        ago but not yet persisted, and the call would fail again; merging keeps both. Persisted
        entries lose to in-memory ones, which are by definition at least as fresh.
        """
        for key, repairs in _parse_entries(data).items():
            self._learned.setdefault(key, repairs)


def _parse_entries(data: Any) -> dict[str, list[Repair]]:
    """``{key: [Repair]}`` from persisted JSON, skipping anything unrecognised."""
    out: dict[str, list[Repair]] = {}
    if not isinstance(data, dict):
        return out
    for key, raw in data.items():
        if not isinstance(key, str) or not isinstance(raw, list):
            continue
        repairs = [
            repair
            for item in raw
            if isinstance(item, dict) and (repair := Repair.from_dict(item)) is not None
        ]
        if repairs:
            out[key] = repairs
    return out


def parse_quirks(raw: str) -> dict[str, Any]:
    """The persisted quirk map, or ``{}`` for blank/corrupt input (logged, never raised)."""
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("llm_param_quirks is not valid JSON; ignoring it and re-learning")
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def persist_quirks(memo: ParamMemo) -> None:
    """Write the learned shape to the ``llm_param_quirks`` setting so it survives a restart.

    **Fail-soft by design.** The in-process memo already holds the repair, so a failed write costs
    only re-learning after the next restart — persistence is an optimisation, never a dependency,
    and a Mongo hiccup must not be able to break model calls. This mirrors ``config_db``'s stated
    posture.

    Deliberately **not** routed through :class:`~app.core.config_admin.ConfigAdminService.set`: that
    path writes an admin audit entry, and a repair the transport taught itself is not an
    administrator action.
    """
    try:
        from app.core.config_db import get_db_provider
        from app.db.models.enums import SettingCategory
        from app.db.repos import PlatformSettingRepo

        await PlatformSettingRepo().upsert(
            "llm_param_quirks",
            json.dumps(memo.snapshot()),
            SettingCategory.models,
            updated_by=None,  # learned by the system, not set by an admin
        )
        provider = get_db_provider()
        if provider is not None:
            await provider.load()  # make it live without a restart
    except Exception as exc:  # noqa: BLE001 - persistence must never break a model call
        logger.info("could not persist learned provider quirks (%s); they stay in memory", exc)


__all__ = [
    "Adaptation",
    "ParamMemo",
    "Repair",
    "apply_repairs",
    "diagnose_param_error",
    "memo_key",
    "parse_quirks",
    "persist_quirks",
    "seed_repairs",
]
