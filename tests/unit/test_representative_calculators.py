import json
from pathlib import Path
from uuid import uuid4

import pytest

from qa_eval.calculators.exact_text_duplicate import ExactTextDuplicateCalculator
from qa_eval.calculators.format_compliance import FormatComplianceCalculator
from qa_eval.calculators.protocol import CalculatorContext, CalculatorValidationError
from qa_eval.calculators.structural_completeness import StructuralCompletenessCalculator
from qa_eval.calculators.text_completeness import TextCompletenessCalculator
from qa_eval.calculators.text_duplicate_degree import TextDuplicateDegreeCalculator


def _sample(
    version_id, sample_id: str, *, presence="PRESENT", text="value", metadata=None
):
    content = {
        "content_id": "body",
        "modality": "text",
        "role": "article.body.plain",
        "presence": presence,
        "attributes": {},
    }
    if presence == "PRESENT":
        content["payload"] = {"type": "inline_text", "text": text, "format": "plain"}
    return {
        "schema_version": "2.0",
        "dataset_version_id": str(version_id),
        "sample_id": sample_id,
        "contents": [content],
        "annotations": [],
        "metadata": metadata or {},
        "source": {
            "source_artifact_id": str(uuid4()),
            "source_record_id": sample_id,
            "source_row": 1,
            "locator": None,
            "attributes": {},
        },
        "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
    }


def _context(tmp_path: Path, records, metric_key: str) -> CalculatorContext:
    path = tmp_path / f"{metric_key}.jsonl"
    path.write_text("".join(json.dumps(item) + "\n" for item in records))
    return CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key=metric_key,
        metric_version="1.0.0",
        input_path=path,
        output_dir=tmp_path / f"out-{metric_key}",
        input_schema_ref="2.0",
    )


def test_structural_completeness_keeps_missing_categories_distinct(tmp_path: Path):
    version = uuid4()
    records = [
        _sample(version, "present"),
        _sample(version, "empty", text="  "),
        _sample(version, "null", presence="NULL"),
        {
            **_sample(version, "absent"),
            "contents": [
                {
                    "content_id": "other",
                    "modality": "text",
                    "role": "other",
                    "presence": "PRESENT",
                    "payload": {"type": "inline_text", "text": "x", "format": "plain"},
                    "attributes": {},
                }
            ],
        },
    ]
    result = StructuralCompletenessCalculator().execute(
        _context(tmp_path, records, "0204"), {"content_role": "article.body.plain"}
    )
    observed = {item["key"]: item["value"] for item in result.observations}
    assert observed["complete_record_ratio"] == 0.25
    assert result.diagnostics[-1]["counts"] == {
        "PRESENT": 1,
        "NULL": 1,
        "EMPTY_STRING": 1,
        "FIELD_ABSENT": 1,
    }
    lines = result.evidence[0].path.read_text().splitlines()
    assert {json.loads(line)["missing_category"] for line in lines} == {
        "NULL",
        "EMPTY_STRING",
        "FIELD_ABSENT",
    }
    assert all("text" not in json.loads(line) for line in lines)


def test_structural_completeness_requires_exactly_one_selector(tmp_path: Path):
    context = _context(tmp_path, [], "0204")
    with pytest.raises(CalculatorValidationError, match="exactly one"):
        StructuralCompletenessCalculator().validate(context, {})


def test_format_compliance_reports_basic_jsonl_and_schema_violations(tmp_path: Path):
    version = uuid4()
    valid = _sample(version, "valid")
    duplicate = _sample(version, "valid")
    wrong_version = _sample(uuid4(), "wrong-version")
    input_path = tmp_path / "0201.jsonl"
    input_path.write_text(
        "".join(
            [
                json.dumps(valid) + "\n",
                json.dumps(duplicate) + "\n",
                json.dumps(wrong_version) + "\n",
                json.dumps({"sample_id": "invalid-schema"}) + "\n",
                "not-json\n",
            ]
        )
    )
    context = CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=version,
        metric_key="0201",
        metric_version="1.0.0",
        input_path=input_path,
        output_dir=tmp_path / "out-0201",
        input_schema_ref="2.0",
    )

    result = FormatComplianceCalculator().execute(context, {})

    observed = {item["key"]: item["value"] for item in result.observations}
    assert observed == {
        "compliant_record_ratio": 0.2,
        "compliant_record_count": 1,
        "non_compliant_record_count": 4,
        "evaluable_record_count": 5,
    }
    assert result.semantic_coverage["level"] == "PARTIAL"
    assert result.evidence[0].contains_raw_content is False
    evidence = [
        json.loads(line) for line in result.evidence[0].path.read_text().splitlines()
    ]
    assert {code for item in evidence for code in item["violation_codes"]} == {
        "DUPLICATE_SAMPLE_ID",
        "DATASET_VERSION_MISMATCH",
        "SAMPLE_RECORD_SCHEMA_INVALID",
        "INVALID_JSON",
    }
    assert all("text" not in item for item in evidence)


