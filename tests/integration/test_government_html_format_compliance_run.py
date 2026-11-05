from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractPublishRequest,
    QualityContractService,
)
from qa_eval.calculators.government_html_format_compliance import (
    GovernmentHtmlFormatComplianceCalculator,
)
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

CLEAN_HTML = (
    '<div><h3 style="font-size:22px">关于印发某某办法的通知</h3>'
    '<p style="font-size:16px;font-family:仿宋">各市、县人民政府：'
    "现将《某某办法》印发给你们，请认真贯彻执行。</p>"
    '<p style="font-size:16px;line-height:28px;text-indent:2em;font-family:仿宋">'
    "第一条&nbsp;为规范管理制定本办法，详见"
    '<a href="https://www.example.gov.cn/policy/1">政策原文</a>。</p>'
    '<p style="text-align:right">二〇二四年三月一日</p></div>'
)

BAD_HTML = (
    '<div class="TRS_Editor">'
    '<p style="font-size:16px">正文内容'
    + "一二三四五六七八九十" * 4
    + "</p>"
    + '<p style="font-size:6px">这一段字号极小用来触发字号异常检测逻辑。</p>'
    + "<p></p>" * 6
    + '<div class="breadcrumb">当前位置：首页</div>'
    + "</span></div>"
)


def _sample(version_id, sample_id: str, html: str) -> str:
    return json.dumps(
        {
            "schema_version": "2.0",
            "dataset_version_id": str(version_id),
            "sample_id": sample_id,
            "contents": [
                {
                    "content_id": "body-html",
                    "modality": "text",
                    "role": "article.body.html",
                    "presence": "PRESENT",
                    "payload": {
                        "type": "inline_text",
                        "text": html,
                        "format": "html",
                    },
                    "attributes": {
                        "source_paths": ["$.content"],
                        "transform": "identity",
                    },
                }
            ],
            "annotations": [],
            "metadata": {},
            "source": {
                "source_artifact_id": str(uuid4()),
                "source_record_id": sample_id,
                "source_row": 1,
                "attributes": {},
            },
            "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
        },
        ensure_ascii=True,
    )


def test_calculator_is_registered_and_loadable():
    with Session(
        create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    ) as session:
        Base.metadata.create_all(session.bind)
        service = MetricRegistryService(session)
        service.sync()
        descriptor = service.get_calculator(
            "government_html_format_compliance", "1.0.0"
        )
        assert descriptor is not None
        assert descriptor.availability.status == "AVAILABLE"
        calculator = TrustedCalculatorLoader().load(descriptor)
        assert isinstance(calculator, GovernmentHtmlFormatComplianceCalculator)
        bindings = service.list_bindings("0201")
        assert {(binding.calculator_key, binding.priority) for binding in bindings} >= {
            ("government_html_format_compliance", 80)
        }
        # 既有 0201 计算器保持注册，互不覆盖
        assert service.get_calculator("format_compliance", "1.0.0") is not None
        assert (
            service.get_calculator("sample_text_encoding_compliance", "1.0.0")
            is not None
        )


def test_dataset_version_to_metric_result_and_evidence_artifact(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id, version_id = uuid4(), uuid4()
    relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
    staging = store.prepare_write(relative)
    staging.write_text(
        "\n".join(
            [
                _sample(version_id, "ok", CLEAN_HTML),
                _sample(version_id, "bad", BAD_HTML),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    normalized = store.commit(relative)

    with Session(engine) as session:
        session.add(
            DatasetModel(id=dataset_id, name="gov-html-test", modality=["text"])
        )
        session.add(
            DatasetVersionModel(
                id=version_id,
                dataset_id=dataset_id,
                version="v1",
                status="PUBLISHED",
                content_fingerprint=f"sha256:{normalized.sha256}",
                artifact_refs={
                    "manifest": {"uri": "manifest.json"},
                    "normalized": {"uri": normalized.uri, "sha256": normalized.sha256},
                    "field_mapping": {
                        "mappings": [
                            {
                                "action": "MAP",
                                "target": {
                                    "kind": "CONTENT",
                                    "role": "article.body.html",
                                    "modality": "text",
                                },
                            }
                        ]
                    },
                },
            )
        )
        session.flush()
        MetricRegistryService(session).sync()
        contract = (
            QualityContractService(session)
            .publish(
                QualityContractPublishRequest.model_validate(
                    {
                        "name": "BG-02-01 gate",
                        "version_label": "v1",
                        "dataset_version_id": str(version_id),
                        "metric_key": "0201",
                        "metric_version": "1.0.0",
                        "measurement": "compliant_record_ratio",
                        "calculator_key": "government_html_format_compliance",
                        "calculator_version": "1.0.0",
                        "parameters": {"include_roles": ["article.body.html"]},
                        "rule": {
                            "operator": "GTE",
                            "threshold": 0.9,
                            "severity": "ERROR",
                            "on_missing": "UNKNOWN",
                        },
                    }
                )
            )
            .contract
        )
        service = EvaluationRunService(session, store)
        run = service.create(contract.contract_version_id)
        session.commit()
        executed = service.execute(run.run_id)

        result = service.list_results(run.run_id)[0]
        values = {item.key: item.value for item in result.observations}
        evidence = result.evidence[0]
        rows = [
            json.loads(line)
            for line in store.read(evidence.artifact.uri).decode().splitlines()
        ]

        assert executed.status == "SUCCEEDED", executed.error
        assert result.status == "OK"
        assert result.population.evaluated_count == 2
        # 默认 CONFIRMED_ONLY 口径：bad 记录有孤立 </span> 与连续空块的确定性违规
        assert values["compliant_record_ratio"] == 0.5
        assert values["non_compliant_record_count"] == 1
        assert values["evaluable_record_count"] == 2
        assert evidence.kind == "GOVERNMENT_HTML_FORMAT_VIOLATIONS"
        assert evidence.summary.attributes["rule_version"] == "1.0.0"
        assert evidence.summary.attributes["rule_config_fingerprint"].startswith(
            "sha256:"
        )
        by_sample = {row["sample_id"]: row for row in rows}
        assert set(by_sample) == {"bad"}
        codes = {row["violation_code"] for row in rows}
        assert {
            "HTML_STRUCTURE_ANOMALY",
            "FONT_SIZE_OUTLIER",
            "EXCESSIVE_EMPTY_BLOCKS",
            "HTML_LAYOUT_RESIDUE",
        } <= codes
        assert all(row["role"] == "article.body.html" for row in rows)
        assert all(row["dom_path"] for row in rows)
