import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.policy_structure_compliance import (
    PolicyStructureComplianceCalculator,
)
from qa_eval.calculators.protocol import CalculatorContext


def _content(content_id: str, role: str, text: str, fmt: str = "html") -> dict:
    return {
        "content_id": content_id,
        "modality": "text",
        "role": role,
        "presence": "PRESENT",
        "payload": {
            "type": "inline_text",
            "text": text,
            "format": fmt,
            "language": "zh",
        },
        "attributes": {"source_paths": ["$.content"]},
    }


def _sample(
    version, sample_id: str, body: str, *, title: str = "通知", annotation=None
) -> dict:
    return {
        "schema_version": "2.0",
        "dataset_version_id": str(version),
        "sample_id": sample_id,
        "contents": [
            _content("title", "article.title", title, "plain"),
            _content("body", "article.body.html", body),
        ],
        "annotations": [annotation] if annotation else [],
        "metadata": {},
        "source": {
            "source_artifact_id": str(uuid4()),
            "source_record_id": sample_id,
            "source_row": 1,
            "locator": f"test://{sample_id}",
            "attributes": {},
        },
        "lineage": {"input_fingerprint": "sha256:" + "1" * 64, "transforms": []},
    }


def _annotation(nodes: list[dict]) -> dict:
    normalized_nodes = [
        {"node_id": item.get("node_id", f"node-{index + 1}"), **item}
        for index, item in enumerate(nodes)
    ]
    return {
        "annotation_id": "gt-1",
        "task": {"key": "policy.structure.segmentation", "version": "1.0.0"},
        "targets": [{"content_id": "body"}],
        "value": {
            "schema_ref": "qa-eval://schemas/annotations/policy-structure/1.0.0",
            "nodes": normalized_nodes,
        },
        "provenance": {
            "method": "HUMAN",
            "annotator_ids": ["tester"],
            "created_at": datetime.now(UTC).isoformat(),
        },
        "status": "FINAL",
        "attributes": {},
    }


def _run(tmp_path: Path, rows: list[dict], parameters=None):
    version = uuid4()
    source = tmp_path / "samples.jsonl"
    source.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    context = CalculatorContext(
        uuid4(), uuid4(), version, "0204", "2.0.0", source, tmp_path / "out", "2.0"
    )
    output = PolicyStructureComplianceCalculator().execute(context, parameters or {})
    evidence = [
        json.loads(line) for line in output.evidence[0].path.read_text().splitlines()
    ]
    observations = {item["key"]: item for item in output.observations}
    return output, observations, evidence


def test_single_parse_four_flags_and_independent_denominators(tmp_path: Path) -> None:
    version = uuid4()
    body = "<p>第一条 总则</p><p>第二条 职责</p>"
    nodes = [
        {"node_type": "ARTICLE", "normalized_text_start": 0, "normalized_text_end": 6},
        {"node_type": "ARTICLE", "normalized_text_start": 7, "normalized_text_end": 13},
    ]
    rows = [
        _sample(version, "legal", body, annotation=_annotation(nodes)),
        _sample(version, "generic", "<p>现将有关事项通知如下。</p>"),
    ]
    output, observations, evidence = _run(tmp_path, rows)
    assert output.status == "PARTIAL"
    assert set(evidence[0]["flags"]) == {
        "parsing_success",
        "numbering_valid",
        "segmentation_correct",
        "path_locatable",
    }
    assert observations["parsing_success_rate"]["denominator"] == 2
    assert observations["numbering_compliance_rate"]["denominator"] == 1
    assert observations["segmentation_correct_rate"]["denominator"] == 1
    assert observations["path_locatable_rate"]["denominator"] == 1
    assert observations["exact_location_rate"]["value"] == 1
    assert evidence[1]["flags"]["numbering_valid"]["status"] == "NOT_APPLICABLE"
    assert evidence[1]["flags"]["segmentation_correct"]["status"] == "UNAVAILABLE"


