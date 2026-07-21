"""Spec loader (phase-43): every seed loads and validates; malformed specs are rejected clearly.

These are pure file-and-schema tests — no database, no Docker. The seeds themselves are the subject:
if one of them stops validating, the benchmark is broken and the eval harness would be measuring a
smaller corpus than it reports.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.eval.specs_loader import (
    SPECS_DIR,
    Difficulty,
    EvalSpec,
    SpecError,
    load_all,
    load_spec,
    spec_index,
    spec_paths,
)

MIN_SEEDS, MAX_SEEDS = 5, 10


def _valid_spec(**overrides: object) -> dict[str, object]:
    spec: dict[str, object] = {
        "id": "sample",
        "title": "Sample",
        "difficulty": "simple",
        "inputs": {"prompt": "build me a thing"},
        "requirements": {
            "features": [
                {
                    "name": "Do the thing",
                    "description": "It does the thing.",
                    "acceptance_criteria": [{"text": "The thing is done.", "kind": "unit"}],
                }
            ]
        },
        "expected_features": ["Do the thing"],
    }
    spec.update(overrides)
    return spec


def _write(tmp_path: Path, data: object, name: str = "sample.yaml") -> Path:
    import yaml

    path = tmp_path / name
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


# --------------------------------------------------------------------- the seeds


def test_every_seed_loads_and_validates() -> None:
    specs = load_all()
    assert len(specs) == len(spec_paths())
    assert all(isinstance(spec, EvalSpec) for spec in specs)


def test_the_benchmark_has_between_five_and_ten_seeds() -> None:
    assert MIN_SEEDS <= len(load_all()) <= MAX_SEEDS


def test_seed_ids_are_unique_and_match_their_filenames() -> None:
    for path in spec_paths():
        assert load_spec(path, specs_dir=SPECS_DIR).id == path.stem
    assert len(spec_index()) == len(spec_paths())


def test_seeds_span_the_full_difficulty_range() -> None:
    """A benchmark of only easy specs cannot show where the repair loop earns its keep."""
    grades = {spec.difficulty for spec in load_all()}
    assert grades == {Difficulty.simple, Difficulty.moderate, Difficulty.complex}


def test_seeds_are_returned_easiest_first() -> None:
    order = [spec.difficulty for spec in load_all()]
    ranks = [list(Difficulty).index(d) for d in order]
    assert ranks == sorted(ranks)


def test_harder_seeds_really_are_larger() -> None:
    """Difficulty should track substance, not just a label someone typed."""
    by_grade: dict[Difficulty, list[int]] = {}
    for spec in load_all():
        by_grade.setdefault(spec.difficulty, []).append(spec.criteria_count)

    simple = max(by_grade[Difficulty.simple])
    complex_ = min(by_grade[Difficulty.complex])
    assert complex_ > simple


def test_every_seed_carries_inputs_requirements_and_expected_features() -> None:
    for spec in load_all():
        assert spec.inputs.prompt or spec.inputs.screenshots
        assert spec.requirements.features
        assert spec.expected_features
        assert spec.notes, f"{spec.id} should say what it is meant to catch"


def test_referenced_screenshots_exist_on_disk() -> None:
    referenced = [(s.id, shot) for s in load_all() for shot in s.inputs.screenshots]
    assert referenced, "at least one seed should exercise the screenshot input path"
    for spec_id, shot in referenced:
        assert (SPECS_DIR / shot).is_file(), f"{spec_id} references a missing {shot}"


# --------------------------------------------------------------------- rejection


def test_a_malformed_file_is_rejected_with_the_reason(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("id: broken\n  bad indentation: [", encoding="utf-8")

    with pytest.raises(SpecError) as exc:
        load_spec(path)
    assert "broken.yaml" in str(exc.value) and "parsed" in str(exc.value)


def test_a_spec_missing_required_fields_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, {"id": "sample", "title": "No inputs"})

    with pytest.raises(SpecError, match="inputs"):
        load_spec(path)


def test_a_spec_whose_id_disagrees_with_its_filename_is_rejected(tmp_path: Path) -> None:
    """The file is the record — a mismatch makes the index ambiguous."""
    path = _write(tmp_path, _valid_spec(id="not-the-filename"))

    with pytest.raises(SpecError, match="must match the filename"):
        load_spec(path)


def test_a_spec_with_no_input_at_all_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid_spec(inputs={}))

    with pytest.raises(SpecError, match="prompt, screenshots, or both"):
        load_spec(path)


def test_requirements_that_the_product_would_reject_are_rejected_here(tmp_path: Path) -> None:
    """The point of reusing `validate_features`: eval and product cannot drift."""
    no_criteria = _valid_spec(
        requirements={"features": [{"name": "Empty", "acceptance_criteria": []}]},
        expected_features=["Empty"],
    )
    path = _write(tmp_path, no_criteria)

    with pytest.raises(SpecError, match="acceptance criterion"):
        load_spec(path)


def test_duplicate_feature_names_are_rejected(tmp_path: Path) -> None:
    duplicated = _valid_spec(
        requirements={
            "features": [
                {"name": "Same", "acceptance_criteria": [{"text": "a"}]},
                {"name": "same", "acceptance_criteria": [{"text": "b"}]},
            ]
        },
        expected_features=["Same"],
    )
    path = _write(tmp_path, duplicated)

    with pytest.raises(SpecError, match="Duplicate feature name"):
        load_spec(path)


def test_expected_features_must_name_real_features(tmp_path: Path) -> None:
    """Otherwise the benchmark asserts something the requirements never described."""
    path = _write(tmp_path, _valid_spec(expected_features=["Nonexistent"]))

    with pytest.raises(SpecError, match="not a feature in requirements"):
        load_spec(path)


def test_a_missing_screenshot_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _valid_spec(inputs={"screenshots": ["assets/nope.png"]}))

    with pytest.raises(SpecError, match="does not exist"):
        load_spec(path)


def test_a_non_object_document_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, ["not", "a", "spec"])

    with pytest.raises(SpecError, match="single spec object"):
        load_spec(path)


def test_one_bad_spec_fails_the_whole_load(tmp_path: Path) -> None:
    """A benchmark that silently shrinks would flatter its own results."""
    _write(tmp_path, _valid_spec(), "sample.yaml")
    _write(tmp_path, _valid_spec(id="other", expected_features=["Nope"]), "other.yaml")

    with pytest.raises(SpecError, match="other.yaml"):
        load_all(tmp_path)


def test_json_specs_load_too(tmp_path: Path) -> None:
    path = tmp_path / "sample.json"
    path.write_text(json.dumps(_valid_spec()), encoding="utf-8")

    assert load_spec(path).id == "sample"


def test_the_cli_lists_every_seed(capsys: pytest.CaptureFixture[str]) -> None:
    from app.eval.specs_loader import main

    assert main(["--list"]) == 0
    out = capsys.readouterr().out
    for spec in load_all():
        assert spec.id in out
    assert f"{len(load_all())} specs" in out


def test_the_cli_reports_the_offending_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.eval.specs_loader import main

    _write(tmp_path, _valid_spec(id="wrong-name"), "sample.yaml")

    assert main(["--list", "--dir", str(tmp_path)]) == 1
    assert "sample.yaml" in capsys.readouterr().out


def test_the_cli_can_validate_quietly(capsys: pytest.CaptureFixture[str]) -> None:
    """Validation is the side effect of loading, so --quiet is a usable CI gate."""
    from app.eval.specs_loader import main

    assert main(["--quiet"]) == 0
    assert capsys.readouterr().out == ""


def test_a_missing_specs_directory_is_an_error_not_an_empty_corpus(tmp_path: Path) -> None:
    """An empty benchmark satisfies every metric trivially — it must never look like success."""
    with pytest.raises(SpecError, match="does not exist"):
        load_all(tmp_path / "nowhere")


def test_a_directory_with_no_specs_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match="no specs"):
        load_all(tmp_path)


def test_the_cli_fails_on_an_empty_corpus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.eval.specs_loader import main

    assert main(["--dir", str(tmp_path / "nowhere")]) == 1
    assert "does not exist" in capsys.readouterr().out