def test_format_compliance_requires_sample_record_v2(tmp_path: Path):
    context = _context(tmp_path, [], "0201")
    invalid_context = CalculatorContext(**{**context.__dict__, "input_schema_ref": "1.0"})
    with pytest.raises(CalculatorValidationError, match="SampleRecord 2.0"):
        FormatComplianceCalculator().validate(invalid_context, {})


def test_text_completeness_applies_disclosed_basic_rules(tmp_path: Path):
    version = uuid4()
    missing = _sample(version, "missing")
    missing["contents"] = [
        {
            "content_id": "other",
            "modality": "text",
            "role": "other",
            "presence": "PRESENT",
            "payload": {"type": "inline_text", "text": "other", "format": "plain"},
            "attributes": {},
        }
    ]
    records = [
        _sample(version, "complete", text="这是长度足够且具有完整结尾的正文。"),
        _sample(version, "short", text="短文"),
        _sample(version, "truncated", text="这是长度足够但带有明显截断信号的正文内容……"),
        _sample(version, "null", presence="NULL"),
        missing,
        _sample(version, "empty", text="  \n"),
    ]

    result = TextCompletenessCalculator().execute(
        _context(tmp_path, records, "02080104"),
        {"content_role": "article.body.plain", "min_char_count": 10},
    )

    observed = {item["key"]: item["value"] for item in result.observations}
    assert observed == {
        "incomplete_text_ratio": 5 / 6,
        "incomplete_text_record_count": 5,
        "evaluable_record_count": 6,
    }
    assert result.semantic_coverage["level"] == "PARTIAL"
    evidence = [
        json.loads(line) for line in result.evidence[0].path.read_text().splitlines()
    ]
    assert {code for item in evidence for code in item["reason_codes"]} == {
        "BELOW_MIN_CHAR_COUNT",
        "POTENTIAL_TRUNCATION_SUFFIX",
        "TEXT_NULL",
        "TEXT_ROLE_MISSING",
        "TEXT_EMPTY",
    }
    assert all("text" not in item for item in evidence)


def test_text_completeness_validates_rule_parameters(tmp_path: Path):
    context = _context(tmp_path, [], "02080104")
    calculator = TextCompletenessCalculator()
    with pytest.raises(CalculatorValidationError, match="at least 1"):
        calculator.validate(context, {"min_char_count": 0})
    with pytest.raises(CalculatorValidationError, match="non-empty list"):
        calculator.validate(context, {"truncation_suffixes": []})


def test_exact_only_matches_full_exact_observations(tmp_path: Path):
    version = uuid4()
    records = [
        _sample(version, "a", text="重复  正文"),
        _sample(version, "b", text="重复 正文"),
        _sample(version, "c", text="独立正文"),
        _sample(version, "empty", text=""),
    ]
    exact = ExactTextDuplicateCalculator().execute(
        _context(tmp_path, records, "02080103"), {"content_role": "article.body.plain"}
    )
    full_context = _context(tmp_path, records, "02080103-full")
    full = TextDuplicateDegreeCalculator().execute(
        full_context, {"content_role": "article.body.plain"}
    )
    exact_values = {item["key"]: item["value"] for item in exact.observations}
    full_values = {item["key"]: item["value"] for item in full.observations}
    assert exact_values == {key: full_values[key] for key in exact_values}
    assert {item["key"] for item in exact.observations} == {
        "exact_affected_record_ratio",
        "exact_redundant_record_ratio",
    }
    assert exact.semantic_coverage["covered"] == ["normalized exact duplicate"]
