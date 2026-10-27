# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# 闭环 1：只读服务，加载已有 LOCAL_VALIDATION 真实产物。
# 不写入数据库，不登记为正式平台 Run，所有返回明确标记 authority。
# ====================================================

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qa_eval.contracts import (
    DatasetManifestV2,
    IssueIndexV1,
    MetricResultV1,
    QualityContractVersionV1,
    QualityIssueV1,
    QualityReportV1,
    RuleEvaluationV1,
)

LOCAL_VALIDATION_AUTHORITY = "LOCAL_VALIDATION"


class LocalValidationArtifactSet:
    """单个 LOCAL_VALIDATION 运行目录下的全部可用产物。"""

    def __init__(
        self,
        run_dir: Path,
        local_context: dict[str, Any],
        manifest: DatasetManifestV2,
        metric_result: MetricResultV1,
        contract: QualityContractVersionV1,
        rule_evaluations: list[RuleEvaluationV1],
        issues: list[QualityIssueV1],
        issue_index: IssueIndexV1,
        report: QualityReportV1,
    ) -> None:
        self.run_dir = run_dir
        self.local_context = local_context
        self.manifest = manifest
        self.metric_result = metric_result
        self.contract = contract
        self.rule_evaluations = rule_evaluations
        self.issues = issues
        self.issue_index = issue_index
        self.report = report

    @property
    def authority(self) -> str:
        return LOCAL_VALIDATION_AUTHORITY

    @property
    def not_a_platform_run(self) -> bool:
        return bool(self.local_context.get("not_a_platform_run", True))


class LocalValidationService:
    """读取本地已生成的真实 canonical 产物，供闭环 1 API/UI 展示。

    该服务不创建 EvaluationRun/StageRun，也不把结果冒充为平台持久化运行。
    调用方必须检查返回的 ``authority`` 与 ``not_a_platform_run`` 字段。
    """

    # 默认读取项目内已跑通的 02080103 真实产物目录
    DEFAULT_RUN_DIR = (
        Path(__file__).resolve().parents[4]
        / "data"
        / "dataset-quality-demo"
        / "policy.jhsjk_article"
        / "20261022T085048Z"
        / "canonical-v1"
    )

    def __init__(self, run_dir: Path | str | None = None) -> None:
        self.run_dir = Path(run_dir) if run_dir else self.DEFAULT_RUN_DIR

    def load(self) -> LocalValidationArtifactSet:
        """加载并校验全部 LOCAL_VALIDATION 产物。"""
        if not self.run_dir.is_dir():
            raise FileNotFoundError(
                f"LOCAL_VALIDATION run directory not found: {self.run_dir}"
            )

        local_context = self._read_json("local_execution_context.json")
        manifest = DatasetManifestV2.model_validate(
            self._read_json("dataset_manifest.json")
        )
        metric_result = MetricResultV1.model_validate(
            self._read_json(
                "metrics",
                "02080103-v2.0.0",
                "metric_result.json",
            )
        )
        contract = QualityContractVersionV1.model_validate(
            self._read_json(
                "quality",
                "02080103-mvp-baseline-v1.0.0",
                "quality_contract.json",
            )
        )
        rule_evaluations = [
            RuleEvaluationV1.model_validate(record)
            for record in self._read_jsonl(
                "quality",
                "02080103-mvp-baseline-v1.0.0",
                "rule_evaluations.jsonl",
            )
        ]
        issues = [
            QualityIssueV1.model_validate(record)
            for record in self._read_jsonl(
                "quality",
                "02080103-mvp-baseline-v1.0.0",
                "issues.jsonl",
            )
        ]
        issue_index = IssueIndexV1.model_validate(
            self._read_json(
                "quality",
                "02080103-mvp-baseline-v1.0.0",
                "issue_index.json",
            )
        )
        report = QualityReportV1.model_validate(
            self._read_json(
                "quality",
                "02080103-mvp-baseline-v1.0.0",
                "quality_report.json",
            )
        )

        return LocalValidationArtifactSet(
            run_dir=self.run_dir,
            local_context=local_context,
            manifest=manifest,
            metric_result=metric_result,
            contract=contract,
            rule_evaluations=rule_evaluations,
            issues=issues,
            issue_index=issue_index,
            report=report,
        )

    def _read_json(self, *parts: str) -> Any:
        path = self.run_dir.joinpath(*parts)
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _read_jsonl(self, *parts: str) -> list[dict[str, Any]]:
        path = self.run_dir.joinpath(*parts)
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records
