"""Query guardrails (phase-41): no server-side code execution, no unbounded queries.

The data browser is the one surface that hands user input to MongoDB as a *query*, so the operator
allowlist is the security control that matters most here. The key property is that it fails
**closed**: an operator nobody has heard of yet is rejected because it is absent from the allowlist,
not because someone remembered to blacklist it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import UserError
from app.data_browser.guards import (
    ALLOWED_OPERATORS,
    DEFAULT_PAGE_SIZE,
    JS_OPERATORS,
    MAX_PAGE_SIZE,
    clamp_page,
    sanitize_filter,
    sanitize_sort,
    validate_collection,
)
from app.data_browser.service import DataBrowserService
from tests.data_browser.conftest import make_project

# --------------------------------------------------------------------- operators


@pytest.mark.parametrize("operator", sorted(JS_OPERATORS))
def test_code_executing_operators_are_rejected(operator: str) -> None:
    with pytest.raises(UserError) as exc:
        sanitize_filter({operator: "function() { return true; }"})
    assert operator in str(exc.value)


def test_where_is_rejected_however_deeply_it_is_buried() -> None:
    """A nested $where is the interesting attack — a shallow check would miss it."""
    payload = {"$and": [{"ok": True}, {"$or": [{"x": 1}, {"$where": "sleep(5000)"}]}]}

    with pytest.raises(UserError, match=r"\$where"):
        sanitize_filter(payload)


def test_an_unknown_operator_fails_closed() -> None:
    """The property that keeps this safe as MongoDB grows new operators."""
    assert "$brandNewOperator" not in ALLOWED_OPERATORS
    with pytest.raises(UserError, match="not supported"):
        sanitize_filter({"$brandNewOperator": 1})


def test_ordinary_filters_pass_through_unchanged() -> None:
    query = {"done": True, "order": {"$gte": 2}, "tags": {"$in": ["a", "b"]}}
    assert sanitize_filter(query) == query


def test_nested_logical_filters_are_allowed() -> None:
    query = {"$or": [{"done": False}, {"order": {"$lt": 3}}]}
    assert sanitize_filter(query) == query


def test_a_non_object_filter_is_rejected() -> None:
    with pytest.raises(UserError, match="JSON object"):
        sanitize_filter("done")


def test_an_empty_filter_means_everything() -> None:
    assert sanitize_filter(None) == {} and sanitize_filter("") == {}


# --------------------------------------------------------------------- size / cost bounds


def test_a_deeply_nested_filter_is_rejected() -> None:
    payload: dict[str, Any] = {"a": 1}
    for _ in range(10):
        payload = {"$and": [payload]}

    with pytest.raises(UserError, match="nested too deeply"):
        sanitize_filter(payload)


def test_a_filter_with_too_many_conditions_is_rejected() -> None:
    with pytest.raises(UserError, match="too many conditions"):
        sanitize_filter({f"field{i}": i for i in range(100)})


def test_an_oversized_regex_is_rejected() -> None:
    with pytest.raises(UserError, match="Regex is too long"):
        sanitize_filter({"title": {"$regex": "a" * 500}})


def test_a_reasonable_regex_is_allowed() -> None:
    assert sanitize_filter({"title": {"$regex": "^todo", "$options": "i"}})


def test_page_size_is_capped() -> None:
    assert clamp_page(1, 10_000) == (1, MAX_PAGE_SIZE)
    assert clamp_page(1, None) == (1, DEFAULT_PAGE_SIZE)
    assert clamp_page(0, 10) == (1, 10)  # pages start at 1
    assert clamp_page(-5, -5) == (1, 1)


def test_a_non_numeric_page_is_a_user_error() -> None:
    with pytest.raises(UserError, match="positive integer"):
        clamp_page("abc", 10)


# --------------------------------------------------------------------- sort / collection names


def test_sort_accepts_field_direction_pairs() -> None:
    assert sanitize_sort({"createdAt": -1}) == [("createdAt", -1)]
    assert sanitize_sort({"name": "asc"}) == [("name", 1)]
    assert sanitize_sort(None) == []


def test_sort_rejects_operators_and_bad_directions() -> None:
    with pytest.raises(UserError, match="plain field names"):
        sanitize_sort({"$where": 1})
    with pytest.raises(UserError, match="1 \\(ascending\\)"):
        sanitize_sort({"name": "sideways"})


def test_sort_is_limited_to_a_few_keys() -> None:
    with pytest.raises(UserError, match="at most"):
        sanitize_sort({"a": 1, "b": 1, "c": 1, "d": 1})


@pytest.mark.parametrize(
    "name",
    ["system.indexes", "system.users", "", "   ", "with$dollar", "1leading-digit", "a" * 200],
)
def test_bad_collection_names_are_rejected(name: str) -> None:
    with pytest.raises(UserError):
        validate_collection(name)


@pytest.mark.parametrize("name", ["todos", "user_profiles", "orders.archive", "a-b"])
def test_ordinary_collection_names_are_accepted(name: str) -> None:
    assert validate_collection(name) == name


# --------------------------------------------------------------------- end to end
# Only these two need a database; the guards above are pure, so they stay fast.


@pytest.mark.usefixtures("mongo_db")
async def test_the_service_refuses_a_dangerous_query_before_touching_mongo(
    cleanup_app_dbs: list[str],
) -> None:
    project = await make_project()
    cleanup_app_dbs.append(project.app_db_name)

    with pytest.raises(UserError, match="execute code"):
        await DataBrowserService().find(project, "todos", filter={"$where": "true"})


@pytest.mark.usefixtures("mongo_db")
async def test_an_oversized_page_request_is_clamped_not_honoured(
    cleanup_app_dbs: list[str],
) -> None:
    project = await make_project()
    cleanup_app_dbs.append(project.app_db_name)
    service = DataBrowserService()
    for i in range(5):
        await service.insert(project, "todos", {"i": i})

    page = await service.find(project, "todos", limit=100_000)
    assert page.limit == MAX_PAGE_SIZE
