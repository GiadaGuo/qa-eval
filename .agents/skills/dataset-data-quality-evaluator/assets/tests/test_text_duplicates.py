from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "calculate_text_duplicates.py"
SPEC = importlib.util.spec_from_file_location("calculate_text_duplicates", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def run_raw(tmp_path: Path, records: list[dict], output_name: str = "out"):
    input_path = tmp_path / f"{output_name}.jsonl"
    output_dir = tmp_path / output_name
    write_jsonl(input_path, records)
    result = MODULE.calculate(input_path, output_dir, input_format="raw")
    return result, output_dir


def test_exact_whitespace_unicode_and_different_text(tmp_path: Path):
    records = [
        {"sample_id": "a", "text": "Alpha"},
        {"sample_id": "b", "text": "Alpha"},
        {"sample_id": "c", "text": "A\t B\nC"},
        {"sample_id": "d", "text": "A B C"},
        {"sample_id": "e", "text": "ＡＢＣ"},
        {"sample_id": "f", "text": "ABC"},
        {"sample_id": "g", "text": "Different"},
    ]

    result, output_dir = run_raw(tmp_path, records)

    assert result["input_record_count"] == 7
    assert result["valid_record_count"] == 7
    assert result["duplicate_group_count"] == 3
    assert result["duplicate_record_count"] == 6
    assert result["redundant_record_count"] == 3
    assert result["affected_record_ratio"] == pytest.approx(6 / 7)
    assert result["redundant_record_ratio"] == pytest.approx(3 / 7)
    assert result["anomaly_count"] == 0

    features = read_jsonl(output_dir / "sample_features.jsonl")
    by_id = {item["sample_id"]: item for item in features}
    assert by_id["a"]["normalized_sha256"] == by_id["b"]["normalized_sha256"]
    assert by_id["c"]["normalized_sha256"] == by_id["d"]["normalized_sha256"]
    assert by_id["e"]["normalized_sha256"] == by_id["f"]["normalized_sha256"]
    assert by_id["g"]["is_duplicate"] is False


def test_empty_and_missing_text_are_anomalies_and_not_duplicates(tmp_path: Path):
    result, output_dir = run_raw(
        tmp_path,
        [
            {"sample_id": "empty", "text": " \n\t "},
            {"sample_id": "null", "text": None},
            {"sample_id": "missing"},
            {"sample_id": "valid", "text": "only valid text"},
        ],
    )

    assert result["input_record_count"] == 4
    assert result["valid_record_count"] == 1
    assert result["anomaly_count"] == 3
    assert result["duplicate_group_count"] == 0
    assert result["affected_record_ratio"] == 0
    assert result["redundant_record_ratio"] == 0
    assert read_json(output_dir / "duplicate_groups.json")["groups"] == []

    features = read_jsonl(output_dir / "sample_features.jsonl")
    assert [item["status"] for item in features] == [
        "EMPTY",
        "EMPTY",
        "ERROR",
        "VALID",
    ]


def test_sample_record_data_content_role(tmp_path: Path):
    records = [
        {
            "sample_id": "s1",
            "data_content": [
                {
                    "content_id": "plain_text_content",
                    "media_type": "text",
                    "role": "plain_text_content",
                    "content": "匿名文章",
                }
            ],
        },
        {
            "sample_id": "s2",
            "data_content": [
                {
                    "content_id": "plain_text_content",
                    "media_type": "text",
                    "role": "plain_text_content",
                    "content": "匿名文章",
                }
            ],
        },
    ]
    input_path = tmp_path / "samples.jsonl"
    output_dir = tmp_path / "out"
    write_jsonl(input_path, records)

    result = MODULE.calculate(input_path, output_dir, input_format="sample-record")

    assert result["duplicate_record_count"] == 2
    assert result["redundant_record_count"] == 1


def test_all_samples_identical_is_high_dataset_duplication(tmp_path: Path):
    result, output_dir = run_raw(
        tmp_path,
        [{"sample_id": f"s{index}", "text": "same"} for index in range(4)],
    )

    assert result["duplicate_group_count"] == 1
    assert result["duplicate_record_count"] == 4
    assert result["redundant_record_count"] == 3
    assert result["affected_record_ratio"] == 1.0
    assert result["redundant_record_ratio"] == 0.75

    features = read_jsonl(output_dir / "sample_features.jsonl")
    assert all(item["is_duplicate"] for item in features)
    assert sum(item["is_redundant"] for item in features) == 3


def test_repeated_runs_are_byte_for_byte_deterministic(tmp_path: Path):
    records = [
        {"sample_id": "x", "text": "repeat"},
        {"sample_id": "y", "text": "repeat"},
        {"sample_id": "z", "text": "unique"},
    ]
    input_path = tmp_path / "input.jsonl"
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    write_jsonl(input_path, records)

    first = MODULE.calculate(input_path, first_dir, input_format="raw")
    second = MODULE.calculate(input_path, second_dir, input_format="raw")

    assert first == second
    for filename in (
        "sample_features.jsonl",
        "duplicate_groups.json",
        "metric_result.json",
    ):
        assert (first_dir / filename).read_bytes() == (
            second_dir / filename
        ).read_bytes()


def test_no_valid_records_produces_null_ratios(tmp_path: Path):
    result, _ = run_raw(
        tmp_path,
        [{"sample_id": "empty", "text": ""}, {"sample_id": "missing"}],
    )

    assert result["valid_record_count"] == 0
    assert result["affected_record_ratio"] is None
    assert result["redundant_record_ratio"] is None


def test_character_and_word_ngram_repetition(tmp_path: Path):
    result, output_dir = run_raw(
        tmp_path,
        [{"sample_id": "ngram", "text": "abcabc one two one two"}],
    )
    [feature] = read_jsonl(output_dir / "sample_features.jsonl")

    expected_char = MODULE.ngram_repetition(
        MODULE.normalize_text("abcabc one two one two"), 3, 2
    )
    expected_word = MODULE.ngram_repetition(
        MODULE.tokenize("abcabc one two one two"), 2, 2
    )
    assert feature["char_ngram_total_count"] == expected_char[0]
    assert feature["char_repeated_ngram_frequency_sum"] == expected_char[1]
    assert feature["char_rep_ratio"] == expected_char[2]
    assert feature["word_ngram_total_count"] == expected_word[0]
    assert feature["word_repeated_ngram_frequency_sum"] == expected_word[1]
    assert feature["word_rep_ratio"] == expected_word[2]
    assert result["char_rep_ratio"] == expected_char[2]
    assert result["word_rep_ratio"] == expected_word[2]


def test_minhash_lsh_finds_full_text_near_duplicates(tmp_path: Path):
    input_path = tmp_path / "near.jsonl"
    output_dir = tmp_path / "near"
    write_jsonl(
        input_path,
        [
            {
                "sample_id": "near-a",
                "text": "alpha beta gamma delta epsilon zeta eta theta iota kappa",
            },
            {
                "sample_id": "near-b",
                "text": "alpha beta gamma delta epsilon zeta eta theta iota changed",
            },
            {"sample_id": "other", "text": "red green blue black white"},
        ],
    )

    result = MODULE.calculate(
        input_path,
        output_dir,
        input_format="raw",
        minhash_shingle_size=2,
        minhash_permutations=64,
        lsh_bands=32,
        near_duplicate_threshold=0.5,
    )

    evidence = read_json(output_dir / "duplicate_groups.json")
    assert result["minhash"]["near_duplicate_group_count"] == 1
    assert evidence["near_duplicate_groups"][0]["sample_ids"] == [
        "near-a",
        "near-b",
    ]
    assert evidence["near_duplicate_pairs"][0]["estimated_jaccard"] >= 0.5


def test_paragraph_hash_groups_repeated_paragraphs(tmp_path: Path):
    result, output_dir = run_raw(
        tmp_path,
        [
            {"sample_id": "p1", "text": "shared paragraph\n\nunique one"},
            {"sample_id": "p2", "text": "shared   paragraph\n\nunique two"},
        ],
    )

    evidence = read_json(output_dir / "duplicate_groups.json")
    assert result["paragraph_total_count"] == 4
    assert result["paragraph_duplicate_group_count"] == 1
    assert result["repeated_paragraph_count"] == 2
    assert result["redundant_paragraph_count"] == 1
    assert result["paragraph_affected_ratio"] == 0.5
    assert result["paragraph_redundant_ratio"] == 0.25
    assert evidence["paragraph_groups"][0]["distinct_record_count"] == 2
