import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.types import JSONB, UUID


class RuleEvaluationModel(Base):
    __tablename__ = "rule_evaluations"
    __table_args__ = (
        CheckConstraint("metric_result_id IS NULL OR calculator_result_id IS NULL", name="ck_rule_evaluations_result_source"),
    )
    rule_evaluation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"), index=True)
    metric_result_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("metric_results.metric_result_id", ondelete="RESTRICT"), index=True)
    calculator_result_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calculator_results.calculator_result_id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    document: Mapped[dict] = mapped_column(JSONB)


class QualityIssueModel(Base):
    __tablename__ = "quality_issues"
    __table_args__ = (
        UniqueConstraint("run_id", "dedup_key"),
        CheckConstraint(
            "(metric_result_id IS NULL) <> (calculator_result_id IS NULL)",
            name="ck_quality_issues_result_source",
        ),
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"), index=True)
    metric_result_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("metric_results.metric_result_id", ondelete="RESTRICT"), index=True)
    calculator_result_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("calculator_results.calculator_result_id", ondelete="RESTRICT"), index=True
    )
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
    __table_args__ = (
        CheckConstraint("(dataset_version_id IS NULL) <> (document_version_id IS NULL)", name="ck_quality_reports_target"),
        CheckConstraint("(source_report_id IS NULL) = (source_document_report_id IS NULL)", name="ck_quality_reports_sources"),
        Index("uq_quality_reports_base_run", "run_id", unique=True,
              sqlite_where=text("source_report_id IS NULL"), postgresql_where=text("source_report_id IS NULL")),
        UniqueConstraint("source_report_id", "source_document_report_id", name="uq_quality_reports_source_pair"),
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="RESTRICT")
    )
    source_report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("quality_reports.report_id", ondelete="RESTRICT")
    )
    source_document_report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("quality_reports.report_id", ondelete="RESTRICT")
    )
    schema_version: Mapped[str] = mapped_column(String(8), server_default="1.0")
    document_quality_report_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_quality_reports.report_id", ondelete="RESTRICT")
    )
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"), index=True)
    dataset_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("dataset_versions.id", ondelete="RESTRICT"), index=True)
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
