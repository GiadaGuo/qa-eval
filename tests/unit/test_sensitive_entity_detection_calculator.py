from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import jsonschema

import qa_eval.calculators.sensitive_entity_detection.calculator as module
from qa_eval.calculators.protocol import CalculatorContext
from qa_eval.calculators.sensitive_entity_detection import CALCULATOR


class FakeProvider:
    calls = 0

    def __init__(self, **_):
        pass

    def infer(self, text: str, request_id: str):
        self.calls += 1
        if "涉案人员王某某" in text:
            start = text.index("王某某")
            return [
                {
                    "category": "CASE_INVOLVED_PERSON",
                    "original_label": "涉案人员",
                    "start": start,
                    "end": start + 3,
                    "probability": 0.9,
                }
            ]
        return []

    def close(self):
        pass


def _record(sample_id: str, body: str, *, html: bool = False):
    version = uuid4()
    contents = [
        {
            "content_id": "body-html" if html else "body-plain",
            "modality": "text",
            "role": "article.body.html" if html else "article.body.plain",
            "presence": "PRESENT",
            "payload": {
                "type": "inline_text",
                "text": body,
                "format": "html" if html else "plain",
            },
        }
    ]
    if html:
        contents.append(
            {
                "content_id": "body-plain",
                "modality": "text",
                "role": "article.body.plain",
                "presence": "PRESENT",
                "payload": {
                    "type": "inline_text",
                    "text": "duplicate representation",
                    "format": "plain",
                },
            }
        )
    return {
        "schema_version": "2.0",
        "dataset_version_id": str(version),
        "sample_id": sample_id,
        "contents": contents,
        "source": {"source_artifact_id": str(uuid4())},
        "lineage": {"input_fingerprint": "sha256:" + "0" * 64},
    }


def _context(tmp_path: Path, rows: list[dict]):
    path = tmp_path / "records.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key="0202",
        metric_version="2.0.0",
        input_path=path,
        output_dir=tmp_path / "out",
        input_schema_ref="2.0",
    )


def _parameters():
    return {
        "ner_python": "/fake/python",
        "model_dir": "/fake/model",
        "vendor_src": "/fake/vendor",
        "model_checksum": "sha256:" + "1" * 64,
        "max_records": 20,
        "window_chars": 320,
        "window_overlap_chars": 64,
        "evidence_limit": 100,
    }


def test_dual_channel_masking_and_html_plain_dedup(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "IsolatedUIEProvider", FakeProvider)
    body = "<p>涉案人员王某某，身份证11010519491231002X，手机13800138000。</p>"
    output = CALCULATOR.execute(
        _context(tmp_path, [_record("s1", body, html=True)]), _parameters()
    )
    observed = {item["key"]: item["value"] for item in output.observations}
    assert output.status == "OK"
    assert observed["sensitive_entity_hit_count"] == 3
    assert observed["sensitive_entity_type_counts"] == {
        "CASE_INVOLVED_PERSON": 1,
        "MOBILE_PHONE_CN": 1,
        "PRC_RESIDENT_ID": 1,
    }
    assert observed["affected_record_count"] == 1
    assert observed["scan_coverage_ratio"] == 1
    rows = [
        json.loads(line) for line in output.evidence[0].path.read_text().splitlines()
    ]
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "11010519491231002X" not in serialized
    assert "13800138000" not in serialized
    assert all(
        row["coordinate_space"] == "VISIBLE_TEXT_UNICODE_CODE_POINT" for row in rows
    )
    assert all(row["offset_unit"] == "UNICODE_CODE_POINT" for row in rows)
    assert all(row["row_id"].startswith("sha256:") for row in rows)
    assert all(row["text_version"].startswith("sha256:") for row in rows)
    assert len({row["row_id"] for row in rows}) == len(rows)
    assert output.evidence[0].schema_ref.endswith("/1.1.0")
    assert output.evidence[0].attributes["completeness_status"] == "COMPLETE"
    assert output.evidence[0].attributes["detected_count"] == 3
    assert output.evidence[0].attributes["emitted_count"] == 3
    assert output.evidence[0].attributes["truncated"] is False
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "schemas/qa-eval/evidence/sensitive-entity-hits/1.1.0.schema.json"
    )
    schema = json.loads(schema_path.read_text())
    for row in rows:
        jsonschema.validate(row, schema)


def test_no_eligible_text_is_not_applicable(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "IsolatedUIEProvider", FakeProvider)
    row = _record("s2", "text")
    row["contents"][0]["presence"] = "NULL"
    row["contents"][0].pop("payload")
    output = CALCULATOR.execute(_context(tmp_path, [row]), _parameters())
    assert output.status == "NOT_APPLICABLE"
    assert output.observations == []


def test_max_records_marks_result_partial(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "IsolatedUIEProvider", FakeProvider)
    parameters = _parameters()
    parameters["max_records"] = 1
    output = CALCULATOR.execute(
        _context(tmp_path, [_record("s1", "clean"), _record("s2", "clean")]),
        parameters,
    )
    assert output.status == "PARTIAL"
    assert output.status_detail == {
        "partial_scope": True,
        "scope_reason": "MAX_RECORDS",
        "evaluated_record_count": 1,
        "input_record_count": 2,
    }
    assert output.population["exclusions"] == [
        {"reason": "SAMPLED_OUT_MAX_RECORDS", "count": 1}
    ]


def test_legacy_evidence_limit_does_not_truncate_persisted_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "IsolatedUIEProvider", FakeProvider)
    parameters = _parameters()
    parameters["evidence_limit"] = 1
    output = CALCULATOR.execute(
        _context(tmp_path, [_record("s1", "电话13800138000，备用13912345678")]),
        parameters,
    )
    rows = output.evidence[0].path.read_text().splitlines()
    assert len(rows) == 2
    assert output.evidence[0].record_count == 2
    assert output.evidence[0].attributes == {
        "detected_count": 2,
        "emitted_count": 2,
        "truncated": False,
        "truncation_reason": None,
        "completeness_status": "COMPLETE",
        "counting_unit": "MERGED_ENTITY_HIT",
        "one_record_per_detected_fact": True,
        "row_identity_version": "1.0",
        "location_schema_version": "1.0",
        "legacy_evidence_limit_ignored": 1,
        "total_hit_count": 2,
        "written_hit_count": 2,
        "evidence_capped": False,
    }
