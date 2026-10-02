"""Pinned public datasets for the industry benchmarks.

Every source is fixed to an upstream **commit** and checked against a **SHA-256** before use, so a
re-run scores against byte-identical problems — or fails loudly instead of quietly measuring
something else. Files are downloaded once into ``eval/datasets/`` (gitignored) and reused.

| Suite | Upstream | Problems |
|---|---|---|
| HumanEval | openai/human-eval | 164 |
| MBPP (sanitized, test split) | google-research/mbpp | 257 |
| HumanEval-TS | nuprl/MultiPL-E ``humaneval-ts`` | 159 |
| BFCL v3 (non-live, Python) | gorilla-llm/Berkeley-Function-Calling-Leaderboard | 1,240 |
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

DATASETS_DIR = Path(__file__).resolve().parents[4] / "eval" / "datasets"

_HUMANEVAL_COMMIT = "463c980b59e818ace59f6f9803cd92c749ceae61"
_MBPP_COMMIT = "f82046ba5aabbbb427dbfd38a254d26bff08b533"
_BFCL_COMMIT = "61fc0608cfd831fcfbbaa676ebdfef0ed963eeda"
_BFCL_BASE = (
    "https://huggingface.co/datasets/gorilla-llm/Berkeley-Function-Calling-Leaderboard/resolve/"
    + _BFCL_COMMIT
)
# MultiPL-E publishes parquet only; the datasets-server serves the same rows as JSON. It cannot be
# pinned to a revision, so the assembled rows are pinned by content hash instead.
_MULTIPLE_ROWS = (
    "https://datasets-server.huggingface.co/rows?dataset=nuprl/MultiPL-E&config=humaneval-ts"
    "&split=test&offset={offset}&length=100"
)
_MULTIPLE_SHA256 = "3ee15b479765370ca8b73d74bdd405f5c4c815724f6fcd934c1f99dc59c7271f"
_MULTIPLE_COUNT = 159


class DatasetError(RuntimeError):
    """A dataset could not be fetched or does not match its pinned checksum."""


@dataclass(frozen=True)
class Source:
    filename: str
    url: str
    sha256: str


HUMANEVAL = Source(
    "HumanEval.jsonl.gz",
    f"https://raw.githubusercontent.com/openai/human-eval/{_HUMANEVAL_COMMIT}/data/HumanEval.jsonl.gz",
    "b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef",
)
MBPP = Source(
    "sanitized-mbpp.json",
    f"https://raw.githubusercontent.com/google-research/google-research/{_MBPP_COMMIT}/mbpp/"
    "sanitized-mbpp.json",
    "ca95deaa9a01ef0a6f439f88bcf0dd3db3563d22f22aad6cae04ebb9a8d8c8e9",
)

#: BFCL v3 non-live AST categories (Python). ``(questions sha256, possible-answers sha256)``.
BFCL_CATEGORIES: dict[str, tuple[str, str | None]] = {
    "simple": (
        "fbc37b2ad252bf9af985582e0e07b456173fe627d957491472ea9cef5fb83158",
        "2911a2bc00df82c4f999ffa64fedb0164cb88e96212ffa5972087eb91fd496ee",
    ),
    "multiple": (
        "aef168155ebd74b7ac2401198b201343bc7d16d7a3d7e0d4e6d8ee82c6969b2a",
        "244e00ce9395df948bcafc7bee64e8f9c87ef70887587d83cae45b13699f3047",
    ),
    "parallel": (
        "fad375d68295776efb63e5aa331dd884f8635e7bb50e05908113c74f14281c3c",
        "3af54bf04e5b7640b03a9b6a82440f52474feb24b398f94435fd2b155c7727de",
    ),
    "parallel_multiple": (
        "8863ea8433239f55c5f016154cf0830853c89f693c6ea270396a2fa121960579",
        "df4d2c16238c1465dc04697c273cf3ab4a450075fcd274e3dc86ad5bf0049b75",
    ),
    # Irrelevance has no answers: the only correct response is to call no function at all.
    "irrelevance": ("975f51c51f688649fd190078efd87081241e0a326f9114a2ea3c1ca2440d8690", None),
}


def bfcl_sources(category: str) -> tuple[Source, Source | None]:
    questions_sha, answers_sha = BFCL_CATEGORIES[category]
    name = f"BFCL_v3_{category}.json"
    questions = Source(name, f"{_BFCL_BASE}/{name}", questions_sha)
    answers = (
        None
        if answers_sha is None
        else Source(f"possible_answer_{name}", f"{_BFCL_BASE}/possible_answer/{name}", answers_sha)
    )
    return questions, answers


# --------------------------------------------------------------------- fetching


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _store(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def fetch(source: Source, cache_dir: Path | None = None) -> bytes:
    """The verified bytes of ``source`` — from the cache when it still matches, else downloaded."""
    path = (cache_dir or DATASETS_DIR) / source.filename
    if path.is_file():
        cached = path.read_bytes()
        if _sha256(cached) == source.sha256:
            return cached
    try:
        response = httpx.get(source.url, follow_redirects=True, timeout=120)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DatasetError(f"could not download {source.url}: {exc}") from exc
    data = response.content
    if _sha256(data) != source.sha256:
        raise DatasetError(
            f"{source.filename} does not match its pinned checksum — upstream changed; refusing "
            "to score against different problems"
        )
    _store(path, data)
    return data


def _fetch_multiple_ts(cache_dir: Path | None = None) -> bytes:
    path = (cache_dir or DATASETS_DIR) / "MultiPL-E-humaneval-ts.json"
    if path.is_file() and _sha256(cached := path.read_bytes()) == _MULTIPLE_SHA256:
        return cached
    rows: list[dict[str, Any]] = []
    try:
        for offset in range(0, _MULTIPLE_COUNT, 100):
            response = httpx.get(_MULTIPLE_ROWS.format(offset=offset), timeout=120)
            response.raise_for_status()
            rows += [item["row"] for item in response.json()["rows"]]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise DatasetError(f"could not download MultiPL-E humaneval-ts: {exc}") from exc
    kept = sorted(
        ({k: row[k] for k in ("name", "prompt", "tests", "stop_tokens")} for row in rows),
        key=lambda row: str(row["name"]),
    )
    data = json.dumps(kept, sort_keys=True, ensure_ascii=False).encode()
    if _sha256(data) != _MULTIPLE_SHA256:
        raise DatasetError("MultiPL-E humaneval-ts does not match its pinned checksum")
    _store(path, data)
    return data


# --------------------------------------------------------------------- problems


@dataclass(frozen=True)
class CodeProblem:
    """One function-level coding task, graded by executing its tests."""

    id: str
    language: str  # "python" | "typescript"
    prompt: str  # what the model is shown (the stub, or a description + one example test)
    entry_point: str
    #: Code placed before the model's solution (HumanEval's stub: imports + helper functions).
    preamble: str = ""
    #: Code placed after it — the benchmark's own tests.
    tests: str = ""
    #: Code placed before everything (MBPP's ``test_imports``).
    header: str = ""


def load_humaneval(cache_dir: Path | None = None) -> list[CodeProblem]:
    raw = gzip.decompress(fetch(HUMANEVAL, cache_dir)).decode()
    problems = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        problems.append(
            CodeProblem(
                id=row["task_id"],
                language="python",
                prompt=row["prompt"],
                entry_point=row["entry_point"],
                preamble=row["prompt"],
                tests=f"{row['test']}\n\ncheck({row['entry_point']})\n",
            )
        )
    return problems


_MBPP_DEF = re.compile(r"^def\s+([A-Za-z_]\w*)\s*\(", re.M)


def load_mbpp(cache_dir: Path | None = None) -> list[CodeProblem]:
    """The sanitized test split (task ids 11–510): 257 hand-verified problems."""
    rows = json.loads(fetch(MBPP, cache_dir))
    problems = []
    for row in rows:
        if not 11 <= int(row["task_id"]) <= 510:
            continue
        tests = list(row["test_list"])
        match = _MBPP_DEF.search(row["code"])
        entry = match.group(1) if match else ""
        # bigcode-harness convention: the description plus ONE example test; graded on all of them.
        prompt = f"{row['prompt'].strip()}\nYour code should pass this test:\n\n{tests[0]}"
        problems.append(
            CodeProblem(
                id=f"Mbpp/{row['task_id']}",
                language="python",
                prompt=prompt,
                entry_point=entry,
                tests="\n".join(tests) + "\n",
                header="\n".join(row.get("test_imports") or []),
            )
        )
    return problems


_TS_FUNCTION = re.compile(r"function\s+([A-Za-z_]\w*)\s*\(")


def load_humaneval_ts(cache_dir: Path | None = None) -> list[CodeProblem]:
    rows = json.loads(_fetch_multiple_ts(cache_dir))
    problems = []
    for row in rows:
        names = _TS_FUNCTION.findall(row["prompt"])
        problems.append(
            CodeProblem(
                id=str(row["name"]),
                language="typescript",
                prompt=row["prompt"],
                entry_point=names[-1] if names else "",
                preamble=row["prompt"],
                tests=row["tests"],
            )
        )
    return problems


def reference_solutions(cache_dir: Path | None = None) -> dict[str, str]:
    """Upstream reference solutions, by problem id — used only to verify the grader.

    HumanEval's are function bodies, MBPP's are full functions; MultiPL-E ships none.
    """
    solutions: dict[str, str] = {}
    for line in gzip.decompress(fetch(HUMANEVAL, cache_dir)).decode().splitlines():
        if line.strip():
            row = json.loads(line)
            solutions[row["task_id"]] = row["canonical_solution"]
    for row in json.loads(fetch(MBPP, cache_dir)):
        solutions[f"Mbpp/{row['task_id']}"] = row["code"]
    return solutions


@dataclass(frozen=True)
class BfclCase:
    id: str
    category: str
    messages: list[dict[str, Any]]
    functions: list[dict[str, Any]]
    ground_truth: list[dict[str, Any]] = field(default_factory=list)


def _jsonl(data: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in data.decode().splitlines() if line.strip()]


def load_bfcl(category: str, cache_dir: Path | None = None) -> list[BfclCase]:
    questions_src, answers_src = bfcl_sources(category)
    questions = _jsonl(fetch(questions_src, cache_dir))
    answers = (
        {}
        if answers_src is None
        else {row["id"]: row["ground_truth"] for row in _jsonl(fetch(answers_src, cache_dir))}
    )
    return [
        BfclCase(
            id=row["id"],
            category=category,
            # Every non-live case is a single turn (verified for the pinned commit).
            messages=list(row["question"][0]),
            functions=list(row["function"]),
            ground_truth=list(answers.get(row["id"], [])),
        )
        for row in questions
    ]


__all__ = [
    "BFCL_CATEGORIES",
    "DATASETS_DIR",
    "BfclCase",
    "CodeProblem",
    "DatasetError",
    "Source",
    "fetch",
    "load_bfcl",
    "load_humaneval",
    "load_humaneval_ts",
    "load_mbpp",
    "reference_solutions",
]
