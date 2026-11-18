from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import jsonschema
import pytest

from qa_eval.calculators.protocol import CalculatorContext, CalculatorValidationError
from qa_eval.calculators.structured_sensitive_elements import (
    CALCULATOR,
    StructuredDetector,
    valid_luhn,
    valid_national_id,
    valid_uscc,
)


def _record(sample_id: str, texts: list[tuple[str, str, str]]) -> dict:
    return {
        "schema_version": "2.0",
        "dataset_version_id": str(uuid4()),
        "sample_id": sample_id,
        "contents": [
            {
                "content_id": str(content_id),
                "modality": "text",
                "role": role,
                "presence": "PRESENT",
                "payload": {"type": "inline_text", "text": text, "format": format_},
            }
            for content_id, (role, text, format_) in enumerate(texts, 1)
        ],
        "source": {"source_artifact_id": str(uuid4())},
        "lineage": {"input_fingerprint": "sha256:" + "0" * 64},
    }


def _context(tmp_path: Path, rows: list[dict]) -> CalculatorContext:
    input_path = tmp_path / "records.jsonl"
    input_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key="0202",
        metric_version="2.0.0",
        input_path=input_path,
        output_dir=tmp_path / "out",
        input_schema_ref="2.0",
    )


def _rows(output) -> list[dict]:
    return [
        json.loads(line) for line in output.evidence[0].path.read_text().splitlines()
    ]


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("11010519491231002X", True),
        ("11010519491331002X", False),
        ("110105194912310021", False),
    ],
)
def test_national_id_birth_date_and_check_digit(value, valid):
    assert valid_national_id(value) is valid


@pytest.mark.parametrize(
    ("value", "valid"), [("4111111111111111", True), ("4111111111111112", False)]
)
def test_luhn(value, valid):
    assert valid_luhn(value) is valid


@pytest.mark.parametrize(
    ("value", "valid"), [("91350211M000100Y46", True), ("91350211M000100Y44", False)]
)
def test_uscc(value, valid):
    assert valid_uscc(value) is valid


def test_seven_categories_boundaries_masking_html_and_determinism(tmp_path):
    text = (
        "手机13800138000 身份证11010519491231002X 邮箱a.user@example.com "
        "卡号4111111111111111 信用代码91350211M000100Y46 IP 192.0.2.1 与2001:db8::1 "
        "家庭住址：北京市朝阳区建国路88号阳光小区3栋2单元1201室"
    )
    context = _context(
        tmp_path,
        [
            _record(
                "s1",
                [
                    (
                        "article.body.html",
                        f"<p>{text}</p><script>13912345678</script>",
                        "html",
                    )
                ],
            )
        ],
    )
    first = CALCULATOR.execute(context, {})
    first_rows = _rows(first)
    second = CALCULATOR.execute(context, {})
    second_rows = _rows(second)
    categories = {row["category"] for row in first_rows}
    assert categories == {
        "MOBILE_PHONE",
        "NATIONAL_ID",
        "EMAIL",
        "BANK_CARD",
        "USCC",
        "IP_ADDRESS",
        "HOME_ADDRESS",
    }
    assert first.observations == second.observations
    assert first_rows == second_rows
    assert all(
        row["coordinate_space"] == "VISIBLE_TEXT_UNICODE_CODE_POINT"
        for row in first_rows
    )
    serialized = json.dumps(first_rows, ensure_ascii=False)
    for sensitive in (
        "13800138000",
        "11010519491231002X",
        "4111111111111111",
        "北京市朝阳区建国路88号",
    ):
        assert sensitive not in serialized
    schema = json.loads(
        (
            Path(__file__).parents[2]
            / "schemas/qa-eval/evidence/sensitive-entity-hits/1.1.0.schema.json"
        ).read_text()
    )
    for row in first_rows:
        jsonschema.validate(row, schema)


def test_invalid_boundaries_false_positives_and_address_policy(tmp_path):
    text = (
        "x13800138000y 11010519491331002X 4111111111111112 91350211M000100Y44 "
        "999.2.3.4 2001:db8::1zz 北京市朝阳区 普通地名三里屯 "
        "某有限公司办公地址：北京市朝阳区建国路88号3栋201室 公共场所北京市朝阳区公园路8号"
    )
    output = CALCULATOR.execute(
        _context(tmp_path, [_record("s1", [("body", text, "plain")])]),
        {
            "enabled_categories": [
                "MOBILE_PHONE",
                "NATIONAL_ID",
                "EMAIL",
                "BANK_CARD",
                "IP_ADDRESS",
                "HOME_ADDRESS",
            ]
        },
    )
    assert _rows(output) == []


def test_address_variants_multi_field_dedup_and_category_toggle(tmp_path):
    texts = [
        ("home", "居住地址：朝阳区幸福路8号2栋1单元301室", "plain"),
        ("shipping", "收件地址：幸福花园5栋2单元901室", "plain"),
        ("other", "手机号13800138000，重复13800138000", "plain"),
    ]
    output = CALCULATOR.execute(
        _context(tmp_path, [_record("s1", texts)]),
        {"enabled_categories": ["HOME_ADDRESS"]},
    )
    rows = _rows(output)
    assert len(rows) == 2
    assert {row["content_id"] for row in rows} == {"1", "2"}
    assert all(row["category"] == "HOME_ADDRESS" for row in rows)


def test_empty_missing_and_invalid_configuration(tmp_path):
    row = _record("s1", [("body", "", "plain")])
    output = CALCULATOR.execute(_context(tmp_path, [row]), {})
    assert output.status == "NOT_APPLICABLE"
    assert output.observations == []
    with pytest.raises(CalculatorValidationError):
        CALCULATOR.validate(
            _context(tmp_path, [_record("s2", [("body", "ok", "plain")])]),
            {"enabled_categories": ["UNKNOWN"]},
        )


def test_ip_versions_and_long_numeric_boundary():
    detector = StructuredDetector(frozenset({"IP_ADDRESS", "BANK_CARD"}))
    hits = list(detector.hits("192.0.2.1 2001:db8::1 192.0.2.999 141111111111111116"))
    assert [(hit.category, hit.metadata.get("ip_version")) for hit in hits] == [
        ("IP_ADDRESS", "IPv4"),
        ("IP_ADDRESS", "IPv6"),
    ]
