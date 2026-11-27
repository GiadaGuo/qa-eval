"""Run the five migrated bindings through the same service as published contracts."""

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
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
from qa_eval.infrastructure.db.models.registry import MetricCalculatorBindingModel
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


def _seed(session, store, dataset_id, schema, raw_rows=None):
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
                    "record_count": 2 if raw_rows is None else len(raw_rows),
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
            store,
            "raw.jsonl",
            "".join(
                json.dumps(item) + "\n"
                for item in (
                    [row, {"title": None, "html": None}]
                    if raw_rows is None
                    else raw_rows
                )
            ),
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
            "compliant_record_ratio",
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
                    rule={"operator": "GTE", "threshold": 0.9}
                    if key == "policy_document_format"
                    else {"operator": "LTE", "threshold": 0},
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


@pytest.mark.parametrize("case", ["pass", "fail", "empty", "historical"])
def test_policy_format_native_scoring_and_issues(tmp_path, case):
    empty = case == "empty"
    engine = create_database_engine(f"sqlite:///{tmp_path / 'policy.sqlite'}")
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id = uuid4()
    # The format score and the independently anchored normative rule can disagree.
    body = (
        "<p>{number}</p><p>为促进工作落实，制定本通知。</p>"
        "<h2>一、组织实施</h2><p>请各单位认真组织开展相关工作。</p><h2>二、反馈</h2>"
    )
    rows = [
        {
            "title": TITLE,
            "html": body.format(number=number),
            "filenumber": number,
            "format_original_verified": True,
            "format_standard_scope_verified": True,
        }
        for number in ("某政发〔2024〕9号", "某政发[2024]9号")
    ] + [{"title": None, "html": None}]
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="fixture", modality=["text"]))
        session.commit()
        MetricRegistryService(session).sync()
        version = _seed(session, store, dataset_id, "3.0", [] if empty else rows)
        item = {
            "key": "policy",
            "metric_key": "0201",
            "metric_version": "1.0.0",
            "calculator_key": "policy_document_format",
            "calculator_version": "1.1.0",
            "input_selectors": {
                field: {"field_tokens": [field]}
                for field in (
                    "title",
                    "html",
                    "filenumber",
                    "format_original_verified",
                    "format_standard_scope_verified",
                )
            },
            "schema_version": "1.1",
            "strategy": "EVALUATE",
            "projections": [],
            "parameters": {"threshold": 100 if case == "fail" else 90, "by_type": {}},
            "statistic_ref": {
                "contract_item_key": "policy",
                "output_slot_id": "policy",
                "calculator_version": "1.1.0",
                "statistics_key": "compliant_record_ratio",
            },
            "rule": {"operator": "GTE", "threshold": 0.3},
            "severity": "ERROR",
        }
        request = {
            "name": "0201 fixture",
            "version_label": "1",
            "dataset_version_id": version,
            "observation_items": [item],
        }
        publisher = QualityContractService(session)
        wrong_stat = {
            **item["statistic_ref"],
            "statistics_key": "normative_violation_rate",
        }
        with pytest.raises(ValueError, match="primary measurement"):
            publisher.publish_multi(
                QualityContractMultiPublishRequest.model_validate(
                    {
                        **request,
                        "observation_items": [{**item, "statistic_ref": wrong_stat}],
                    }
                )
            )
        with pytest.raises(ValueError, match="Invalid Calculator parameters"):
            publisher.publish_multi(
                QualityContractMultiPublishRequest.model_validate(
                    {
                        **request,
                        "observation_items": [
                            {**item, "parameters": {"by_type": "通知"}}
                        ],
                    }
                )
            )
        if case == "historical":
            binding = session.scalar(
                select(MetricCalculatorBindingModel).where(
                    MetricCalculatorBindingModel.calculator_key
                    == "policy_document_format",
                    MetricCalculatorBindingModel.calculator_version == "1.1.0",
                )
            )
            current = binding.document
            binding.document = {
                **current,
                "compatibility": {"sample_record_schema": "3.0"},
            }
            session.flush()
            request["observation_items"] = [{**item, "statistic_ref": wrong_stat}]
        contract = publisher.publish_multi(
            QualityContractMultiPublishRequest.model_validate(request)
        ).contract
        if case == "historical":
            binding.document = current
            session.commit()
        runner = EvaluationRunService(session, store)
        run = runner.create(contract.contract_version_id)
        session.commit()
        completed = runner.execute(run.run_id)
        assert completed.status == "SUCCEEDED", completed.error
        assert runner.list_results(run.run_id) == []
        (result,) = runner.list_calculator_results(run.run_id)
        assert len(result.statistics) == 13
        service = QualityResultService(session, store)
        report = service.finalize(run.run_id)
        (section,) = report.native_evaluations
        expected = None if empty else 0 if case == "fail" else 1 / 3
        assert result.statistics["compliant_record_ratio"] == expected
        assert section.evaluation.metric_value == expected
        assert section.evaluation.status == (
            "NOT_EVALUATED" if empty else "FAIL" if case == "fail" else "PASS"
        )
        scored = service.rebuild_operational_report(run.run_id)
        axis = next(axis for axis in scored.radar if axis.dimension == "0201")
        score = None if empty else 0 if case == "fail" else 33.3333
        assert axis.estimated_score == score
        assert (
            next(
                entry for entry in report.guide_coverage if entry.metric_key == "0201"
            ).score
            == score
        )
        total, issues = service.list_issues(run.run_id)
        assert total == section.issue_count == (0 if empty else 1)
        if not empty:
            assert issues[0].code == "POLICY_FORMAT_NORMATIVE_DEFECT"
            (artifact,) = result.decision_artifact_refs
            decisions = list(EvidenceReader(store).iter_jsonl(artifact))
            assert [row["status"] for row in decisions] == (
                ["NOT_EVALUATED", "0", "NOT_EVALUATED"]
                if case == "fail"
                else ["1", "NOT_EVALUATED", "NOT_EVALUATED"]
            )
            assert issues[0].evidence[0].locator["row_id"] == decisions[1]["row_id"]
            assert decisions[1]["locations"]
            evidence = EvidenceQueryService(session, store)
            locations = evidence.issue_locations(issues[0].issue_id)["locations"]
            assert any(
                item["precision"] == "EXACT"
                and "某政发[2024]9号"
                in item["text"][item["highlight"]["start"] : item["highlight"]["end"]]
                for item in locations
            )


