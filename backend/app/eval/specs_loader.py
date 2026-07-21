"""Benchmark spec format + loader (phase-43, D13) — the foundation of the evaluation harness.

An :class:`EvalSpec` is one reproducible benchmark: what the user would hand ForgeFlow (a prompt
and/or screenshots), the structured requirements that should come out of it, and the features a
correct build must contain. The runner (phase-44) drives the pipeline from these and measures
first-pass vs post-repair pass rate, repair iterations, cost and screenshot→URL time.

Two decisions keep the harness honest:

**One schema, not two.** A spec's ``requirements`` block *is* a
:class:`~app.orchestrator.requirements.RequirementSpecInput` — the same body the requirements API
accepts — and it is validated by the same
:func:`~app.orchestrator.requirements.validate_features`. A seed this loader accepts is therefore
one the product would accept; the two cannot drift.

**Graded difficulty.** Seeds run from simple CRUD to multi-entity apps with cross-cutting rules,
because that gradient is what shows *where* the repair loop earns its keep: first-pass generation
usually handles a todo list, and usually does not handle conflict detection (D1/D13).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

from app.orchestrator.requirements import RequirementSpecInput, validate_features

#: ``backend/app/eval/specs_loader.py`` → repo root → ``eval/specs``.
SPECS_DIR = Path(__file__).resolve().parents[3] / "eval" / "specs"
ASSETS_DIRNAME = "assets"

_SUFFIXES = (".yaml", ".yml", ".json")


class Difficulty(StrEnum):
    """How much a spec is expected to strain first-pass generation."""

    simple = "simple"  # one entity, plain CRUD
    moderate = "moderate"  # a few entities, filtering/derived values
    complex = "complex"  # multiple related entities and cross-cutting rules


#: Ordering for reports: hardest last, so a results table reads as a difficulty curve.
DIFFICULTY_ORDER: dict[str, int] = {
    Difficulty.simple: 0,
    Difficulty.moderate: 1,
    Difficulty.complex: 2,
}


class SpecError(ValueError):
    """A spec file could not be loaded or is invalid. Always names the file and the reason."""

    def __init__(self, path: Path | str, reason: str) -> None:
        self.path = str(path)
        self.reason = reason
        super().__init__(f"{self.path}: {reason}")


class EvalInputs(BaseModel):
    """What the user would hand ForgeFlow. At least one of the two is required."""

    prompt: str | None = None
    #: Paths relative to ``eval/specs/`` (conventionally under ``assets/``).
    screenshots: list[str] = Field(default_factory=list)


class EvalSpec(BaseModel):
    id: str
    title: str
    difficulty: Difficulty = Difficulty.simple
    inputs: EvalInputs
    #: The same contract the requirements API accepts — see the module docstring.
    requirements: RequirementSpecInput
    #: Feature names a correct build must implement; each must exist in ``requirements``.
    expected_features: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @property
    def criteria_count(self) -> int:
        """Total acceptance criteria — the denominator for a spec's pass rate."""
        return sum(len(f.acceptance_criteria) for f in self.requirements.features)


