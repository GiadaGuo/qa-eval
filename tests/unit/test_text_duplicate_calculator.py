import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from qa_eval.calculators.protocol import CalculatorContext, CalculatorValidationError
from qa_eval.calculators.text_duplicate_degree import TextDuplicateDegreeCalculator


def _sample(
    version_id,
    sample_id: str,
    text: str,
    *,
    metadata=None,
    source_attributes=None,
):
    return {
        "schema_version": "2.0",
        "dataset_version_id": str(version_id),
        "sample_id": sample_id,
        "contents": [
            {
                "content_id": "body",
                "modality": "text",
                "role": "article.body.plain",
                "presence": "PRESENT",
                "payload": {"type": "inline_text", "text": text, "format": "plain"},
                "attributes": {},
            }
        ],
        "annotations": [],
        "metadata": metadata or {},
        "source": {
            "source_artifact_id": str(uuid4()),
            # This intentionally equals sample_id and must not become document_id.
            "source_record_id": sample_id,
            "source_row": 1,
            "locator": None,
            "attributes": source_attributes or {},
        },
        "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
    }


def _context(tmp_path: Path, records) -> CalculatorContext:
    input_path = tmp_path / "samples.jsonl"
    input_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    return CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key="02080103",
        metric_version="1.0.0",
        input_path=input_path,
        output_dir=tmp_path / "output",
        input_schema_ref="2.0",
    )


def test_semantic_parameter_validation_rejects_incompatible_lsh_shape(
    tmp_path: Path,
) -> None:
    context = CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key="02080103",
        metric_version="1.0.0",
        input_path=tmp_path / "input.jsonl",
        output_dir=tmp_path / "output",
        input_schema_ref="2.0",
    )

    with pytest.raises(CalculatorValidationError, match="evenly divisible") as error:
        TextDuplicateDegreeCalculator().validate(
            context,
            {"minhash_permutations": 63, "lsh_bands": 16},
        )

    assert error.value.code == "CALCULATOR_VALIDATION_ERROR"


def test_duplicate_groups_preserve_member_provenance_without_changing_counts(
    tmp_path: Path,
) -> None:
    version_id = uuid4()
    text = "共同 政策 内容 一 二 三 四 五 六 七 八 九"
    records = [
        _sample(
            version_id,
            "sample-1",
            text,
            metadata={"article_id": "document-1"},
            source_attributes={
                "origin_name": "政府网站A",
                "collection_batch": "batch-01",
            },
        ),
        _sample(
            version_id,
            "sample-2",
            text,
            metadata={"document_id": "document-2", "batch_id": "batch-02"},
            source_attributes={"source": "政府网站B"},
        ),
        _sample(version_id, "sample-3", text),
        _sample(version_id, "unique", "完全 不同 的 唯一 文本 甲 乙 丙 丁 戊"),
    ]

    output = TextDuplicateDegreeCalculator().execute(
        _context(tmp_path, records),
        {"content_role": "article.body.plain"},
    )
    evidence = json.loads((tmp_path / "output" / "duplicate_groups.json").read_text())

    exact = evidence["groups"][0]
    assert exact["sample_ids"] == ["sample-1", "sample-2", "sample-3"]
    assert exact["record_count"] == len(exact["members"]) == 3
    assert [member["sample_id"] for member in exact["members"]] == exact["sample_ids"]
    assert exact["members"] == [
        {
            "sample_id": "sample-1",
            "document_id": "document-1",
            "source": "政府网站A",
            "collection_batch": "batch-01",
        },
        {
            "sample_id": "sample-2",
            "document_id": "document-2",
            "source": "政府网站B",
            "collection_batch": "batch-02",
        },
        {
            "sample_id": "sample-3",
            "document_id": None,
            "source": None,
            "collection_batch": None,
        },
    ]

    near = next(
        group
        for group in evidence["near_duplicate_groups"]
        if {"sample-1", "sample-2", "sample-3"}.issubset(group["sample_ids"])
    )
    assert near["record_count"] == len(near["members"])
    assert [member["sample_id"] for member in near["members"]] == near["sample_ids"]
    assert near["members"][:3] == exact["members"]

    observations = {item["key"]: item for item in output.observations}
    assert observations["exact_affected_record_ratio"]["numerator"] == 3
    assert observations["exact_redundant_record_ratio"]["numerator"] == 2
    assert exact["group_id"].startswith("sha256:")
    assert exact["representative_sample_id"] == "sample-1"

    schema_path = (
        Path(__file__).parents[2]
        / "schemas/qa-eval/evidence/02080103-duplicate-groups/2.1.0.schema.json"
    )
    schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(evidence)

    legacy_shape = copy.deepcopy(evidence)
    for key in ("groups", "near_duplicate_groups"):
        for group in legacy_shape[key]:
            group.pop("members")
    # Existing consumers use the retained fields and do not require members.
    assert legacy_shape["groups"][0]["sample_ids"] == exact["sample_ids"]
    assert legacy_shape["near_duplicate_groups"][0]["sample_ids"]
