from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator

from qa_eval.calculators.dataset_card_conformance import (
    DatasetCardConformanceCalculator,
)
from qa_eval.calculators.protocol import CalculatorContext
from qa_eval.contracts.snapshots import ProfileSnapshotV1

ROOT = Path(__file__).resolve().parents[2]


def _snapshot(path: Path, document: dict | None) -> Path:
    snapshot = ProfileSnapshotV1.model_validate(
        {
            "snapshot_id": uuid4(),
            "dataset_version_id": uuid4(),
            "dataset_version": {
                "id": str(uuid4()),
                "content_fingerprint": "sha256:" + "a" * 64,
            },
            "card": (
                {
                    "card_id": str(uuid4()),
                    "revision": 1,
                    "lifecycle_status": "PUBLISHED",
                    "content_sha256": "b" * 64,
                    "document": document,
                }
                if document is not None
                else None
            ),
            "observed_profile": {
                "profile_id": str(uuid4()),
                "checksum_sha256": "c" * 64,
            },
            "manifest": {"uri": "manifest.json", "sha256": "d" * 64},
            "rules": [],
            "calculator_bindings": [],
            "content_sha256": "e" * 64,
            "input_fingerprint": "sha256:" + "e" * 64,
            "created_at": datetime.now(UTC),
        }
    )
    path.write_text(snapshot.model_dump_json(), encoding="utf-8")
    return path


def _context(tmp_path: Path, document: dict | None) -> CalculatorContext:
    snapshot = _snapshot(tmp_path / "snapshot.json", document)
    return CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key="BG01_CARD_CONFORMANCE",
        metric_version="1.0.0",
        input_path=snapshot,
        output_dir=tmp_path / "output",
        input_schema_ref="qa-eval://schemas/profile-snapshot/1.0.schema.json",
        input_artifacts={"PROFILE_SNAPSHOT": snapshot},
    )


def _full_document() -> dict:
    return {
        "schema_version": "1.0",
        "basic_information": {"name": "dataset"},
        "content_characteristics": {"modality": "text"},
        "construction_process": {"source": "fixture"},
        "application_description": {"scenario": "testing"},
    }


def _rows(output) -> list[dict]:
    return [
        json.loads(line)
        for line in output.evidence[0].path.read_text(encoding="utf-8").splitlines()
    ]


def test_unapproved_rules_are_partial_and_do_not_emit_fabricated_ratios(
    tmp_path: Path,
) -> None:
    output = DatasetCardConformanceCalculator().execute(
        _context(tmp_path, _full_document()),
        {"rule_set_ref": "bg01-card-rules@1.0.0"},
    )
    observations = {item["key"]: item for item in output.observations}
    rows = _rows(output)

    assert output.status == "PARTIAL"
    assert observations["four_element_completeness_ratio"]["value"] == 1
    assert "required_field_completeness_ratio" not in observations
    assert "government_field_valid_ratio" not in observations
    assert output.population["evaluated_count"] == 4
    assert output.population["excluded_count"] == 21
    assert all(
        row["reason_code"] == "RULE_NOT_APPROVED"
        for row in rows
        if row["outcome"] == "NOT_EVALUATED"
    )

    schema = json.loads(
        (
            ROOT / "schemas/qa-eval/evidence/bg01-card-checks/1.0.0.schema.json"
        ).read_text()
    )
    validator = Draft202012Validator(schema)
    for row in rows:
        validator.validate(row)


def test_missing_card_emits_zero_structure_ratio_and_traceable_evidence(
    tmp_path: Path,
) -> None:
    output = DatasetCardConformanceCalculator().execute(
        _context(tmp_path, None), {"rule_set_ref": "bg01-card-rules@1.0.0"}
    )
    rows = _rows(output)
    observation = output.observations[0]

    assert observation["key"] == "four_element_completeness_ratio"
    assert observation["value"] == 0
    assert observation["numerator"] == 0
    assert observation["denominator"] == 4
    assert sum(row["outcome"] == "MISSING" for row in rows) == 4
    assert all(
        row["declared_value_hash"] is None
        for row in rows
        if row["outcome"] == "MISSING"
    )


