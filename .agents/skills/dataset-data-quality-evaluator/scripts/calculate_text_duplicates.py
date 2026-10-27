"""Deterministic exact/normalized text duplicate calculator Demo."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

from qa_eval.contracts.datasets import InlineTextPayload, SampleRecordV2

METRIC_CODE = "02080103"
ALGORITHM_VERSION = "2.0.0"
InputFormat = Literal["auto", "sample-record", "sample-record-v2", "raw"]
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u3400-\u4dbf\u4e00-\u9fff]")
PARAGRAPH_SPLIT_PATTERN = re.compile(r"(?:\r?\n\s*){2,}")


def normalize_text(text: str) -> str:
    """Apply the versioned normalization defined by the metric asset."""

    normalized = unicodedata.normalize("NFKC", text)
    return " ".join(normalized.split())


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tokenize(text: str) -> list[str]:
    """Tokenize deterministically; each Han character is one Demo token."""

    return TOKEN_PATTERN.findall(text.casefold())


def ngram_repetition(
    items: list[str] | str,
    n: int,
    min_frequency: int = 2,
) -> tuple[int, int, float | None]:
    if n < 1 or min_frequency < 2:
        raise ValueError("invalid n-gram parameters")
    grams = [tuple(items[index : index + n]) for index in range(len(items) - n + 1)]
    total = len(grams)
    if total == 0:
        return 0, 0, None
    frequencies = Counter(grams)
    repeated = sum(count for count in frequencies.values() if count >= min_frequency)
    return total, repeated, repeated / total


def normalized_paragraphs(text: str) -> list[str]:
    normalized_newlines = unicodedata.normalize("NFKC", text).replace("\r\n", "\n")
    return [
        paragraph
        for block in PARAGRAPH_SPLIT_PATTERN.split(normalized_newlines)
        if (paragraph := " ".join(block.split()))
    ]


def _hash64(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:8], "big")


def minhash_signature(shingles: set[str], num_perm: int) -> tuple[int, ...]:
    """Deterministic densified one-permutation MinHash signature."""

    if num_perm < 1:
        raise ValueError("minhash permutations must be positive")
    signature: list[int | None] = [None] * num_perm
    for shingle in shingles:
        value = _hash64(shingle)
        bucket = value % num_perm
        rank = value // num_perm
        current = signature[bucket]
        if current is None or rank < current:
            signature[bucket] = rank
    populated = [index for index, value in enumerate(signature) if value is not None]
    if not populated:
        return tuple(0 for _ in range(num_perm))
    for index, value in enumerate(signature):
        if value is not None:
            continue
        source = min(populated, key=lambda candidate: (candidate - index) % num_perm)
        signature[index] = _hash64(f"densify:{index}:{source}:{signature[source]}")
    return tuple(int(value) for value in signature)


def _near_duplicate_groups(
    signatures: list[tuple[int, ...] | None],
    features: list[dict[str, Any]],
    *,
    bands: int,
    threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    num_perm = len(next(signature for signature in signatures if signature is not None))
    if bands < 1 or num_perm % bands:
        raise ValueError("LSH bands must evenly divide MinHash permutations")
    rows_per_band = num_perm // bands
    buckets: dict[tuple[int, tuple[int, ...]], list[int]] = defaultdict(list)
    candidates: set[tuple[int, int]] = set()
    for index, signature in enumerate(signatures):
        if signature is None:
            continue
        for band in range(bands):
            start = band * rows_per_band
            key = (band, signature[start : start + rows_per_band])
            for other in buckets[key]:
                candidates.add((other, index))
            buckets[key].append(index)

    parent = list(range(len(features)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    pairs = []
    for left, right in sorted(candidates):
        left_signature, right_signature = signatures[left], signatures[right]
        assert left_signature is not None and right_signature is not None
        similarity = (
            sum(a == b for a, b in zip(left_signature, right_signature)) / num_perm
        )
        if similarity >= threshold:
            union(left, right)
            pairs.append(
                {
                    "left_sample_id": features[left]["sample_id"],
                    "right_sample_id": features[right]["sample_id"],
                    "estimated_jaccard": similarity,
                }
            )
    components: dict[int, list[int]] = defaultdict(list)
    for index, signature in enumerate(signatures):
        if signature is not None:
            components[find(index)].append(index)
    groups = []
    for members in sorted(components.values(), key=lambda value: value[0]):
        if len(members) < 2:
            continue
        group_id = f"near:{len(groups) + 1:06d}"
        for index in members:
            features[index]["near_duplicate_group_id"] = group_id
            features[index]["is_near_duplicate"] = True
        groups.append(
            {
                "group_id": group_id,
                "record_count": len(members),
                "sample_ids": [features[index]["sample_id"] for index in members],
            }
        )
    return groups, pairs


def _record_shape(record: dict[str, Any], input_format: InputFormat) -> str:
    if input_format != "auto":
        return input_format
    return "sample-record" if "data_content" in record else "raw"


def _extract_text(
    record: dict[str, Any],
    *,
    input_format: InputFormat,
    content_role: str,
    id_field: str,
    text_field: str,
) -> tuple[str, Any]:
    shape = _record_shape(record, input_format)
    if shape == "sample-record-v2":
        sample = SampleRecordV2.model_validate(record)
        matches = [
            item
            for item in sample.contents
            if item.modality == "text" and item.role == content_role
        ]
        if not matches:
            raise ValueError("MISSING_CONTENT_ROLE")
        if len(matches) > 1:
            raise ValueError("AMBIGUOUS_CONTENT_ROLE")
        [item] = matches
        if item.presence != "PRESENT":
            return sample.sample_id, None
        if not isinstance(item.payload, InlineTextPayload):
            raise ValueError("TEXT_CONTENT_REQUIRES_INLINE_TEXT_PAYLOAD")
        return sample.sample_id, item.payload.text

    id_key = "sample_id" if shape == "sample-record" else id_field
    sample_id = record.get(id_key)
    if sample_id is None or not str(sample_id).strip():
        raise ValueError("MISSING_SAMPLE_ID")

    if shape == "raw":
        if text_field not in record:
            raise ValueError("MISSING_TEXT_FIELD")
        return str(sample_id), record[text_field]

    items = record.get("data_content")
    if not isinstance(items, list):
        raise TypeError("INVALID_DATA_CONTENT")
    matches = [
        item
        for item in items
        if isinstance(item, dict)
        and item.get("media_type") == "text"
        and item.get("role") == content_role
    ]
    if not matches:
        raise ValueError("MISSING_CONTENT_ROLE")
    if len(matches) > 1:
        raise ValueError("AMBIGUOUS_CONTENT_ROLE")
    if "content" not in matches[0]:
        raise ValueError("MISSING_TEXT_FIELD")
    return str(sample_id), matches[0]["content"]


def _json_dump(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def calculate(
    input_path: Path,
    output_dir: Path,
    *,
    input_format: InputFormat = "auto",
    content_role: str = "plain_text_content",
    id_field: str = "sample_id",
    text_field: str = "text",
    char_ngram_size: int = 3,
    char_ngram_min_frequency: int = 2,
    word_ngram_size: int = 2,
    minhash_shingle_size: int = 5,
    minhash_permutations: int = 64,
    lsh_bands: int = 16,
    near_duplicate_threshold: float = 0.8,
) -> dict[str, Any]:
    """Calculate duplicate evidence and write the three Demo artifacts."""

    input_bytes = input_path.read_bytes()
    input_fingerprint = hashlib.sha256(input_bytes).hexdigest()
    features: list[dict[str, Any]] = []
    hash_members: dict[str, list[int]] = defaultdict(list)
    signatures: list[tuple[int, ...] | None] = []
    paragraph_members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    char_total = char_repeated = word_total = word_repeated = 0

    if not 0 <= near_duplicate_threshold <= 1:
        raise ValueError("near duplicate threshold must be between 0 and 1")
    if minhash_shingle_size < 1:
        raise ValueError("MinHash shingle size must be positive")
    if minhash_permutations < 1 or lsh_bands < 1:
        raise ValueError("MinHash permutations and LSH bands must be positive")
    if minhash_permutations % lsh_bands:
        raise ValueError("LSH bands must evenly divide MinHash permutations")

    for line_number, raw_line in enumerate(input_bytes.splitlines(), start=1):
        if not raw_line.strip():
            continue
        feature: dict[str, Any] = {
            "input_index": len(features),
            "sample_id": None,
            "status": "ERROR",
            "error_code": None,
            "normalized_sha256": None,
            "normalized_length": None,
            "duplicate_group_id": None,
            "is_duplicate": False,
            "is_redundant": False,
            "char_ngram_total_count": 0,
            "char_repeated_ngram_frequency_sum": 0,
            "char_rep_ratio": None,
            "word_ngram_total_count": 0,
            "word_repeated_ngram_frequency_sum": 0,
            "word_rep_ratio": None,
            "paragraph_count": 0,
            "repeated_paragraph_count": 0,
            "paragraph_rep_ratio": None,
            "near_duplicate_group_id": None,
            "is_near_duplicate": False,
        }
        try:
            decoded = json.loads(raw_line)
            if not isinstance(decoded, dict):
                raise TypeError("RECORD_NOT_OBJECT")
            sample_id, value = _extract_text(
                decoded,
                input_format=input_format,
                content_role=content_role,
                id_field=id_field,
                text_field=text_field,
            )
            feature["sample_id"] = sample_id
            if value is None:
                normalized = ""
            elif isinstance(value, str):
                normalized = normalize_text(value)
            else:
                raise ValueError("TEXT_NOT_STRING")

            if not normalized:
                feature["status"] = "EMPTY"
                feature["error_code"] = "EMPTY_TEXT"
            else:
                digest = sha256_text(normalized)
                feature["status"] = "VALID"
                feature["normalized_sha256"] = digest
                feature["normalized_length"] = len(normalized)
                hash_members[digest].append(feature["input_index"])
                sample_char_total, sample_char_repeated, char_ratio = ngram_repetition(
                    normalized,
                    char_ngram_size,
                    char_ngram_min_frequency,
                )
                tokens = tokenize(normalized)
                sample_word_total, sample_word_repeated, word_ratio = ngram_repetition(
                    tokens,
                    word_ngram_size,
                )
                feature.update(
                    {
                        "char_ngram_total_count": sample_char_total,
                        "char_repeated_ngram_frequency_sum": sample_char_repeated,
                        "char_rep_ratio": char_ratio,
                        "word_ngram_total_count": sample_word_total,
                        "word_repeated_ngram_frequency_sum": sample_word_repeated,
                        "word_rep_ratio": word_ratio,
                    }
                )
                char_total += sample_char_total
                char_repeated += sample_char_repeated
                word_total += sample_word_total
                word_repeated += sample_word_repeated

                paragraphs = normalized_paragraphs(value)
                feature["paragraph_count"] = len(paragraphs)
                for paragraph_index, paragraph in enumerate(paragraphs):
                    paragraph_members[sha256_text(paragraph)].append(
                        {
                            "feature_index": feature["input_index"],
                            "sample_id": sample_id,
                            "paragraph_index": paragraph_index,
                        }
                    )
                shingles = {
                    "\u241f".join(tokens[index : index + minhash_shingle_size])
                    for index in range(len(tokens) - minhash_shingle_size + 1)
                }
                if not shingles:
                    shingles = {normalized}
                signature = minhash_signature(shingles, minhash_permutations)
                signatures.append(signature)
        except (UnicodeDecodeError, json.JSONDecodeError):
            feature["error_code"] = "INVALID_JSON"
            feature["source_line"] = line_number
        except (TypeError, ValueError) as exc:
            feature["error_code"] = str(exc)
            feature["source_line"] = line_number
        if feature["status"] != "VALID":
            signatures.append(None)
        features.append(feature)

    groups: list[dict[str, Any]] = []
    for digest in sorted(hash_members):
        member_indexes = hash_members[digest]
        if len(member_indexes) < 2:
            continue
        group_id = f"sha256:{digest}"
        sample_ids = [features[index]["sample_id"] for index in member_indexes]
        for position, index in enumerate(member_indexes):
            features[index]["duplicate_group_id"] = group_id
            features[index]["is_duplicate"] = True
            features[index]["is_redundant"] = position > 0
        groups.append(
            {
                "group_id": group_id,
                "normalized_sha256": digest,
                "record_count": len(member_indexes),
                "representative_sample_id": sample_ids[0],
                "sample_ids": sample_ids,
            }
        )

    near_groups: list[dict[str, Any]] = []
    near_pairs: list[dict[str, Any]] = []
    if any(signature is not None for signature in signatures):
        near_groups, near_pairs = _near_duplicate_groups(
            signatures,
            features,
            bands=lsh_bands,
            threshold=near_duplicate_threshold,
        )

    paragraph_groups = []
    for digest in sorted(paragraph_members):
        occurrences = paragraph_members[digest]
        if len(occurrences) < 2:
            continue
        paragraph_groups.append(
            {
                "group_id": f"paragraph-sha256:{digest}",
                "paragraph_sha256": digest,
                "occurrence_count": len(occurrences),
                "distinct_record_count": len(
                    {occurrence["sample_id"] for occurrence in occurrences}
                ),
                "occurrences": [
                    {
                        "sample_id": occurrence["sample_id"],
                        "paragraph_index": occurrence["paragraph_index"],
                    }
                    for occurrence in occurrences
                ],
            }
        )
        for occurrence in occurrences:
            features[occurrence["feature_index"]]["repeated_paragraph_count"] += 1

    for feature in features:
        paragraph_count = feature["paragraph_count"]
        if paragraph_count:
            feature["paragraph_rep_ratio"] = (
                feature["repeated_paragraph_count"] / paragraph_count
            )

    input_record_count = len(features)
    valid_record_count = sum(item["status"] == "VALID" for item in features)
    anomaly_count = input_record_count - valid_record_count
    duplicate_record_count = sum(group["record_count"] for group in groups)
    redundant_record_count = sum(group["record_count"] - 1 for group in groups)
    paragraph_total_count = sum(item["paragraph_count"] for item in features)
    repeated_paragraph_count = sum(
        group["occurrence_count"] for group in paragraph_groups
    )
    redundant_paragraph_count = sum(
        group["occurrence_count"] - 1 for group in paragraph_groups
    )

    def ratio(numerator: int) -> float | None:
        return None if valid_record_count == 0 else numerator / valid_record_count

    metric_result = {
        "metric_code": METRIC_CODE,
        "algorithm_version": ALGORITHM_VERSION,
        "coverage": "PARTIAL_EXACT_NGRAM_MINHASH_LSH_AND_PARAGRAPH_HASH",
        "input_record_count": input_record_count,
        "valid_record_count": valid_record_count,
        "duplicate_group_count": len(groups),
        "duplicate_record_count": duplicate_record_count,
        "redundant_record_count": redundant_record_count,
        "affected_record_ratio": ratio(duplicate_record_count),
        "redundant_record_ratio": ratio(redundant_record_count),
        "char_ngram_size": char_ngram_size,
        "char_ngram_min_frequency": char_ngram_min_frequency,
        "char_ngram_total_count": char_total,
        "char_repeated_ngram_frequency_sum": char_repeated,
        "char_rep_ratio": None if char_total == 0 else char_repeated / char_total,
        "word_ngram_size": word_ngram_size,
        "word_tokenizer": "stdlib_unicode_lexical_han_character_tokens_v1",
        "word_ngram_total_count": word_total,
        "word_repeated_ngram_frequency_sum": word_repeated,
        "word_rep_ratio": None if word_total == 0 else word_repeated / word_total,
        "minhash": {
            "variant": "densified_one_permutation_minhash",
            "shingle_size": minhash_shingle_size,
            "permutations": minhash_permutations,
            "lsh_bands": lsh_bands,
            "estimated_jaccard_threshold": near_duplicate_threshold,
            "near_duplicate_group_count": len(near_groups),
            "near_duplicate_pair_count": len(near_pairs),
            "near_duplicate_record_count": sum(
                group["record_count"] for group in near_groups
            ),
        },
        "paragraph_total_count": paragraph_total_count,
        "paragraph_duplicate_group_count": len(paragraph_groups),
        "repeated_paragraph_count": repeated_paragraph_count,
        "redundant_paragraph_count": redundant_paragraph_count,
        "paragraph_affected_ratio": (
            None
            if paragraph_total_count == 0
            else repeated_paragraph_count / paragraph_total_count
        ),
        "paragraph_redundant_ratio": (
            None
            if paragraph_total_count == 0
            else redundant_paragraph_count / paragraph_total_count
        ),
        "ratio_denominator": "valid_record_count",
        "empty_text_policy": "ANOMALY_EXCLUDED_FROM_DENOMINATOR_AND_GROUPS",
        "anomaly_count": anomaly_count,
        "input_fingerprint": f"sha256:{input_fingerprint}",
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "sample_features.jsonl").write_text(
        "".join(
            json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
            for item in features
        ),
        encoding="utf-8",
    )
    _json_dump(
        output_dir / "duplicate_groups.json",
        {
            "metric_code": METRIC_CODE,
            "algorithm_version": ALGORITHM_VERSION,
            "groups": groups,
            "near_duplicate_groups": near_groups,
            "near_duplicate_pairs": near_pairs,
            "paragraph_groups": paragraph_groups,
        },
    )
    _json_dump(output_dir / "metric_result.json", metric_result)
    return metric_result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calculate deterministic normalized full-text duplicates."
    )
    parser.add_argument("input", type=Path, help="Local JSONL input path")
    parser.add_argument("output_dir", type=Path, help="Artifact output directory")
    parser.add_argument(
        "--input-format",
        choices=("auto", "sample-record", "sample-record-v2", "raw"),
        default="auto",
    )
    parser.add_argument("--content-role", default="plain_text_content")
    parser.add_argument("--id-field", default="sample_id")
    parser.add_argument("--text-field", default="text")
    parser.add_argument("--char-ngram-size", type=int, default=3)
    parser.add_argument("--char-ngram-min-frequency", type=int, default=2)
    parser.add_argument("--word-ngram-size", type=int, default=2)
    parser.add_argument("--minhash-shingle-size", type=int, default=5)
    parser.add_argument("--minhash-permutations", type=int, default=64)
    parser.add_argument("--lsh-bands", type=int, default=16)
    parser.add_argument("--near-duplicate-threshold", type=float, default=0.8)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = calculate(
            args.input,
            args.output_dir,
            input_format=args.input_format,
            content_role=args.content_role,
            id_field=args.id_field,
            text_field=args.text_field,
            char_ngram_size=args.char_ngram_size,
            char_ngram_min_frequency=args.char_ngram_min_frequency,
            word_ngram_size=args.word_ngram_size,
            minhash_shingle_size=args.minhash_shingle_size,
            minhash_permutations=args.minhash_permutations,
            lsh_bands=args.lsh_bands,
            near_duplicate_threshold=args.near_duplicate_threshold,
        )
    except (OSError, ValueError) as exc:
        print(f"calculation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
