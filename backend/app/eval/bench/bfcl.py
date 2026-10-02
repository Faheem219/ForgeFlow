"""BFCL — Berkeley Function-Calling Leaderboard, v3 non-live categories (Python).

Measures agent tool-call accuracy through ForgeFlow's transport: each case's functions are offered
as tools, the model's tool calls come back through the same seam the build agents use, and they are
graded by BFCL's **AST checker**.

The request and the grading are ports of the official harness
(https://github.com/ShishirPatil/gorilla, ``bfcl_eval``, Apache-2.0), Python language path only:

- tools are converted as ``convert_to_tool(..., GORILLA_TO_OPENAPI)`` does for OpenAI-style function
  calling (``.`` → ``_`` in names, ``dict`` → ``object``, a float hint, the "Python 3 syntax" note);
- no system prompt; temperature 0.001 (BFCL's default);
- grading follows ``ast_checker.py``: names, required/unexpected/optional parameters, Python type
  rules (int accepted for float), BFCL's string normalisation, and order-free matching for parallel
  calls. ``irrelevance`` passes only when the model calls no function.

Java/JavaScript, live and multi-turn categories are out of scope, so the mean reported here is not
the leaderboard's *Overall*; it is the unweighted mean of the four non-live AST categories run.
"""

from __future__ import annotations

import asyncio
import copy
import re
import time
from collections import Counter
from typing import Any, cast

from app.eval.bench.datasets import BfclCase
from app.eval.bench.llm import BenchLLM, Spend

#: BFCL's default sampling temperature.
BFCL_TEMPERATURE = 0.001
PYTHON_HINT = " Note that the provided function is in Python 3 syntax."

GORILLA_TO_OPENAPI = {
    "integer": "integer",
    "number": "number",
    "float": "number",
    "string": "string",
    "boolean": "boolean",
    "bool": "boolean",
    "array": "array",
    "list": "array",
    "dict": "object",
    "object": "object",
    "tuple": "array",
    "any": "string",
}
PYTHON_TYPE_MAPPING: dict[str, type] = {
    "string": str,
    "integer": int,
    "float": float,
    "boolean": bool,
    "array": list,
    "tuple": list,
    "dict": dict,
    "any": str,
}
PYTHON_NESTED_TYPE_CHECK_LIST = ("array", "tuple")

Result = dict[str, Any]


# --------------------------------------------------------------------- request shape


def _cast_to_openai_type(properties: dict[str, Any]) -> dict[str, Any]:
    for key, value in properties.items():
        if "type" not in value:
            value["type"] = "string"
        else:
            var_type = value["type"]
            if var_type == "float":
                value["format"] = "float"
                value["description"] = value.get("description", "") + " This is a float type value."
            value["type"] = GORILLA_TO_OPENAPI.get(var_type, "string")
        if value["type"] in ("array", "object"):
            if "properties" in value:
                value["properties"] = _cast_to_openai_type(value["properties"])
            elif "items" in value:
                items = value["items"]
                items["type"] = GORILLA_TO_OPENAPI[items["type"]]
                if items["type"] == "array" and "items" in items:
                    items["items"]["type"] = GORILLA_TO_OPENAPI[items["items"]["type"]]
                elif items["type"] == "object" and "properties" in items:
                    items["properties"] = _cast_to_openai_type(items["properties"])
        properties[key] = value
    return properties


