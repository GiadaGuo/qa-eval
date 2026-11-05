from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from qa_eval.calculators.protocol import CalculatorContext, CalculatorValidationError
from qa_eval.calculators.sample_text_encoding_compliance import (
    SampleTextEncodingComplianceCalculator,
)


def _content(
    content_id: str,
    role: str,
    text: str | None,
    *,
    fmt="plain",
    modality="text",
    presence=None,
    source="$.content",
):
    presence = presence or ("PRESENT" if text is not None else "NULL")
    item = {
        "content_id": content_id,
        "modality": modality,
        "role": role,
        "presence": presence,
        "attributes": {"source_paths": [source], "transform": "identity"},
    }
    if presence == "PRESENT":
        if modality == "text":
            item["payload"] = {"type": "inline_text", "text": text, "format": fmt}
        else:
            item["payload"] = {
                "type": "structured",
                "data": {},
                "schema_ref": "test://schema",
            }
    return item


def _sample(version, sample_id, contents):
    return {
        "schema_version": "2.0",
        "dataset_version_id": str(version),
        "sample_id": sample_id,
        "contents": contents,
        "annotations": [],
        "metadata": {},
        "source": {
            "source_artifact_id": str(uuid4()),
            "source_record_id": sample_id,
            "source_row": 1,
            "attributes": {},
        },
        "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
    }


def _context(tmp_path: Path, records):
    path = tmp_path / "samples.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in records),
        encoding="utf-8",
    )
    return CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key="0201",
        metric_version="1.0.0",
        input_path=path,
        output_dir=tmp_path / "out",
        input_schema_ref="2.0",
    )


def _observed(result):
    return {item["key"]: item["value"] for item in result.observations}


def _evidence(result):
    return [
        json.loads(line) for line in result.evidence[0].path.read_text().splitlines()
    ]


def test_normal_plain_html_english_emoji_and_whitespace_pass(tmp_path: Path):
    version = uuid4()
    records = [
        _sample(
            version,
            "ok",
            [
                _content(
                    "title", "article.title", "关于某项政策的通知\n\tEnglish 123 😀"
                ),
                _content(
                    "html",
                    "article.body.html",
                    '<p style="font-family: Calibri;">正文&nbsp;<a href="https://example.test/a?x=1">内容</a></p>',
                    fmt="html",
                ),
            ],
        )
    ]
    result = SampleTextEncodingComplianceCalculator().execute(
        _context(tmp_path, records), {}
    )
    assert _observed(result)["compliant_record_ratio"] == 1.0
    assert _evidence(result) == []


def test_detects_all_violation_types_and_exact_offsets(tmp_path: Path):
    version = uuid4()
    text = "ab�cd\ud800ef\x01锟斤拷"
    result = SampleTextEncodingComplianceCalculator().execute(
        _context(
            tmp_path,
            [_sample(version, "bad", [_content("body", "article.body.plain", text)])],
        ),
        {},
    )
    rows = _evidence(result)
    by_code = {row["violation_code"]: row for row in rows}
    assert {
        "REPLACEMENT_CHARACTER",
        "INVALID_UNICODE_SURROGATE",
        "SUSPICIOUS_CONTROL_CHARACTER",
        "SUSPICIOUS_MOJIBAKE",
    } <= set(by_code)
    assert (
        by_code["REPLACEMENT_CHARACTER"]["start_offset"],
        by_code["REPLACEMENT_CHARACTER"]["end_offset"],
    ) == (2, 3)
    assert by_code["REPLACEMENT_CHARACTER"]["matched_text"] == "�"
    assert by_code["INVALID_UNICODE_SURROGATE"]["start_offset"] == 5


def test_html_mojibake_uses_visible_coordinates_without_css_false_positive(
    tmp_path: Path,
):
    version = uuid4()
    html = "<style>.x{font-family:Calibri}</style><p>正文锟斤拷</p>"
    result = SampleTextEncodingComplianceCalculator().execute(
        _context(
            tmp_path,
            [
                _sample(
                    version,
                    "html",
                    [_content("html", "article.body.html", html, fmt="html")],
                )
            ],
        ),
        {},
    )
    row = next(
        row
        for row in _evidence(result)
        if row["violation_code"] == "SUSPICIOUS_MOJIBAKE"
    )
    assert row["coordinate_space"] == "visible_text"
    assert row["matched_text"] == "锟斤拷"


def test_record_content_counts_and_source_traceability_are_distinct(tmp_path: Path):
    version = uuid4()
    contents = [
        _content("html", "article.body.html", "<p>�</p>", fmt="html"),
        _content("plain", "article.body.plain", "�", source="$.content"),
    ]
    result = SampleTextEncodingComplianceCalculator().execute(
        _context(tmp_path, [_sample(version, "same", contents)]), {}
    )
    stats = result.diagnostics[-1]
    assert stats["affected_record_count"] == 1
    assert stats["affected_content_count"] == 2
    assert stats["violation_count"] == 2
    rows = _evidence(result)
    assert {row["content_id"] for row in rows} == {"html", "plain"}
    assert all(row["source_paths"] == ["$.content"] for row in rows)


def test_selectors_skips_and_not_applicable(tmp_path: Path):
    version = uuid4()
    records = [
        _sample(
            version,
            "none",
            [
                _content("missing", "article.title", None, presence="MISSING"),
                _content("structured", "metadata", "ignored", modality="structured"),
            ],
        )
    ]
    result = SampleTextEncodingComplianceCalculator().execute(
        _context(tmp_path, records), {"include_roles": ["article.title"]}
    )
    assert result.status == "NOT_APPLICABLE"
    assert "compliant_record_ratio" not in _observed(result)
    assert result.status_detail["reason_code"] == "NO_ELIGIBLE_INLINE_TEXT"


def test_include_roles_and_evidence_limit_apply_without_capping_counts(tmp_path: Path):
    version = uuid4()
    records = [
        _sample(
            version,
            "limited",
            [
                _content("title", "article.title", "���"),
                _content("body", "article.body.plain", "�"),
            ],
        )
    ]
    result = SampleTextEncodingComplianceCalculator().execute(
        _context(tmp_path, records),
        {"include_roles": ["article.title"], "max_evidence_per_record": 1},
    )
    assert len(_evidence(result)) == 1
    assert result.diagnostics[-1]["violation_count"] == 3
    assert result.diagnostics[-1]["scanned_content_count"] == 1


def test_validation_rejects_invalid_parameters_and_schema(tmp_path: Path):
    calculator = SampleTextEncodingComplianceCalculator()
    context = _context(tmp_path, [])
    with pytest.raises(CalculatorValidationError, match="boolean"):
        calculator.validate(context, {"check_mojibake": 1})
    with pytest.raises(CalculatorValidationError, match="at least 1"):
        calculator.validate(context, {"max_evidence_per_record": 0})
    with pytest.raises(CalculatorValidationError, match="SampleRecord 2.0"):
        calculator.validate(
            CalculatorContext(**{**context.__dict__, "input_schema_ref": "1.0"}), {}
        )
