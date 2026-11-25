"""Run the five migrated bindings through the same service as published contracts."""

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from qa_eval.application.evidence.readers import EvidenceReader
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.session import create_database_engine
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

TERM = {
    "dictionary_id": "fixture",
    "version": "1",
    "category": "FIXTURE",
    "term": "测试词",
    "enabled": True,
    "match_mode": "EXACT",
}
TITLE = "关于组织实施工作的通知"
HTML = "<p>某政发[2024]9号</p><p>第一条 总则</p><p>第二条 职责</p><p>测试词 13800138000 sample@example.invalid。本通知有效期至2024年12月31日。</p>"
DAY = "2025-01-02T00:00:00+08:00"
ROLES = {"title": "article.title", "html": "article.body.html"}


def _write(store, uri, content):
    store.prepare_write(uri).write_text(content, encoding="utf-8")
    return {"uri": uri, "sha256": store.commit(uri).sha256}


def _seed(session, store, dataset_id, schema):
    version = uuid4()
    mapping = {
        "schema_version": "2.0",
        "adapter": {"key": "fixture", "version": "1"},
        "mappings": [
            {
                "source_path": f"$.{key}",
                "action": "MAP",
                "target": {"kind": "CONTENT", "role": role, "modality": "text"},
            }
            for key, role in ROLES.items()
        ]
        + [
            {
                "source_path": "$.document_type",
                "action": "MAP",
                "target": {"kind": "METADATA", "name": "document_type"},
            }
        ],
    }
    manifest = _write(
        store,
        f"{schema}-manifest.json",
        json.dumps(
            {
                "schema_version": "2.0",
                "dataset_id": str(dataset_id),
                "dataset_version_id": str(version),
                "name": "fixture",
                "modalities": ["text"],
                "created_at": DAY,
                "source_snapshot": {
                    "source_type": "fixture",
                    "source_uri": "fixture://policy",
                    "captured_at": DAY,
                    "consistency": "FIXTURE",
                    "record_count": 2,
                },
                "field_mapping": mapping,
            }
        ),
    )
    metadata = {
        "document_type": "通知",
        "temporal": {"published_at": {"value": "2024-01-01"}},
        "document_number": {"filenumber": "某政发[2024]9号"},
        "attachments": {"url": "https://example.invalid/fixture.pdf"},
    }
    attributes = {
        "format_original_verified": True,
        "format_standard_scope_verified": True,
    }
    annotations = [
        {
            "annotation_id": "fixture",
            "task": {"key": "policy.structure.segmentation", "version": "1.0.0"},
            "targets": [{"content_id": "html"}],
            "value": {
                "schema_ref": "qa-eval://schemas/annotations/policy-structure/1.0.0",
                "nodes": [
                    {
                        "node_id": "n1",
                        "node_type": "ARTICLE",
                        "normalized_text_start": 15,
                        "normalized_text_end": 21,
                    },
                ],
            },
            "provenance": {
                "method": "IMPORTED",
                "annotator_ids": ["fixture"],
                "created_at": DAY,
            },
            "status": "FINAL",
        }
    ]
    texts = {"title": TITLE, "html": HTML}
    digest = "sha256:" + hashlib.sha256(b"fixture schema").hexdigest()
    if schema == "3.0":
        row = {
            **texts,
            **attributes,
            "annotations": annotations,
            "document_type": "通知",
            "filenumber": "某政发[2024]9号",
            "published_at": "2024-01-01",
            "attachurl": "https://example.invalid/fixture.pdf",
        }
        source = _write(
            store, "raw.jsonl", json.dumps(row) + '\n{"title":null,"html":null}\n'
        )
        refs = {
            "raw_record_manifest": {
                "dataset_version_id": str(version),
                "source_uri": source["uri"],
                "source_sha256": "sha256:" + source["sha256"],
                "schema_fingerprint": digest,
            }
        }
    else:
        rows = []
        for index in range(2):
            contents = []
            for key, role in ROLES.items():
                item = {
                    "content_id": key,
                    "role": role,
                    "modality": "text",
                    "presence": "NULL" if index else "PRESENT",
                }
                if not index:
                    item["payload"] = {
                        "type": "inline_text",
                        "text": texts[key],
                        "format": "html" if key == "html" else "plain",
                    }
                    item["attributes"] = attributes if key == "html" else {}
                contents.append(item)
            rows.append(
                {
                    "schema_version": "2.0",
                    "sample_id": str(index),
                    "dataset_version_id": str(version),
                    "contents": contents,
                    "annotations": [] if index else annotations,
                    "metadata": {} if index else metadata,
                    "source": {"source_artifact_id": str(uuid4())},
                    "lineage": {"input_fingerprint": digest, "transforms": []},
                }
            )
        refs = {
            "normalized": _write(
                store,
                "normalized.jsonl",
                "".join(json.dumps(row) + "\n" for row in rows),
            ),
            "field_mapping": mapping,
        }
    session.add(
        DatasetVersionModel(
            id=version,
            dataset_id=dataset_id,
            version=schema,
            status="PUBLISHED",
            artifact_refs={**refs, "manifest": manifest},
        )
    )
    session.commit()
    return version


