"""Project a frozen quality report and its indexes into an Excel workbook."""

import json
from itertools import chain
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import select

from qa_eval.application.services.decision_exports import write_sheets
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.infrastructure.db.models.evaluation_run import MetricResultModel
from qa_eval.infrastructure.db.models.quality_result import (
    QualityIssueModel,
    RuleEvaluationModel,
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _issues(session, run_id):
    issue = QualityIssueModel
    query = (
        select(issue.document).where(issue.run_id == run_id).order_by(issue.issue_id)
    )
    yield from session.scalars(query.execution_options(yield_per=128))


def _classification(report, calculators):
    for result in calculators:
        for statistic in result.output_schema.statistics:
            value = result.statistics.get(statistic.key)
            if isinstance(value, dict):
                for code, count in value.items():
                    yield (
                        result.contract_item_key,
                        statistic.key,
                        code,
                        count if type(count) in (int, float) else _json(count),
                    )
    for metric in report.get("metrics", ()):
        for code, count in metric["issue_summary"]["by_code"].items():
            yield metric["metric"]["key"], "问题分类", code, count
    for dimension in report.get("dimensions", ()):
        for metric in dimension.get("metrics", ()):
            counts = {}
            for check in metric.get("checks", ()):
                outcome = check["outcome"]
                counts[outcome] = counts.get(outcome, 0) + 1
            for outcome, count in sorted(counts.items()):
                yield metric["key"], "检查项状态", outcome, count


def _methodology(report, calculators):
    for result in calculators:
        for statistic in result.output_schema.statistics:
            value = result.statistics.get(statistic.key)
            yield (
                result.contract_item_key,
                statistic.key,
                _json(value),
                statistic.unit,
                statistic.population,
                statistic.method,
                statistic.numerator_key,
                statistic.denominator_key,
            )
    for metric in report.get("metrics", ()):
        primary = (
            (metric["measurement"],)
            if metric.get("measurement")
            else metric.get("observations", ())
        )
        for item in chain(primary, metric.get("diagnostics") or ()):
            yield (
                metric["metric"]["key"],
                item["key"],
                _json(item["value"]),
                item.get("unit"),
                "",
                "",
                "",
                "",
            )
    for section in report.get("native_evaluations", ()):
        rule = section["evaluation"]
        ref = rule["statistic_ref"]
        threshold = rule["rule"]
        yield (
            ref["contract_item_key"],
            f"{ref['statistics_key']}:threshold",
            _json(threshold),
            rule["statistic"]["unit"],
            rule["statistic"]["population"],
            rule["statistic"]["method"],
            rule.get("numerator"),
            rule.get("denominator"),
        )


def _artifact_rows(store, indexes, spec):
    if indexes.status(spec)["status"] == "READY":
        yield from indexes.iter_evidence_export(spec)
        return
    # Historical evidence can exist without a byte-offset index. This path is
    # restricted to the background export and reads one persisted row at a time.
    local_path = getattr(store, "local_read_path", None)
    if local_path is not None:
        with local_path(spec.source_uri).open("rb") as source:
            for line in source:
                if line.strip():
                    yield json.loads(line)
        return
    with TemporaryDirectory() as directory:
        path = Path(directory) / "evidence.jsonl"
        store.materialize(spec.source_uri, path)
        with path.open("rb") as source:
            for line in source:
                if line.strip():
                    yield json.loads(line)


def export_report(path, report, calculators, session, store, *, metadata=None):
    data = report.model_dump(mode="json")
    evidence = EvidenceQueryService(session, store)
    indexes = evidence.indexes
    references = {}
    decisions = {}
    for result in calculators:
        for ref in result.decision_artifact_refs:
            decisions[ref.evidence_id] = (
                result.contract_item_key,
                evidence._evidence_spec(ref),
            )
        for ref in result.evidence_artifact_refs:
            if ref.artifact.format == "jsonl":
                references[ref.evidence_id] = (
                    result.contract_item_key,
                    evidence._evidence_spec(ref),
                )
    if data["schema_version"] != "2.0":
        query = select(MetricResultModel.document).where(
            MetricResultModel.run_id == report.run_id,
            MetricResultModel.schema_version == "1.0",
        )
        from qa_eval.contracts.metrics import MetricResultV1

        for document in session.scalars(query.execution_options(yield_per=64)):
            result = MetricResultV1.model_validate(document)
            for ref in result.evidence:
                if ref.artifact.format == "jsonl":
                    references[ref.evidence_id] = (
                        result.metric.key,
                        evidence._evidence_spec(ref),
                    )

    def evidence_rows():
        for issue in _issues(session, report.run_id):
            for locator in issue["evidence"]:
                yield (
                    issue["issue_id"],
                    str(locator["evidence_id"]),
                    _json(locator["locator"]),
                )
        for evidence_id, (scope, spec) in references.items():
            for row in _artifact_rows(store, indexes, spec):
                yield scope, str(evidence_id), _json(row)
        for evidence_id, (scope, spec) in decisions.items():
            for row in indexes.iter_evidence_export(
                spec, decision_status="NOT_EVALUATED"
            ):
                if row.get("locations"):
                    yield (
                        scope,
                        str(evidence_id),
                        _json(
                            {
                                "sample_id": row["sample_id"],
                                "row_id": row["row_id"],
                                "locations": row["locations"],
                                "evidence_refs": row.get("evidence_refs", []),
                            }
                        ),
                    )

    def failed_rows():
        for evidence_id, (scope, spec) in decisions.items():
            for row in indexes.iter_evidence_export(spec, decision_status="0"):
                yield scope, str(evidence_id), row["sample_id"], _json(row)
        for issue in _issues(session, report.run_id):
            if issue["determination"] == "CONFIRMED":
                yield (
                    issue["code"],
                    issue["issue_id"],
                    issue["subject"].get("sample_id"),
                    _json(issue),
                )

    def undecided_rows():
        for evidence_id, (scope, spec) in decisions.items():
            for sample_id, reasons in indexes.iter_decision_export(spec, None):
                yield scope, str(evidence_id), sample_id, reasons

    metrics = [
        (dimension, metric)
        for dimension in data["dimensions"]
        for metric in dimension.get("metrics", ())
    ]
    checks = [
        (metric["key"], check)
        for _, metric in metrics
        for check in metric.get("checks", ())
    ]
    summary = (
        ("数据集名称", metadata.dataset_name if metadata else None),
        ("数据集版本号", metadata.dataset_version if metadata else None),
        ("说明文档版本号", metadata.document_version if metadata else None),
        ("评测耗时（秒）", metadata.duration_seconds if metadata and metadata.duration_seconds is not None else "—"),
        ("任务 ID", data["run_id"]),
        ("报告 ID", data["report_id"]),
        ("数据集版本", data.get("dataset_version_id")),
        ("说明文档版本", data.get("document_version_id")),
        ("契约版本", data["contract_version_id"]),
        ("报告协议", data["schema_version"]),
        ("评测结论", data.get("overall_verdict") or data.get("gate_status")),
        ("报告时间", data.get("finalized_at") or data.get("generated_at")),
    )
    report_metrics = data.get("metrics", ())

    def legacy_rule_rows():
        rule = RuleEvaluationModel
        query = (
            select(rule.document)
            .where(rule.run_id == report.run_id, rule.metric_result_id.is_not(None))
            .order_by(rule.rule_evaluation_id)
        )
        for item in session.scalars(query.execution_options(yield_per=64)):
            observed = item.get("observed") or {}
            yield (
                item["contract_item_key"],
                item["status"],
                "",
                _json(item["rule"]),
                _json(observed.get("value")),
            )

    core_rows = chain(
        (
            (
                metric["key"],
                metric["status"],
                metric.get("score"),
                dimension["threshold"],
                "",
            )
            for dimension, metric in metrics
        ),
        (
            (
                metric["metric"]["key"],
                metric["status"],
                metric.get("score"),
                "",
                _json(metric.get("measurement")),
            )
            for metric in report_metrics
        ),
        (
            (
                section["evaluation"]["statistic_ref"]["contract_item_key"],
                section["evaluation"]["status"],
                "",
                _json(section["evaluation"]["rule"]),
                _json(section["evaluation"]["metric_value"]),
            )
            for section in data.get("native_evaluations", ())
        ),
        legacy_rule_rows(),
    )
    tables = [
        ("评测摘要", ("项目", "值"), summary),
        ("核心指标", ("指标或算子", "状态", "得分", "阈值", "测量值"), core_rows),
        (
            "分类统计",
            ("指标或算子", "统计项", "分类", "值"),
            _classification(data, calculators),
        ),
    ]
    if decisions or data["schema_version"] != "2.0" or data.get("mode") == "BASIC":
        tables.append(
            (
                "不合规记录" if decisions else "确认问题",
                ("算子或问题分类", "引用 ID", "样本 ID", "判定或确认问题"),
                failed_rows(),
            )
        )
    if decisions:
        tables.append(
            ("未判定记录", ("算子", "证据 ID", "样本 ID", "原因代码"), undecided_rows())
        )
    if checks:
        tables.append(
            (
                "检查项",
                ("指标", "检查项", "状态", "判定原因"),
                (
                    (key, check["item_code"], check["outcome"], check["reason"])
                    for key, check in checks
                ),
            )
        )
    tables.append(
        (
            "证据明细",
            ("来源", "证据或检查项 ID", "原始证据"),
            chain(
                evidence_rows(),
                (
                    (key, check["item_code"], _json(quote))
                    for key, check in checks
                    for quote in check["quotes"]
                ),
            ),
        )
    )
    tables.append(
        (
            "计算口径",
            ("指标或算子", "统计项", "实际值", "单位", "总体", "方法", "分子", "分母"),
            _methodology(data, calculators),
        )
    )
    write_sheets(path, tables)