@pytest.mark.parametrize("threshold,empty", [(1, False), (2, False), (1, True)])
def test_sensitive_dictionary_record_decisions(tmp_path, threshold, empty):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'dictionary.sqlite'}")
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id = uuid4()
    rows = [
        {"title": "普通标题", "html": "<p>普通正文</p>"},
        {"title": None, "html": "<p>甲乙</p>"},
        {"title": "甲乙", "html": "<p>甲乙甲乙</p>"},
        {"title": "", "html": None},
    ]
    terms = [
        dict(dictionary_id="fixture", version="1", category=category,
             term=term, enabled=True, match_mode="EXACT")
        for category, term in [("a", "甲乙"), ("b", "甲乙"), ("b", "甲")]
    ]
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="fixture", modality=["text"]))
        session.commit()
        MetricRegistryService(session).sync()
        version = _seed(session, store, dataset_id, "3.0", [] if empty else rows)
        item = {
            "key": "dictionary", "metric_key": "0202", "metric_version": "2.0.0",
            "calculator_key": "sensitive_dictionary_match", "calculator_version": "1.2.0",
            "input_selectors": {name: {"field_tokens": [source]} for name, source in
                                [("title", "title"), ("html", "html"), ("plain", "html")]},
            "schema_version": "1.1", "strategy": "EVALUATE", "projections": [],
            "parameters": {"record_hit_threshold": threshold, "dictionary": terms},
            "statistic_ref": {"contract_item_key": "dictionary", "output_slot_id": "dictionary",
                              "calculator_version": "1.2.0", "statistics_key": "compliant_record_ratio"},
            "rule": {"operator": "GTE", "threshold": 0.25}, "severity": "ERROR",
        }
        contract = QualityContractService(session).publish_multi(
            QualityContractMultiPublishRequest.model_validate({
                "name": "0202 fixture", "version_label": "1", "dataset_version_id": version,
                "observation_items": [item],
            })
        ).contract
        runner = EvaluationRunService(session, store)
        run = runner.create(contract.contract_version_id, task_type="SINGLE_METRIC")
        session.commit()
        completed = runner.execute(run.run_id)
        assert completed.status == "SUCCEEDED", completed.error
        (result,) = runner.list_calculator_results(run.run_id)
        service = QualityResultService(session, store)
        report = service.finalize(run.run_id)
        (section,) = report.native_evaluations
        (artifact,) = result.decision_artifact_refs
        decisions = list(EvidenceReader(store).iter_jsonl(artifact))
        stats = result.statistics
        if empty:
            assert decisions == []
            assert stats["compliant_record_ratio"] is None
            assert section.evaluation.status == "NOT_EVALUATED"
            return
        compliant = threshold
        assert [d["status"] for d in decisions] == ["1", "0" if threshold == 1 else "1", "0", "NOT_EVALUATED"]
        assert [d["attributes"]["actual_value"] for d in decisions] == [0, 1, 3, 0]
        assert stats["input_count"] == stats["compliant_record_count"] + stats["non_compliant_record_count"] + stats["not_evaluated_count"] == 4
        assert stats["sensitive_entity_hit_count"] == 4
        assert stats["compliant_record_ratio"] == compliant / 4
        assert stats["evaluated_compliance_ratio"] == compliant / 3
        assert stats["evaluable_rate"] == 3 / 4
        assert section.evaluation.status == "PASS"
        total, issues = service.list_issues(run.run_id)
        assert total == stats["non_compliant_record_count"]
        for issue in issues:
            locations = EvidenceQueryService(session, store).issue_locations(issue.issue_id)["locations"]
            assert locations and any(location.get("highlight") for location in locations)
        scored = service.rebuild_operational_report(run.run_id)
        axis = next(axis for axis in scored.radar if axis.dimension == "0202")
        assert axis.estimated_score == compliant / 4 * 100
