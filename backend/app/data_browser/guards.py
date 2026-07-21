"""Query guardrails for the data browser (phase-41) — pure, so they are exhaustively testable.

The data browser hands user-supplied filters to MongoDB, which makes it the one place in ForgeFlow
where a request could ask the database to *execute* something. The defence is an **allowlist**: only
comparison/logical operators known to be data-only are accepted, and everything else — including
every JavaScript-evaluating operator (``$where``, ``$function``, ``$accumulator``) — is refused by
default rather than blacklisted one at a time. A new MongoDB release adding another dangerous
operator therefore fails closed.

Alongside that: collection names are constrained (no ``system.*``, no ``$``), filters are bounded in
depth and size, sorts are restricted to plain field→direction pairs, and pages are capped. Together
these keep one project's browsing from becoming a denial-of-service on a shared cluster.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.errors import UserError

#: Data-only operators. Anything absent is refused — see the module docstring on failing closed.
ALLOWED_OPERATORS = frozenset(
    {
        # comparison
        "$eq",
        "$ne",
        "$gt",
        "$gte",
        "$lt",
        "$lte",
        "$in",
        "$nin",
        # logical
        "$and",
        "$or",
        "$nor",
        "$not",
        # element
        "$exists",
        "$type",
        # array
        "$all",
        "$elemMatch",
        "$size",
        # evaluation (data-only subset)
        "$regex",
        "$options",
    }
)

#: Named explicitly so the error can say *why*, and so the intent is documented in code.
JS_OPERATORS = frozenset({"$where", "$function", "$accumulator", "$expr", "$jsonSchema"})

MAX_FILTER_DEPTH = 6
MAX_FILTER_KEYS = 40
MAX_REGEX_LENGTH = 200
MAX_SORT_KEYS = 3
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
#: Server-side time budget: a pathological query is cut off rather than pinning the cluster.
QUERY_MAX_TIME_MS = 5_000

_COLLECTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")


def validate_collection(name: str) -> str:
    """Constrain a collection name to something ordinary and non-internal.

    ``system.*`` is MongoDB's own namespace (indexes, users, views) — browsing it would leak
    database internals, so it is refused even inside the project's own database.
    """
    name = (name or "").strip()
    if not name:
        raise UserError("A collection name is required")
    if not _COLLECTION_RE.match(name):
        raise UserError(
            "Invalid collection name — use letters, numbers, dots, dashes or underscores"
        )
    if name.startswith("system."):
        raise UserError("System collections are not browsable")
    if "$" in name:
        raise UserError("Collection names must not contain '$'")
    return name


def _check_value(value: Any, depth: int, counter: list[int]) -> Any:
    if depth > MAX_FILTER_DEPTH:
        raise UserError(f"Filter is nested too deeply (max {MAX_FILTER_DEPTH})")

    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            counter[0] += 1
            if counter[0] > MAX_FILTER_KEYS:
                raise UserError(f"Filter has too many conditions (max {MAX_FILTER_KEYS})")
            if not isinstance(key, str):
                raise UserError("Filter keys must be strings")
            if key.startswith("$"):
                if key in JS_OPERATORS:
                    raise UserError(
                        f"The {key} operator is not allowed — it can execute code on the database"
                    )
                if key not in ALLOWED_OPERATORS:
                    raise UserError(f"The {key} operator is not supported here")
                if key == "$regex" and isinstance(item, str) and len(item) > MAX_REGEX_LENGTH:
                    raise UserError(f"Regex is too long (max {MAX_REGEX_LENGTH} characters)")
            elif "." in key and key.startswith("."):
                raise UserError("Invalid field path in filter")
            cleaned[key] = _check_value(item, depth + 1, counter)
        return cleaned

    if isinstance(value, list):
        return [_check_value(item, depth + 1, counter) for item in value]

    return value


def sanitize_filter(raw: Any) -> dict[str, Any]:
    """Validate a client filter, returning it unchanged when safe.

    Nothing is silently stripped: a rejected filter raises, so the user learns why rather than
    quietly getting different results than they asked for.
    """
    if raw is None or raw == "":
        return {}
    if not isinstance(raw, dict):
        raise UserError("Filter must be a JSON object")
    checked = _check_value(raw, depth=1, counter=[0])
    assert isinstance(checked, dict)  # noqa: S101 - _check_value preserves the dict shape
    return checked


def sanitize_sort(raw: Any) -> list[tuple[str, int]]:
    """Accept ``{"field": 1|-1}`` (or a list of pairs); reject anything else."""
    if raw is None or raw == "":
        return []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = [(pair[0], pair[1]) for pair in raw if isinstance(pair, (list, tuple)) and pair]
    else:
        raise UserError('Sort must be an object like {"field": -1}')

    if len(items) > MAX_SORT_KEYS:
        raise UserError(f"Sort by at most {MAX_SORT_KEYS} fields")

    sort: list[tuple[str, int]] = []
    for field, direction in items:
        if not isinstance(field, str) or not field or field.startswith("$"):
            raise UserError("Sort fields must be plain field names")
        if direction not in (1, -1, "1", "-1", "asc", "desc"):
            raise UserError("Sort direction must be 1 (ascending) or -1 (descending)")
        normalized = -1 if direction in (-1, "-1", "desc") else 1
        sort.append((field, normalized))
    return sort


def clamp_page(page: Any, limit: Any) -> tuple[int, int]:
    """Coerce paging into sane bounds — an unbounded page is a denial-of-service."""
    try:
        page_num = max(1, int(page if page not in (None, "") else 1))
    except (TypeError, ValueError):
        raise UserError("Page must be a positive integer") from None
    try:
        size = int(limit if limit not in (None, "") else DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        raise UserError("Limit must be an integer") from None
    return page_num, max(1, min(size, MAX_PAGE_SIZE))


__all__ = [
    "ALLOWED_OPERATORS",
    "DEFAULT_PAGE_SIZE",
    "JS_OPERATORS",
    "MAX_PAGE_SIZE",
    "QUERY_MAX_TIME_MS",
    "clamp_page",
    "sanitize_filter",
    "sanitize_sort",
    "validate_collection",
]