def to_tools(functions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """BFCL functions → the transport's tool shape (``name``/``description``/``input_schema``)."""
    tools = []
    for function in copy.deepcopy(functions):
        parameters = function["parameters"]
        parameters["type"] = "object"
        parameters["properties"] = _cast_to_openai_type(parameters.get("properties", {}))
        tools.append(
            {
                "name": re.sub(r"\.", "_", function["name"]),
                "description": function.get("description", "") + PYTHON_HINT,
                "input_schema": parameters,
            }
        )
    return tools


# --------------------------------------------------------------------- AST checker (port)


def _convert_func_name(name: str) -> str:
    # OpenAI-style function calling forbids "." in names, so BFCL compares against "_".
    return re.sub(r"\.", "_", name)


def _find_description(functions: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for function in functions:
        if function["name"] == name:
            return function
    return None


def _possible_answer_type(possible: list[Any]) -> type | None:
    for answer in possible:
        if answer != "":
            return type(answer)
    return None


def _type_checker(
    param: str,
    value: Any,
    possible: list[Any],
    expected_desc: str,
    expected: type,
    nested: type | None,
) -> Result:
    result: Result = {"valid": True, "error": [], "is_variable": False}
    is_variable = False
    answer_type = _possible_answer_type(possible)
    if answer_type is not None and answer_type != expected:
        is_variable = True

    if type(value) == expected:  # noqa: E721 — exact type, as the reference checker does
        if nested is None:
            result["is_variable"] = is_variable
            return result
        for item in possible:
            flag = True
            if type(item) == list:  # noqa: E721
                for value_item in cast("list[Any]", value):
                    if not _type_checker(param, value_item, item, str(nested), nested, None)[
                        "valid"
                    ]:
                        flag = False
                        break
            if flag:
                return {"valid": True, "error": [], "is_variable": is_variable}
        result["valid"] = False
        result["error"] = [f"Nested type checking failed for parameter {param!r}."]

    if answer_type is not None and type(value) == answer_type:  # noqa: E721
        result["is_variable"] = True
        return result

    result["valid"] = False
    result["error"].append(
        f"Incorrect type for parameter {param!r}. Expected type {expected_desc}, "
        f"got {type(value).__name__}."
    )
    return result


def standardize_string(value: str) -> str:
    """BFCL's normalisation: drop spaces and ``,./-_*^``, lowercase, single → double quotes."""
    return re.sub(r"[ \,\.\/\-\_\*\^]", "", value).lower().replace("'", '"')


def _string_checker(param: str, value: str, possible: list[Any]) -> Result:
    allowed = [standardize_string(p) for p in possible if type(p) == str]  # noqa: E721
    if standardize_string(value) not in allowed:
        return {"valid": False, "error": [f"Invalid value for {param!r}: {value!r}."]}
    return {"valid": True, "error": []}


def _list_checker(param: str, value: list[Any], possible: list[Any]) -> Result:
    model = [standardize_string(v) if type(v) == str else v for v in value]  # noqa: E721
    allowed = [
        [standardize_string(v) if type(v) == str else v for v in option]  # noqa: E721
        for option in possible
    ]
    if model not in allowed:
        return {"valid": False, "error": [f"Invalid value for {param!r}: {value!r}."]}
    return {"valid": True, "error": []}


def _dict_checker(param: str, value: dict[str, Any], possible: list[Any]) -> Result:
    result: Result = {"valid": False, "error": []}
    for option in possible:
        if option == "":
            continue
        result = {"valid": False, "error": []}
        flag = True
        for key, item in value.items():
            if key not in option:
                result["error"].append(f"Unexpected dict key parameter: {key!r}.")
                flag = False
                break
            standard = standardize_string(item) if type(item) == str else item  # noqa: E721
            allowed = [
                standardize_string(v) if type(v) == str else v for v in option[key]  # noqa: E721
            ]
            if standard not in allowed:
                result["error"].append(f"Invalid value for parameter {key!r}: {item!r}.")
                flag = False
                break
        for key, allowed_values in option.items():
            if key not in value and "" not in allowed_values:
                result["error"].append(f"Missing dict key parameter: {key!r}.")
                flag = False
                break
        if flag:
            return {"valid": True, "error": []}
    return result


def _list_dict_checker(param: str, value: list[Any], possible: list[Any]) -> Result:
    result: Result = {"valid": False, "error": []}
    for option in possible:
        if len(value) != len(option):
            result = {"valid": False, "error": ["Wrong number of dictionaries in the list."]}
            continue
        flag = True
        for index, item in enumerate(value):
            result = _dict_checker(param, item, [option[index]])
            if not result["valid"]:
                flag = False
                break
        if flag:
            return {"valid": True, "error": []}
    return result


def simple_function_checker(
    description: dict[str, Any], model_output: dict[str, Any], possible_answer: dict[str, Any]
) -> Result:
    possible = list(possible_answer.values())[0]
    func_name = _convert_func_name(description["name"])
    details = description["parameters"]["properties"]
    required = description["parameters"].get("required", [])

    if func_name not in model_output:
        return {"valid": False, "error": [f"Function name {func_name!r} not found."]}
    params = model_output[func_name]
    if not isinstance(params, dict):
        return {"valid": False, "error": ["Arguments are not an object."]}

    for param in required:
        if param not in params:
            return {"valid": False, "error": [f"Missing required parameter: {param!r}."]}

    for param, raw_value in params.items():
        value = raw_value
        if param not in details or param not in possible:
            return {"valid": False, "error": [f"Unexpected parameter: {param!r}."]}
        expected_desc = details[param]["type"]
        expected = PYTHON_TYPE_MAPPING[expected_desc]
        nested: type | None = None
        if expected_desc in PYTHON_NESTED_TYPE_CHECK_LIST:
            nested = PYTHON_TYPE_MAPPING[details[param]["items"]["type"]]

        if expected_desc == "tuple" and type(value) == tuple:  # noqa: E721
            value = list(value)
        if expected_desc == "float" and type(value) == int:  # noqa: E721 — Python auto-promotion
            value = float(value)

        checked = _type_checker(param, value, possible[param], expected_desc, expected, nested)
        if not checked["valid"]:
            return checked
        if not checked["is_variable"]:
            if expected is dict:
                result = _dict_checker(param, value, possible[param])
            elif expected is list and nested is dict:
                result = _list_dict_checker(param, value, possible[param])
            elif expected is str:
                result = _string_checker(param, value, possible[param])
            elif expected is list:
                result = _list_checker(param, value, possible[param])
            else:
                result = None
            if result is not None:
                if not result["valid"]:
                    return result
                continue
        if value not in possible[param]:
            return {"valid": False, "error": [f"Invalid value for {param!r}: {value!r}."]}

    for param in possible:
        if param not in params and "" not in possible[param]:
            return {"valid": False, "error": [f"Optional parameter {param!r} not provided."]}
    return {"valid": True, "error": []}


def _parallel_no_order(
    functions: list[dict[str, Any]], output: list[dict[str, Any]], answers: list[dict[str, Any]]
) -> Result:
    if len(output) != len(answers):
        return {"valid": False, "error": ["Wrong number of functions."]}
    matched: set[int] = set()
    for answer in answers:
        description = _find_description(functions, list(answer.keys())[0])
        if description is None:
            return {"valid": False, "error": ["Ground truth names an unknown function."]}
        for index, call in enumerate(output):
            if index in matched:
                continue
            if simple_function_checker(description, call, answer)["valid"]:
                matched.add(index)
                break
        else:
            return {"valid": False, "error": ["Could not match a model call to an answer."]}
    return {"valid": True, "error": []}


def ast_check(case: BfclCase, output: list[dict[str, Any]]) -> Result:
    """Grade decoded calls (``[{name: args}]``) for one case, dispatching as BFCL does."""
    if case.category == "irrelevance":
        if output:
            return {"valid": False, "error": ["Called a function for an irrelevant request."]}
        return {"valid": True, "error": []}
    if not output:
        return {"valid": False, "error": ["No function call."]}
    if "parallel" in case.category:
        return _parallel_no_order(case.functions, output, case.ground_truth)
    if "multiple" in case.category:
        if len(output) != len(case.ground_truth):
            return {"valid": False, "error": ["Wrong number of functions."]}
        description = _find_description(case.functions, list(case.ground_truth[0].keys())[0])
        if description is None:
            return {"valid": False, "error": ["Ground truth names an unknown function."]}
        return simple_function_checker(description, output[0], case.ground_truth[0])
    if len(output) != 1:
        return {"valid": False, "error": ["Wrong number of functions."]}
    return simple_function_checker(case.functions[0], output[0], case.ground_truth[0])


# --------------------------------------------------------------------- the suite


async def run_case(case: BfclCase, *, llm: BenchLLM, spend: Spend) -> dict[str, Any]:
    started = time.monotonic()
    system = "\n".join(m["content"] for m in case.messages if m.get("role") == "system") or None
    messages = [
        {"role": m["role"], "content": m["content"]}
        for m in case.messages
        if m.get("role") in ("user", "assistant")
    ]
    error: str | None = None
    try:
        reply = await llm.complete(
            system=system,
            messages=messages,
            tools=to_tools(case.functions),
            spend=spend,
            temperature=BFCL_TEMPERATURE,
        )
        output = [{call.name: call.input} for call in reply.tool_uses]
        verdict = ast_check(case, output)
    except Exception as exc:  # a failed call is this case's result, never the whole run's
        # Malformed tool-call JSON is the model's fault and fails the case, exactly as an
        # undecodable output does in BFCL; it is also counted so a provider outage is visible.
        error = f"{type(exc).__name__}: {exc}"[:300]
        output = []
        verdict = (
            {"valid": True, "error": []}
            if case.category == "irrelevance" and "tool-call arguments" in error
            else {"valid": False, "error": [error]}
        )
    return {
        "id": case.id,
        "category": case.category,
        "valid": bool(verdict["valid"]),
        "error": (verdict.get("error") or [None])[0],
        "provider_error": error,
        "calls": output,
        "seconds": round(time.monotonic() - started, 2),
    }


async def run_bfcl_suite(
    cases: list[BfclCase],
    *,
    llm: BenchLLM,
    spend: Spend,
    concurrency: int = 12,
    progress: Any = None,
) -> dict[str, Any]:
    gate = asyncio.Semaphore(concurrency)
    started = time.monotonic()

    async def one(case: BfclCase) -> dict[str, Any] | None:
        async with gate:
            if spend.exhausted:
                return None
            item = await run_case(case, llm=llm, spend=spend)
            if progress is not None:
                progress("bfcl", item)
            return item

    items = [item for item in await asyncio.gather(*(one(c) for c in cases)) if item is not None]
    by_category: dict[str, dict[str, Any]] = {}
    for category in dict.fromkeys(c.category for c in cases):
        rows = [i for i in items if i["category"] == category]
        correct = sum(1 for i in rows if i["valid"])
        by_category[category] = {
            "cases": len(rows),
            "correct": correct,
            "accuracy": round(correct / len(rows), 4) if rows else None,
        }
    ast_categories = [c for c in by_category if c != "irrelevance"]
    ast_scores = [by_category[c]["accuracy"] for c in ast_categories]
    return {
        "suite": "bfcl",
        "kind": "tool_calling",
        "cases": len(items),
        "skipped_for_budget": len(cases) - len(items),
        "categories": by_category,
        "non_live_ast_mean": (
            round(sum(ast_scores) / len(ast_scores), 4)
            if ast_scores and None not in ast_scores
            else None
        ),
        "accuracy_all_cases": (
            round(sum(1 for i in items if i["valid"]) / len(items), 4) if items else None
        ),
        "provider_errors": sum(1 for i in items if i["provider_error"]),
        "error_types": dict(Counter(i["error"] for i in items if not i["valid"]).most_common(8)),
        "spend": spend.to_dict(),
        "seconds": round(time.monotonic() - started, 1),
        "settings": {"model": llm.model, "temperature": BFCL_TEMPERATURE},
        "items": items,
    }


__all__ = ["ast_check", "run_bfcl_suite", "simple_function_checker", "to_tools"]
