from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

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
            return [{"category": "CASE_INVOLVED_PERSON", "original_label": "涉案人员", "start": start, "end": start + 3, "probability": 0.9}]
        return []

    def close(self):
        pass


def _record(sample_id: str, body: str, *, html: bool = False):
    version = uuid4()
    contents = [{"content_id": "body-html" if html else "body-plain", "modality": "text", "role": "article.body.html" if html else "article.body.plain", "presence": "PRESENT", "payload": {"type": "inline_text", "text": body, "format": "html" if html else "plain"}}]
    if html:
        contents.append({"content_id": "body-plain", "modality": "text", "role": "article.body.plain", "presence": "PRESENT", "payload": {"type": "inline_text", "text": "duplicate representation", "format": "plain"}})
    return {"schema_version": "2.0", "dataset_version_id": str(version), "sample_id": sample_id, "contents": contents, "source": {"source_artifact_id": str(uuid4())}, "lineage": {"input_fingerprint": "sha256:" + "0" * 64}}


def _context(tmp_path: Path, rows: list[dict]):
    path = tmp_path / "records.jsonl"
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return CalculatorContext(run_id=uuid4(), stage_run_id=uuid4(), dataset_version_id=uuid4(), metric_key="0202", metric_version="2.0.0", input_path=path, output_dir=tmp_path / "out", input_schema_ref="2.0")


def _parameters():
    return {"ner_python": "/fake/python", "model_dir": "/fake/model", "vendor_src": "/fake/vendor", "model_checksum": "sha256:" + "1" * 64, "max_records": 20, "window_chars": 320, "window_overlap_chars": 64, "evidence_limit": 100}


def test_dual_channel_masking_and_html_plain_dedup(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "IsolatedUIEProvider", FakeProvider)
    body = "<p>涉案人员王某某，身份证11010519491231002X，手机13800138000。</p>"
    output = CALCULATOR.execute(_context(tmp_path, [_record("s1", body, html=True)]), _parameters())
    observed = {item["key"]: item["value"] for item in output.observations}
    assert output.status == "OK"
    assert observed["sensitive_entity_hit_count"] == 3
    assert observed["sensitive_entity_type_counts"] == {"CASE_INVOLVED_PERSON": 1, "MOBILE_PHONE_CN": 1, "PRC_RESIDENT_ID": 1}
    assert observed["affected_record_count"] == 1
    assert observed["scan_coverage_ratio"] == 1
    rows = [json.loads(line) for line in output.evidence[0].path.read_text().splitlines()]
    serialized = json.dumps(rows, ensure_ascii=False)
    assert "11010519491231002X" not in serialized
    assert "13800138000" not in serialized
    assert all(row["coordinate_space"] == "VISIBLE_TEXT_UNICODE_CODE_POINT" for row in rows)


def test_no_eligible_text_is_not_applicable(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "IsolatedUIEProvider", FakeProvider)
    row = _record("s2", "text")
    row["contents"][0]["presence"] = "NULL"
    row["contents"][0].pop("payload")
    output = CALCULATOR.execute(_context(tmp_path, [row]), _parameters())
    assert output.status == "NOT_APPLICABLE"
    assert output.observations == []
