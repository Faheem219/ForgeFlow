"""BFCL port: the request shape and the AST checker must match the reference harness."""

from __future__ import annotations

from typing import Any

from app.eval.bench.bfcl import PYTHON_HINT, ast_check, standardize_string, to_tools
from app.eval.bench.datasets import BfclCase

AREA: dict[str, Any] = {
    "name": "geometry.area",
    "description": "Area of a shape.",
    "parameters": {
        "type": "dict",
        "properties": {
            "base": {"type": "integer", "description": "Base."},
            "height": {"type": "float", "description": "Height."},
            "unit": {"type": "string", "description": "Unit."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags."},
        },
        "required": ["base", "height"],
    },
}
TRUTH = {"geometry.area": {"base": [10], "height": [5.0], "unit": ["cm", ""], "tags": [""]}}


def _case(category: str = "simple", truth: list[dict[str, Any]] | None = None) -> BfclCase:
    return BfclCase(
        id=f"{category}_0",
        category=category,
        messages=[{"role": "user", "content": "q"}],
        functions=[AREA],
        ground_truth=[TRUTH] if truth is None else truth,
    )


def test_tools_are_converted_like_bfcl_does_for_openai_style_function_calling() -> None:
    (tool,) = to_tools([AREA])

    assert tool["name"] == "geometry_area"  # "." is illegal in OpenAI function names
    assert tool["description"].endswith(PYTHON_HINT)
    schema = tool["input_schema"]
    assert schema["type"] == "object"
    assert schema["properties"]["height"]["type"] == "number"
    assert schema["properties"]["height"]["description"].endswith("This is a float type value.")
    assert AREA["parameters"]["type"] == "dict"  # the dataset itself is never mutated


def test_a_correct_call_passes_and_an_int_is_accepted_for_a_float() -> None:
    assert ast_check(_case(), [{"geometry_area": {"base": 10, "height": 5}}])["valid"]


def test_strings_are_compared_with_bfcls_normalisation() -> None:
    assert standardize_string("New York, NY") == standardize_string("new york ny")
    assert ast_check(_case(), [{"geometry_area": {"base": 10, "height": 5.0, "unit": "CM"}}])[
        "valid"
    ]


def test_wrong_values_missing_required_and_unexpected_parameters_fail() -> None:
    assert not ast_check(_case(), [{"geometry_area": {"base": 11, "height": 5.0}}])["valid"]
    assert not ast_check(_case(), [{"geometry_area": {"base": 10}}])["valid"]
    assert not ast_check(_case(), [{"geometry_area": {"base": 10, "height": 5.0, "x": 1}}])["valid"]


def test_a_wrong_type_fails_even_when_the_value_looks_right() -> None:
    assert not ast_check(_case(), [{"geometry_area": {"base": "10", "height": 5.0}}])["valid"]


def test_an_optional_parameter_without_an_empty_option_must_be_provided() -> None:
    truth = [{"geometry.area": {"base": [10], "height": [5.0], "unit": ["cm"]}}]
    assert not ast_check(_case(truth=truth), [{"geometry_area": {"base": 10, "height": 5.0}}])[
        "valid"
    ]


def test_parallel_calls_match_in_any_order_and_need_the_right_count() -> None:
    truth = [
        {"geometry.area": {"base": [1], "height": [1.0]}},
        {"geometry.area": {"base": [2], "height": [2.0]}},
    ]
    case = _case("parallel", truth)
    swapped = [
        {"geometry_area": {"base": 2, "height": 2.0}},
        {"geometry_area": {"base": 1, "height": 1.0}},
    ]

    assert ast_check(case, swapped)["valid"]
    assert not ast_check(case, swapped[:1])["valid"]


def test_irrelevance_passes_only_when_nothing_is_called() -> None:
    case = _case("irrelevance", [])
    assert ast_check(case, [])["valid"]
    assert not ast_check(case, [{"geometry_area": {"base": 1, "height": 1.0}}])["valid"]


def test_no_call_on_a_relevant_case_fails() -> None:
    assert not ast_check(_case(), [])["valid"]