def test_approved_required_conditional_placeholder_and_enum_rules(
    monkeypatch, tmp_path
):
    rules = {
        "rule_set_key": "approved-test-rules",
        "version": "1.0.0",
        "rules": [
            {
                "rule_id": "placeholder.empty",
                "kind": "PLACEHOLDER",
                "approval_status": "APPROVED",
                "enabled": True,
                "parameters": {"values": [None, ""], "trim_strings": True},
            },
            {
                "rule_id": "basic.name",
                "kind": "FIELD_REQUIREMENT",
                "field_path": "$.basic_information.name",
                "requirement_level": "REQUIRED",
                "approval_status": "APPROVED",
                "enabled": True,
                "parameters": {},
            },
            {
                "rule_id": "basic.access",
                "kind": "FIELD_REQUIREMENT",
                "field_path": "$.basic_information.access",
                "requirement_level": "CONDITIONAL",
                "approval_status": "APPROVED",
                "enabled": True,
                "parameters": {
                    "condition": {
                        "field_path": "$.content_characteristics.modality",
                        "operator": "EQUALS",
                        "value": "video",
                    }
                },
            },
            {
                "rule_id": "government.modality",
                "kind": "ENUM_REFERENCE",
                "field_path": "$.content_characteristics.modality",
                "approval_status": "APPROVED",
                "enabled": True,
                "parameters": {"values": ["text"]},
            },
        ],
    }
    monkeypatch.setattr(
        DatasetCardConformanceCalculator,
        "_load_rule_set",
        staticmethod(lambda _reference: rules),
    )
    document = _full_document()
    document["basic_information"]["name"] = "  "
    document["content_characteristics"]["modality"] = "video"
    output = DatasetCardConformanceCalculator().execute(
        _context(tmp_path, document), {"rule_set_ref": "approved-test-rules@1.0.0"}
    )
    observations = {item["key"]: item for item in output.observations}
    outcomes = {row["comparison_id"]: row["outcome"] for row in _rows(output)}

    assert output.status == "OK"
    assert observations["required_field_completeness_ratio"]["value"] == 0
    assert observations["required_field_completeness_ratio"]["denominator"] == 2
    assert observations["government_field_valid_ratio"]["value"] == 0
    assert outcomes["basic.name"] == "PLACEHOLDER"
    assert outcomes["basic.access"] == "MISSING"
    assert outcomes["government.modality"] == "INVALID"


def test_business_reserved_none_word_is_not_placeholder(monkeypatch, tmp_path):
    rules = {
        "rule_set_key": "approved-test-rules",
        "version": "1.0.0",
        "rules": [
            {
                "rule_id": "placeholder.empty",
                "kind": "PLACEHOLDER",
                "approval_status": "APPROVED",
                "enabled": True,
                "parameters": {"values": [None, ""], "trim_strings": True},
            },
            {
                "rule_id": "basic.name",
                "kind": "FIELD_REQUIREMENT",
                "field_path": "$.basic_information.name",
                "requirement_level": "REQUIRED",
                "approval_status": "APPROVED",
                "enabled": True,
                "parameters": {},
            },
        ],
    }
    monkeypatch.setattr(
        DatasetCardConformanceCalculator,
        "_load_rule_set",
        staticmethod(lambda _reference: rules),
    )
    document = _full_document()
    document["basic_information"]["name"] = "无"
    output = DatasetCardConformanceCalculator().execute(
        _context(tmp_path, document), {"rule_set_ref": "approved-test-rules@1.0.0"}
    )

    observation = next(
        item
        for item in output.observations
        if item["key"] == "required_field_completeness_ratio"
    )
    assert observation["value"] == 1