def test_numbering_defects_count_events_without_double_counting(tmp_path: Path) -> None:
    version = uuid4()
    rows = [
        _sample(
            version,
            "bad",
            "<p>第一条 A</p><p>第四条 D</p><p>第四条 D2</p><p>第三条 C</p>",
        )
    ]
    _output, observations, evidence = _run(tmp_path, rows)
    assert observations["numbering_defect_count"]["value"] == 3
    assert observations["skipped_number_count"]["value"] == 1
    assert observations["missing_number_count"]["value"] == 2
    assert observations["duplicate_number_count"]["value"] == 1
    assert observations["reverse_number_count"]["value"] == 1
    violations = evidence[0]["flags"]["numbering_valid"]["violations"]
    assert [item["violation_code"] for item in violations] == [
        "NUMBER_GAP",
        "NUMBER_DUPLICATE",
        "NUMBER_REVERSE",
    ]


def test_evidence_cap_does_not_change_aggregate(tmp_path: Path) -> None:
    version = uuid4()
    rows = [
        _sample(version, str(index), "<p>第一条 A</p><p>第三条 C</p>")
        for index in range(5)
    ]
    _output, observations, evidence = _run(tmp_path, rows, {"evidence_sample_limit": 2})
    assert len(evidence) == 2
    assert observations["numbering_defect_count"]["value"] == 5


def test_long_html_rejected_without_fabricated_rates(tmp_path: Path) -> None:
    version = uuid4()
    rows = [_sample(version, "long", "<p>" + "正" * 1001 + "</p>")]
    _output, observations, evidence = _run(
        tmp_path, rows, {"max_html_length_chars": 1000}
    )
    assert "parsing_success_rate" not in observations
    assert evidence[0]["flags"]["parsing_success"] == {
        "diagnostics": {},
        "reason_code": "HTML_LENGTH_LIMIT_EXCEEDED",
        "status": "UNAVAILABLE",
        "violations": [],
    }


def test_ground_truth_mismatch_is_fail_not_runtime_error(tmp_path: Path) -> None:
    version = uuid4()
    annotation = _annotation(
        [{"node_type": "ARTICLE", "normalized_text_start": 0, "normalized_text_end": 2}]
    )
    rows = [_sample(version, "mismatch", "<p>第一条 总则</p>", annotation=annotation)]
    output, _observations, evidence = _run(tmp_path, rows)
    assert output.status == "OK"
    result = evidence[0]["flags"]["segmentation_correct"]
    assert result["status"] == "FAIL"
    assert result["diagnostics"] == {"fn": 1, "fp": 1, "tp": 0}


def test_ground_truth_hierarchy_is_compared(tmp_path: Path) -> None:
    version = uuid4()
    nodes = [
        {
            "node_id": "chapter",
            "node_type": "CHAPTER",
            "normalized_text_start": 0,
            "normalized_text_end": 6,
        },
        {
            "node_id": "article",
            "node_type": "ARTICLE",
            "normalized_text_start": 7,
            "normalized_text_end": 13,
        },
    ]
    rows = [
        _sample(
            version,
            "wrong-parent",
            "<p>第一章 总则</p><p>第一条 内容</p>",
            annotation=_annotation(nodes),
        )
    ]
    _output, _observations, evidence = _run(tmp_path, rows)
    assert evidence[0]["flags"]["segmentation_correct"]["status"] == "FAIL"


def test_invalid_record_is_isolated_and_population_closes(tmp_path: Path) -> None:
    version = uuid4()
    valid = _sample(version, "valid", "<p>无编号通知</p>")
    source = tmp_path / "samples.jsonl"
    source.write_text(
        json.dumps(valid, ensure_ascii=False) + "\n{}\n", encoding="utf-8"
    )
    context = CalculatorContext(
        uuid4(), uuid4(), version, "0204", "2.0.0", source, tmp_path / "out", "2.0"
    )
    output = PolicyStructureComplianceCalculator().execute(context, {})
    assert output.population == {
        "input_count": 2,
        "evaluated_count": 1,
        "excluded_count": 1,
        "exclusions": [{"reason": "sample_processing_error", "count": 1}],
    }


def test_code_digest_covers_every_package_module() -> None:
    calculator = PolicyStructureComplianceCalculator()
    declared = {path.resolve() for path in calculator.source_paths()}
    package = (
        Path(__file__).resolve().parents[2]
        / "src/qa_eval/calculators/policy_structure_compliance"
    )
    assert declared == {path.resolve() for path in package.glob("*.py")}
    assert package.joinpath("parsers.py").resolve() in declared
    assert TrustedCalculatorLoader.code_digest(calculator).startswith("sha256:")
