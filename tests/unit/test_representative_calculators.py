import json
from pathlib import Path
from uuid import uuid4

import pytest

from qa_eval.calculators.exact_text_duplicate import ExactTextDuplicateCalculator
from qa_eval.calculators.protocol import CalculatorContext, CalculatorValidationError
from qa_eval.calculators.structural_completeness import StructuralCompletenessCalculator
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
