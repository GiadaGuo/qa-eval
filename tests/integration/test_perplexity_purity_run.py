"""02080101 / 02080106 接入现有评测闭环的集成测试。

覆盖：
- 契约发布（Calculator Binding + 参数 Schema 校验）→ Logical Plan 校验 →
  Physical Plan DIRECT Stage → Evaluation Run → MetricResult 持久化 →
  RuleEvaluation / QualityReport 生成；
- 多指标（02080103 + 02080101 + 02080106）在同一 Run 中以多个 DIRECT Stage 执行；
- KenLM 模型缺失时 Stage 失败、MetricResult=ERROR、RuleEvaluation=METRIC_ERROR，
  不产生伪造 PPL。
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

FIXTURE_MODEL = Path(__file__).parents[1] / "fixtures" / "kenlm" / "test-zh-char.arpa"

NORMAL = "我们坚持改革开放的政策，不断完善社会主义市场经济体制。详见 http://example.com/policy。"
# 乱码 + 广告标记：同一条记录命中两条规则，用于验证计数去重口径
GARBLED = "锟斤拷锟斤拷改革意见全文补充通知，更多详情请加微信 kefu_2020"
HTML = '<p>正文</p><table class="x"><tr><td>数据</td></tr></table>&nbsp;'
AD = "想了解更多请加微信 kefu_2020 备注「资料」，限时特价！"
PAGE = "……正文内容……\n第 3 页\n继续正文"
FLUENT = "我们坚持改革开放的政策"
DISORDERED = "政我建坚设化的现策开"


def _sample(version_id, sample_id: str, text: str | None) -> str:
    content = {
        "content_id": "body",
        "modality": "text",
        "role": "article.body.plain",
        "presence": "PRESENT" if text is not None else "NULL",
        "attributes": {},
    }
    if text is not None:
        content["payload"] = {
            "type": "inline_text",
            "text": text,
            "format": "plain",
        }
    return json.dumps(
        {
            "schema_version": "2.0",
            "dataset_version_id": str(version_id),
            "sample_id": sample_id,
            "contents": [content],
            "annotations": [],
            "metadata": {},
            "source": {
                "source_artifact_id": str(uuid4()),
                "source_record_id": sample_id,
                "source_row": 1,
                "locator": None,
                "attributes": {},
            },
            "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
        },
        ensure_ascii=False,
    )


def _prepare(tmp_path: Path, texts: dict[str, str | None]):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    dataset_id, version_id = uuid4(), uuid4()
    relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
    content = "\n".join(
        _sample(version_id, sample_id, text) for sample_id, text in texts.items()
    ) + "\n"
    staging = store.prepare_write(relative)
    staging.write_text(content, encoding="utf-8")
    normalized = store.commit(relative)
    with Session(engine) as session:
        session.add(DatasetModel(id=dataset_id, name="articles", modality=["text"]))
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
                                    "role": "article.body.plain",
                                },
                            }
                        ]
                    },
                },
            )
        )
        session.flush()
        MetricRegistryService(session).sync()
        session.commit()
    return engine, store, version_id


def _publish_contract(session, version_id, *, ppl_parameters=None):
    return QualityContractService(session).publish_multi(
        QualityContractMultiPublishRequest.model_validate(
            {
                "name": "text quality gate",
                "version_label": "v1",
                "dataset_version_id": str(version_id),
                "items": [
                    {
                        "key": "duplicate",
                        "metric_key": "02080103",
                        "metric_version": "1.0.0",
                        "measurement": "exact_affected_record_ratio",
                        "calculator_key": "exact_text_duplicate",
                        "calculator_version": "1.0.0",
                        "parameters": {"content_role": "article.body.plain"},
                        "rule": {
                            "operator": "LTE",
                            "threshold": 0.5,
                            "severity": "ERROR",
                            "on_missing": "UNKNOWN",
                        },
                    },
                    {
                        "key": "perplexity",
                        "metric_key": "02080101",
                        "metric_version": "1.0.0",
                        "measurement": "high_perplexity_record_ratio",
                        "calculator_key": "text_perplexity",
                        "calculator_version": "1.0.0",
                        "parameters": {
                            "content_role": "article.body.plain",
                            "model_path": str(FIXTURE_MODEL),
                            "high_perplexity_threshold": 50.0,
                            **(ppl_parameters or {}),
                        },
                        "rule": {
                            "operator": "LTE",
                            "threshold": 0.5,
                            "severity": "WARNING",
                            "on_missing": "UNKNOWN",
                        },
                    },
                    {
                        "key": "purity",
                        "metric_key": "02080106",
                        "metric_version": "1.0.0",
                        "measurement": "interference_record_ratio",
                        "calculator_key": "text_purity",
                        "calculator_version": "1.0.0",
                        "parameters": {"content_role": "article.body.plain"},
                        "rule": {
                            "operator": "LTE",
                            "threshold": 0.5,
                            "severity": "ERROR",
                            "on_missing": "UNKNOWN",
                        },
                    },
                ],
            }
        )
    )


def test_multi_metric_run_executes_and_persists_all_three_results(tmp_path: Path):
    engine, store, version_id = _prepare(
        tmp_path,
        {
            "normal": NORMAL,
            "fluent": FLUENT,
            "disordered": DISORDERED,
            "garbled": GARBLED,
            "html": HTML,
            "ad": AD,
            "page": PAGE,
            "empty": "  \n",
            "null": None,
        },
    )
    with Session(engine) as session:
        contract = _publish_contract(session, version_id)
        service = EvaluationRunService(session, store)
        run = service.create(contract.contract.contract_version_id)
        session.commit()
        executed = service.execute(run.run_id)

        stage_runs = service.list_stage_runs(run.run_id)
        results = service.list_results(run.run_id)
        rules = QualityResultService(session, store).list_rules(run.run_id)
        report = QualityResultService(session, store).get_report(run.run_id)

        assert executed.status == "SUCCEEDED", executed.error
        assert len(stage_runs) == 3
        assert all(stage.status == "SUCCEEDED" for stage in stage_runs)
        assert {stage.calculator["key"] for stage in stage_runs} == {
            "exact_text_duplicate",
            "text_perplexity",
            "text_purity",
        }
        by_metric = {result.metric.key: result for result in results}
        assert set(by_metric) == {"02080103", "02080101", "02080106"}
        assert all(result.status == "OK" for result in results)

        # 02080101：真实 PPL 统计；乱序文本 PPL 高于流畅文本；空/NULL 被排除
        ppl = by_metric["02080101"]
        ppl_values = {item.key: item.value for item in ppl.observations}
        assert ppl_values["evaluable_record_count"] == 7
        assert ppl_values["mean_perplexity"] > 0
        evidence_rows = [
            json.loads(line)
            for line in store.read(
                next(
                    item.artifact.uri
                    for item in ppl.evidence
                    if item.kind == "PERPLEXITY_SAMPLES"
                )
            ).decode("utf-8").splitlines()
        ]
        per_record = {row["sample_id"]: row for row in evidence_rows}
        assert per_record["fluent"]["perplexity"] < per_record["disordered"][
            "perplexity"
        ]
        assert ppl.semantic_coverage.level == "FULL"
        assert ppl.population.excluded_count == 2

        # 02080106：受影响记录按记录去重（garbled/html/ad/page 共 4 条）
        purity = by_metric["02080106"]
        purity_values = {item.key: item.value for item in purity.observations}
        assert purity_values["interference_record_count"] == 4
        assert purity_values["evaluable_record_count"] == 7
        assert purity_values["interference_record_ratio"] == pytest.approx(4 / 7)
        assert purity_values["rule_hit_count"] > purity_values["interference_record_count"]
        assert purity.semantic_coverage.level == "PARTIAL"
        purity_evidence = next(
            item for item in purity.evidence if item.kind == "TEXT_PURITY_VIOLATIONS"
        )
        assert purity_evidence.summary.attributes["ruleset_version"] == "1.0.0"
        assert store.exists(purity_evidence.artifact.uri)

        # 规则评估使用契约绑定的 measurement；PPL 阈值判定按证据逐条推算
        rule_by_item = {item.contract_item_key: item for item in rules}
        assert rule_by_item["purity"].status == "FAIL"  # 4/7 > 0.5
        assert rule_by_item["purity"].observed.value == pytest.approx(4 / 7)
        high_ratio = sum(
            1 for row in evidence_rows if row["perplexity"] >= 50.0
        ) / len(evidence_rows)
        assert rule_by_item["perplexity"].observed.value == pytest.approx(high_ratio)
        assert rule_by_item["perplexity"].status == (
            "PASS" if high_ratio <= 0.5 else "FAIL"
        )

        assert report is not None
        assert report.execution_status == "SUCCEEDED"
        report_metrics = {section.metric.key: section for section in report.metrics}
        assert set(report_metrics) == {"02080103", "02080101", "02080106"}


def test_missing_kenlm_model_fails_the_stage_without_fabricated_values(
    monkeypatch, tmp_path: Path
):
    from qa_eval.calculators import text_perplexity
    from qa_eval.config import Settings

    monkeypatch.setattr(
        text_perplexity,
        "get_settings",
        lambda: Settings(kenlm_model_path=None),
    )
    engine, store, version_id = _prepare(
        tmp_path, {"fluent": FLUENT, "garbled": GARBLED}
    )
    with Session(engine) as session:
        contract = _publish_contract(
            session, version_id, ppl_parameters={"model_path": None}
        )
        service = EvaluationRunService(session, store)
        run = service.create(contract.contract.contract_version_id)
        session.commit()
        executed = service.execute(run.run_id)

        # 创建阶段可加载（kenlm 包存在），执行阶段因缺少模型而失败
        assert executed.status == "PARTIAL_SUCCEEDED"
        ppl_stage = next(
            stage
            for stage in service.list_stage_runs(run.run_id)
            if stage.calculator["key"] == "text_perplexity"
        )
        assert ppl_stage.status == "FAILED"
        assert ppl_stage.error["code"] == "KENLM_MODEL_UNAVAILABLE"

        results = {result.metric.key: result for result in service.list_results(run.run_id)}
        ppl_result = results["02080101"]
        assert ppl_result.status == "ERROR"
        assert ppl_result.observations == []  # 无伪造 PPL
        assert ppl_result.population is None

        rules = QualityResultService(session, store).list_rules(run.run_id)
        ppl_rule = next(
            item for item in rules if item.metric.key == "02080101"
        )
        assert ppl_rule.status == "UNKNOWN"
        assert ppl_rule.reason_code == "METRIC_ERROR"

        # 其余指标不受影响
        assert results["02080106"].status == "OK"
        report = QualityResultService(session, store).get_report(run.run_id)
        assert report is not None
        assert report.execution_status == "PARTIAL_SUCCEEDED"
