import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.types import JSONB, UUID


class RuleEvaluationModel(Base):
    __tablename__ = "rule_evaluations"
    rule_evaluation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"), index=True)
    metric_result_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("metric_results.metric_result_id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    document: Mapped[dict] = mapped_column(JSONB)


class QualityIssueModel(Base):
    __tablename__ = "quality_issues"
    __table_args__ = (UniqueConstraint("run_id", "dedup_key"),)
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"), index=True)
    metric_result_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("metric_results.metric_result_id", ondelete="RESTRICT"), index=True)
    determination: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(128), index=True)
    dedup_key: Mapped[str] = mapped_column(String(512))
    document: Mapped[dict] = mapped_column(JSONB)


class IssueIndexModel(Base):
    __tablename__ = "issue_indices"
    issue_index_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"), index=True)
    metric_result_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("metric_results.metric_result_id", ondelete="RESTRICT"), unique=True)
    document: Mapped[dict] = mapped_column(JSONB)


class QualityReportModel(Base):
    __tablename__ = "quality_reports"
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"), unique=True, index=True)
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("dataset_versions.id", ondelete="RESTRICT"), index=True)
    contract_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("quality_contract_versions.contract_version_id", ondelete="RESTRICT"), index=True)
    execution_status: Mapped[str] = mapped_column(String(32), index=True)
    gate_status: Mapped[str] = mapped_column(String(32), index=True)
    document: Mapped[dict] = mapped_column(JSONB)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScoredQualityReportModel(Base):
    __tablename__ = "scored_quality_reports"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "scoring_profile_id",
            "scoring_profile_version",
            "report_schema_version",
        ),
    )
    scored_report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    base_report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("quality_reports.report_id", ondelete="CASCADE"),
        index=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"),
        index=True,
    )
    scoring_profile_id: Mapped[str] = mapped_column(String(128), index=True)
    scoring_profile_version: Mapped[str] = mapped_column(String(32))
    report_schema_version: Mapped[str] = mapped_column(String(32))
    document: Mapped[dict] = mapped_column(JSONB)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