def _read(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix == ".json":
            return json.loads(text)
        return yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise SpecError(path, f"could not be parsed: {exc}") from exc


def load_spec(path: Path | str, *, specs_dir: Path | None = None) -> EvalSpec:
    """Load and fully validate one spec file, or raise :class:`SpecError` explaining why not."""
    path = Path(path)
    root = specs_dir or path.parent

    raw = _read(path)
    if not isinstance(raw, dict):
        raise SpecError(path, "must contain a single spec object")

    try:
        spec = EvalSpec.model_validate(raw)
    except ValidationError as exc:
        raise SpecError(path, _first_error(exc)) from exc

    _validate_semantics(spec, path, root)
    return spec


def _first_error(exc: ValidationError) -> str:
    """One clear reason beats a wall of pydantic output when a human is fixing a seed."""
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "spec"
    return f"{location}: {error['msg']}"


def _validate_semantics(spec: EvalSpec, path: Path, root: Path) -> None:
    """The rules pydantic cannot express — the ones that keep a benchmark meaningful."""
    if path.suffix in _SUFFIXES and spec.id != path.stem:
        raise SpecError(path, f"id {spec.id!r} must match the filename stem {path.stem!r}")

    if not spec.inputs.prompt and not spec.inputs.screenshots:
        raise SpecError(path, "inputs need a prompt, screenshots, or both")

    # Reuse the product's own validator: a seed the loader accepts is one the API would accept.
    try:
        validate_features(spec.requirements)
    except Exception as exc:
        raise SpecError(path, f"requirements are invalid: {exc}") from exc

    names = {f.name.strip().lower() for f in spec.requirements.features}
    for expected in spec.expected_features:
        if expected.strip().lower() not in names:
            raise SpecError(
                path,
                f"expected_features names {expected!r}, which is not a feature in requirements",
            )

    for screenshot in spec.inputs.screenshots:
        if not (root / screenshot).is_file():
            raise SpecError(path, f"screenshot {screenshot!r} does not exist")


def spec_paths(specs_dir: Path | None = None) -> list[Path]:
    directory = specs_dir or SPECS_DIR
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix in _SUFFIXES)


def load_all(specs_dir: Path | None = None) -> list[EvalSpec]:
    """Every seed, ordered easiest-first so a results table reads as a difficulty curve.

    Loading is all-or-nothing: one malformed spec fails the load rather than silently shrinking the
    benchmark, which would quietly flatter the results. For the same reason a missing or empty
    directory is an error, not an empty corpus that every metric would trivially satisfy.
    """
    directory = specs_dir or SPECS_DIR
    if not directory.is_dir():
        raise SpecError(directory, "specs directory does not exist")

    paths = spec_paths(directory)
    if not paths:
        raise SpecError(directory, "contains no specs")

    specs = [load_spec(path, specs_dir=directory) for path in paths]

    seen: dict[str, str] = {}
    for spec, path in zip(specs, spec_paths(directory), strict=True):
        if spec.id in seen:
            raise SpecError(path, f"duplicate spec id {spec.id!r} (also in {seen[spec.id]})")
        seen[spec.id] = str(path)

    return sorted(specs, key=lambda s: (DIFFICULTY_ORDER.get(s.difficulty, 99), s.id))


def spec_index(specs_dir: Path | None = None) -> dict[str, EvalSpec]:
    """Specs keyed by id — how the runner (phase-44) selects what to run."""
    return {spec.id: spec for spec in load_all(specs_dir)}


def _format_table(specs: Iterable[EvalSpec]) -> str:
    rows = [("ID", "DIFFICULTY", "FEATURES", "CRITERIA", "TITLE")]
    rows += [
        (
            spec.id,
            str(spec.difficulty),
            str(len(spec.requirements.features)),
            str(spec.criteria_count),
            spec.title,
        )
        for spec in specs
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows)


def main(argv: list[str] | None = None) -> int:
    """``uv run python -m app.eval.specs_loader --list``"""
    import argparse

    parser = argparse.ArgumentParser(description="Load and validate the benchmark specs.")
    parser.add_argument(
        "--list", action="store_true", help="print the index (loading always validates)"
    )
    parser.add_argument("--quiet", action="store_true", help="validate only, print nothing")
    parser.add_argument("--dir", type=Path, default=None, help="specs directory")
    args = parser.parse_args(argv)

    try:
        specs = load_all(args.dir)
    except SpecError as exc:
        print(f"INVALID SPEC — {exc}")
        return 1

    if not args.quiet:
        print(_format_table(specs))
        print(
            f"\n{len(specs)} specs · "
            f"{sum(len(s.requirements.features) for s in specs)} features · "
            f"{sum(s.criteria_count for s in specs)} acceptance criteria"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())


__all__ = [
    "ASSETS_DIRNAME",
    "SPECS_DIR",
    "Difficulty",
    "EvalInputs",
    "EvalSpec",
    "SpecError",
    "load_all",
    "load_spec",
    "spec_index",
    "spec_paths",
]
