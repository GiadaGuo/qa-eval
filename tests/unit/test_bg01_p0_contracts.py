from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from qa_eval.calculators.protocol import CalculatorContext

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = ROOT / "schemas" / "qa-eval"
RULE_ROOT = ROOT / "configs" / "card-rules"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(relative_path: str) -> Draft202012Validator:
    schema = _json(SCHEMA_ROOT / relative_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize(
    ("document_name", "schema_name"),
    [
        ("bg01-card-rules-1.0.0.json", "card-rule-set/1.0.schema.json"),
        ("bg01-consistency-1.0.0.json", "card-rule-set/1.0.schema.json"),
        (
            "bg01-evaluation-contract-1.0.0.json",
            "bg01-evaluation-contract/1.0.schema.json",
        ),
        (
            "bg01-lifecycle-contract-1.0.0.json",
            "bg01-lifecycle-contract/1.0.schema.json",
        ),
    ],
)
def test_versioned_bg01_documents_validate(document_name: str, schema_name: str) -> None:
    _validator(schema_name).validate(_json(RULE_ROOT / document_name))


def test_declarations_require_all_four_elements_but_not_unapproved_fields() -> None:
    validator = _validator("dataset-card-declarations/1.0.schema.json")
    valid = {
        "schema_version": "1.0",
        "basic_information": {},
        "content_characteristics": {},
        "construction_process": {},
        "application_description": {},
    }
    validator.validate(valid)

    invalid = dict(valid)
    invalid.pop("construction_process")
    with pytest.raises(ValidationError):
        validator.validate(invalid)


def test_declarations_reject_unknown_government_specific_fields() -> None:
    validator = _validator("dataset-card-declarations/1.0.schema.json")
    document = {
        "schema_version": "1.0",
        "basic_information": {"unapproved_government_code": "invented"},
        "content_characteristics": {},
        "construction_process": {},
        "application_description": {},
    }
    with pytest.raises(ValidationError):
        validator.validate(document)


def test_unapproved_field_requirements_are_disabled() -> None:
    rules = _json(RULE_ROOT / "bg01-card-rules-1.0.0.json")["rules"]
    field_rules = [rule for rule in rules if rule["kind"] == "FIELD_REQUIREMENT"]
    assert {rule["requirement_level"] for rule in field_rules} == {
        "REQUIRED",
        "CONDITIONAL",
        "OPTIONAL",
    }
    assert all(
        rule["approval_status"] == "PENDING_BUSINESS_CONFIRMATION"
        and rule["enabled"] is False
        for rule in field_rules
    )


def test_placeholder_contract_does_not_reclassify_business_value_as_empty() -> None:
    rules = _json(RULE_ROOT / "bg01-card-rules-1.0.0.json")["rules"]
    by_id = {rule["rule_id"]: rule for rule in rules}
    assert by_id["placeholder.empty"]["parameters"]["values"] == [None, ""]
    reserved = by_id["placeholder.business_reserved"]
    assert reserved["parameters"]["candidate_values"] == ["无"]
    assert reserved["enabled"] is False
    assert reserved["approval_status"] == "PENDING_BUSINESS_CONFIRMATION"


def test_unapproved_consistency_rules_contain_no_tolerance_or_enum_values() -> None:
    rules = _json(RULE_ROOT / "bg01-consistency-1.0.0.json")["rules"]
    assert all(rule["enabled"] is False for rule in rules)
    distribution = next(rule for rule in rules if rule["rule_id"] == "distribution.labels")
    assert distribution["parameters"]["absolute_tolerance"] is None
    assert distribution["parameters"]["relative_tolerance"] is None
    enums = next(rule for rule in rules if rule["rule_id"] == "government.enums")
    assert enums["parameters"]["references"] == []


def test_evaluation_contract_freezes_temporary_keys_and_non_scoring_semantics() -> None:
    contract = _json(RULE_ROOT / "bg01-evaluation-contract-1.0.0.json")
    assert {item["metric_key"] for item in contract["metric_definitions"]} == {
        "BG01_CARD_CONFORMANCE",
        "BG01_CARD_DATA_CONSISTENCY",
    }
    assert all(
        item["key_status"] == "TEMPORARY_UNAPPROVED"
        and item["scoring"] == "NOT_SCORED_WITHOUT_APPROVED_WEIGHT"
        for item in contract["metric_definitions"]
    )
    assert contract["coverage_contract"]["unassessed_measurement"].startswith(
        "omit observation"
    )


def test_calculator_protocol_extension_is_additive_and_deferred_to_p2() -> None:
    contract = _json(RULE_ROOT / "bg01-evaluation-contract-1.0.0.json")
    extension = contract["calculator_protocol_extension"]
    assert extension["phase"] == "P2"
    assert extension["legacy_input_path"] == "PRESERVED"
    assert {item["name"] for item in extension["additive_fields"]} == {
        "input_artifacts",
        "progress",
    }
    current_fields = {item.name for item in fields(CalculatorContext)}
    assert "input_path" in current_fields
    assert "input_artifacts" in current_fields
    assert "progress" in current_fields


def test_frozen_documents_have_reproducible_sha256() -> None:
    for path in sorted(RULE_ROOT.glob("bg01-*-1.0.0.json")):
        first = hashlib.sha256(path.read_bytes()).hexdigest()
        second = hashlib.sha256(path.read_bytes()).hexdigest()
        assert first == second
        assert len(first) == 64


def test_evidence_rows_require_traceability_without_raw_values() -> None:
    validator = _validator("evidence/bg01-card-checks/1.0.0.schema.json")
    validator.validate(
        {
            "comparison_id": "basic.name",
            "scope": "FIELD",
            "field_path": "$.basic_information.name",
            "outcome": "NOT_EVALUATED",
            "rule_id": "basic.name",
            "rule_version": "1.0.0",
            "declared_value_hash": None,
            "observed_value_hash": None,
            "reason_code": "RULE_NOT_APPROVED",
        }
    )