@pytest.mark.parametrize("alternate", [False, True])
@pytest.mark.parametrize(
    "key,metric,version,measurement,parameters,extra_fields,native",
    [
        (
            "policy_document_format",
            "0201",
            "1.0.0",
            "normative_violation_rate",
            {"threshold": 70},
            [
                "filenumber",
                "attachurl",
                "format_original_verified",
                "format_standard_scope_verified",
                "document_type",
            ],
            True,
        ),
        (
            "sensitive_dictionary_match",
            "0202",
            "2.0.0",
            "sensitive_entity_hit_count",
            {
                "dictionary": [TERM],
                "include_content_ids": ["html"],
                "unicode_normalization": "NFC",
            },
            [],
            False,
        ),
        (
            "structured_sensitive_element_regex",
            "0202",
            "2.0.0",
            "sensitive_entity_hit_count",
            {
                "enabled_categories": ["MOBILE_PHONE"],
                "include_roles": ["article.body.html"],
            },
            [],
            False,
        ),
        (
            "policy_structure_compliance",
            "0204",
            "2.0.0",
            "parsing_success_rate",
            {"evidence_sample_limit": 1, "long_text_policy": "FULL_SCAN"},
            ["annotations", "document_type"],
            False,
        ),
        (
            "policy_effectiveness",
            "0205",
            "1.0.0",
            "expired_evidence_rate",
            {"reference_date": "2025-01-02"},
            ["document_type", "published_at"],
            True,
        ),
    ],
)
def test_m4_policy_run(
    tmp_path,
    key,
    metric,
    version,
    measurement,
    parameters,
    extra_fields,
    native,
    alternate,
):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'isolated.sqlite'}")
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id = uuid4()
    outputs = []
    evidence_payloads = []
    parameters = dict(parameters)
    if alternate:
        if key == "sensitive_dictionary_match":
            dictionary = tmp_path / "dictionary.json"
            dictionary.write_text(
                json.dumps({"terms": parameters.pop("dictionary")}), encoding="utf-8"
            )
            parameters["dictionary_path"] = str(dictionary)
        elif key == "structured_sensitive_element_regex":
            parameters["enabled_categories"] = ["EMAIL"]
        elif key == "policy_structure_compliance":
            parameters["evidence_sample_limit"] = 0
        elif key == "policy_document_format":
            parameters["threshold"] = 95
        else:
            parameters["observation_date"] = "2025-01-03"
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="fixture", modality=["text"]))
        session.commit()
        MetricRegistryService(session).sync()
        runner = EvaluationRunService(session, store)
        for schema, implementation in (("2.0", "1.0.0"), ("3.0", "1.1.0")):
            dataset_version = _seed(session, store, dataset_id, schema)
            item = {
                "key": "policy",
                "metric_key": metric,
                "metric_version": version,
                "calculator_key": key,
                "calculator_version": implementation,
                "parameters": parameters,
            }
            if schema == "3.0":
                item["input_selectors"] = {
                    field: {"field_tokens": [field]}
                    for field in [*ROLES, *extra_fields]
                }
            if native:
                item.update(
                    schema_version="1.1",
                    strategy="EVALUATE",
                    projections=[],
                    statistic_ref={
                        "contract_item_key": "policy",
                        "output_slot_id": "policy",
                        "calculator_version": implementation,
                        "statistics_key": measurement,
                    },
                    rule={"operator": "LTE", "threshold": 0},
                    severity="WARNING",
                )
            else:
                item.update(
                    measurement=measurement,
                    rule={
                        "operator": "LTE",
                        "threshold": 1,
                        "severity": "ERROR",
                        "on_missing": "UNKNOWN",
                    },
                )
            contract = (
                QualityContractService(session)
                .publish_multi(
                    QualityContractMultiPublishRequest.model_validate(
                        {
                            "name": f"{key}-{schema}",
                            "version_label": "1",
                            "dataset_version_id": dataset_version,
                            "items": [] if native else [item],
                            "observation_items": [item] if native else [],
                        }
                    )
                )
                .contract
            )
            run = runner.create(contract.contract_version_id)
            session.commit()
            completed = runner.execute(run.run_id)
            assert completed.status == "SUCCEEDED", completed.error
            if native:
                (result,) = runner.list_calculator_results(run.run_id)
                (evidence,) = result.decision_artifact_refs
                outputs.append((result.status, result.statistics))
            else:
                (result,) = runner.list_results(run.run_id)
                (evidence,) = result.evidence
                outputs.append((result.status, result.observations, result.population))
            if native:
                QualityResultService(session, store).finalize(run.run_id)
            rows = list(EvidenceReader(store).iter_jsonl(evidence))
            assert rows
            if schema == "3.0":
                origin = (
                    rows[0].get("source_origin")
                    or (
                        rows[0].get("source_origins")
                        or rows[0]["attributes"]["source_origins"]
                    )["html"]
                )
                sample = EvidenceQueryService(session, store).get_sample_by_id(
                    dataset_version, rows[0]["sample_id"]
                )
                assert sample is not None
                assert sample.meta.artifact_sha256 == origin["source_sha256"]
                assert sample.data["html"] == HTML
                assert origin["field_tokens"] == ["html"]
                assert origin["byte_start"] == 0
                assert (
                    origin["text_sha256"]
                    == "sha256:" + hashlib.sha256(HTML.encode()).hexdigest()
                )
            if key == "policy_structure_compliance":
                assert rows[0]["flags"]["segmentation_correct"]["status"] in {
                    "PASS",
                    "FAIL",
                }
            elif metric == "0202":
                assert all(0 <= row["start"] < row["end"] for row in rows)
                assert all("测试词" not in row["masked_excerpt"] for row in rows)
            for row in rows:
                for field in (
                    "sample_id",
                    "row_id",
                    "source_origin",
                    "source_origins",
                    "field_path",
                ):
                    row.pop(field, None)
                for reference in row.get("evidence_refs", []):
                    reference.pop("row_id")
                for field in (
                    "source_origins",
                    "source_record_id",
                    "source_row",
                    "source_artifact_id",
                ):
                    row.get("attributes", {}).pop(field, None)
            evidence_payloads.append(rows)
        assert outputs[0] == outputs[1]
        assert evidence_payloads[0] == evidence_payloads[1]
