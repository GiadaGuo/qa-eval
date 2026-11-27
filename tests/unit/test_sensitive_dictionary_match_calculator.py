from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from qa_eval.calculators.protocol import CalculatorContext, CalculatorValidationError
from qa_eval.calculators.sensitive_dictionary_match import CALCULATOR


def _entry(term: str, category: str = "SYNTHETIC", **updates) -> dict:
    value = {
        "dictionary_id": "test-only",
        "version": "1",
        "category": category,
        "term": term,
        "enabled": True,
        "match_mode": "EXACT",
        "term_id": f"t-{term}",
    }
    value.update(updates)
    return value


def _context(tmp_path: Path, texts: list[str]) -> CalculatorContext:
    path = tmp_path / "records.jsonl"
    rows = []
    for index, text in enumerate(texts):
        rows.append(
            {
                "schema_version": "2.0",
                "dataset_version_id": str(uuid4()),
                "sample_id": f"s{index}",
                "contents": [
                    {
                        "content_id": "body",
                        "modality": "text",
                        "role": "body",
                        "presence": "PRESENT",
                        "payload": {
                            "type": "inline_text",
                            "text": text,
                            "format": "plain",
                        },
                    }
                ],
                "source": {"source_artifact_id": str(uuid4())},
                "lineage": {"input_fingerprint": "sha256:" + "0" * 64},
            }
        )
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


def _rows(output) -> list[dict]:
    return [
        json.loads(line) for line in output.evidence[0].path.read_text().splitlines()
    ]


def test_chinese_english_mixed_overlap_positions_and_categories(tmp_path):
    terms = [_entry("蓝色"), _entry("blue", "EN"), _entry("蓝色blue", "MIXED")]
    output = CALCULATOR.execute(
        _context(tmp_path, ["前缀蓝色blue后缀"]), {"dictionary": terms}
    )
    rows = _rows(output)
    assert [(row["category"], row["start"], row["end"]) for row in rows] == [
        ("SYNTHETIC", 2, 4),
        ("MIXED", 2, 8),
        ("EN", 4, 8),
    ]
    assert all(row["masked_excerpt"] == "[DICTIONARY_MATCH_REDACTED]" for row in rows)


def test_case_unicode_normalization_and_original_coordinate_mapping(tmp_path):
    output = CALCULATOR.execute(
        _context(tmp_path, ["ＡＢc Straße"]),
        {
            "dictionary": [_entry("abc"), _entry("STRASSE", "EN")],
            "case_sensitive": False,
            "unicode_normalization": "NFKC",
        },
    )
    rows = _rows(output)
    assert [(row["start"], row["end"]) for row in rows] == [(0, 3), (4, 10)]


def test_disabled_empty_bad_and_duplicate_dictionary(tmp_path):
    context = _context(tmp_path, ["蓝色"])
    disabled = _entry("蓝色", enabled=False)
    output = CALCULATOR.execute(context, {"dictionary": [disabled]})
    assert _rows(output) == []
    assert CALCULATOR.execute(context, {"dictionary": []}).status == "OK"
    with pytest.raises(CalculatorValidationError, match="duplicate"):
        CALCULATOR.validate(context, {"dictionary": [_entry("蓝色"), _entry("蓝色")]})
    with pytest.raises(CalculatorValidationError, match="match_mode"):
        CALCULATOR.validate(
            context, {"dictionary": [_entry("蓝色", match_mode="FUZZY")]}
        )


def test_same_term_dedup_but_distinct_term_facts_preserved(tmp_path):
    terms = [
        _entry("aba", "A", term_id="one"),
        _entry("ba", "A", term_id="two"),
        _entry("ba", "B", term_id="three"),
    ]
    rows = _rows(
        CALCULATOR.execute(_context(tmp_path, ["ababa"]), {"dictionary": terms})
    )
    assert len(rows) == 6
    assert len({row["row_id"] for row in rows}) == 6


def test_local_dictionary_version_hash_and_reproducibility(tmp_path):
    dictionary = tmp_path / "dictionary.json"
    dictionary.write_text(
        json.dumps({"terms": [_entry("合成词")]}, ensure_ascii=False), encoding="utf-8"
    )
    context = _context(tmp_path, ["一个合成词。"])
    first = CALCULATOR.execute(context, {"dictionary_path": str(dictionary)})
    rows_first = _rows(first)
    second = CALCULATOR.execute(context, {"dictionary_path": str(dictionary)})
    assert rows_first == _rows(second)
    assert first.observations == second.observations
    assert first.diagnostics[0]["dictionary_versions"] == ["test-only@1"]
    assert first.diagnostics[0]["dictionary_content_hash"].startswith("sha256:")


@pytest.mark.parametrize("parameters", [
    {},
    {"dictionary_path": "/nonexistent/dictionary.json"},
    {"dictionary": [{"term": "fixture"}]},
    {"dictionary": [_entry("fixture")], "record_hit_threshold": 0},
    {"dictionary": [_entry("fixture")], "record_hit_threshold": True},
])
def test_record_decision_rejects_invalid_configuration(tmp_path, parameters):
    from dataclasses import replace

    from qa_eval.calculators.sensitive_dictionary_decision import SensitiveDictionaryDecisionCalculator

    context = replace(_context(tmp_path, []), input_schema_ref="3.0", resolved_records=lambda: iter(()))
    with pytest.raises(CalculatorValidationError):
        SensitiveDictionaryDecisionCalculator().execute(context, parameters)
