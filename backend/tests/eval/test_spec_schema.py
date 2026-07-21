"""Schema reuse (phase-43): an `EvalSpec` embeds a real `RequirementSpec`, not a lookalike.

The value of this is that the benchmark corpus and the product cannot drift. If someone tightens
requirements validation in phase-25, the seeds must still satisfy it — or the eval is measuring a
contract the product no longer honours.
"""

from __future__ import annotations

import pytest

from app.core.errors import UserError
from app.db.models import Feature
from app.db.models.enums import CriterionKind
from app.eval.specs_loader import EvalSpec, load_all
from app.orchestrator.requirements import (
    FeatureInput,
    RequirementSpecInput,
    validate_features,
)


def test_the_requirements_block_is_the_products_own_input_schema() -> None:
    """Not a copy that happens to look similar — literally the API's request body type."""
    annotation = EvalSpec.model_fields["requirements"].annotation
    assert annotation is RequirementSpecInput


def test_every_seed_satisfies_the_products_validator() -> None:
    for spec in load_all():
        features = validate_features(spec.requirements)
        assert features and all(isinstance(f, Feature) for f in features)


def test_validation_mints_stable_join_keys_for_every_criterion() -> None:
    """`AcceptanceCriterion.id` is the join key requirements → tests → results → repair."""
    for spec in load_all():
        ids = [c.id for f in validate_features(spec.requirements) for c in f.acceptance_criteria]
        assert ids, spec.id
        assert all(cid.startswith("ac-") for cid in ids)
        assert len(set(ids)) == len(ids), f"{spec.id} produced duplicate criterion ids"


def test_seed_criteria_declare_a_test_kind_test_gen_can_use() -> None:
    kinds = {
        c.kind
        for spec in load_all()
        for f in validate_features(spec.requirements)
        for c in f.acceptance_criteria
    }
    assert kinds <= set(CriterionKind)
    # A benchmark that is all-unit would never exercise the deployed app.
    assert CriterionKind.e2e in kinds and CriterionKind.unit in kinds


def test_the_validator_rejects_what_the_product_rejects() -> None:
    """Same function, same rules — this is the guarantee, asserted directly."""
    with pytest.raises(UserError, match="at least one feature"):
        validate_features(RequirementSpecInput(features=[]))

    with pytest.raises(UserError, match="acceptance criterion"):
        validate_features(RequirementSpecInput(features=[FeatureInput(name="Bare")]))


def test_criteria_counts_are_the_denominator_for_a_pass_rate() -> None:
    for spec in load_all():
        expected = sum(len(f.acceptance_criteria) for f in spec.requirements.features)
        assert spec.criteria_count == expected


def test_expected_features_name_features_the_requirements_describe() -> None:
    for spec in load_all():
        described = {f.name for f in spec.requirements.features}
        assert set(spec.expected_features) <= described, spec.id
