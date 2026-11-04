import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.types import JSONB, UUID


class LogicalEvaluationPlanModel(Base):
    __tablename__ = "logical_evaluation_plans"
    logical_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    contract_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "quality_contract_versions.contract_version_id", ondelete="RESTRICT"
        ),
        index=True,
    )
    semantic_fingerprint: Mapped[str] = mapped_column(String(80), index=True)
    document: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PhysicalEvaluationPlanModel(Base):
    __tablename__ = "physical_evaluation_plans"
    physical_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    logical_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("logical_evaluation_plans.logical_plan_id", ondelete="RESTRICT"),
        index=True,
    )
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    contract_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "quality_contract_versions.contract_version_id", ondelete="RESTRICT"
        ),
        index=True,
    )
    semantic_fingerprint: Mapped[str] = mapped_column(String(80), index=True)
    document: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EvaluationRunModel(Base):
    __tablename__ = "evaluation_runs"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    physical_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("physical_evaluation_plans.physical_plan_id", ondelete="RESTRICT"),
        index=True,
    )
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    contract_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "quality_contract_versions.contract_version_id", ondelete="RESTRICT"
        ),
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), index=True)
    document: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StageRunModel(Base):
    __tablename__ = "stage_runs"
    __table_args__ = (UniqueConstraint("run_id", "stage_id", "attempt"),)
    stage_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"),
        index=True,
    )
    physical_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("physical_evaluation_plans.physical_plan_id", ondelete="RESTRICT"),
    )
    stage_id: Mapped[str] = mapped_column(String(255))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    document: Mapped[dict] = mapped_column(JSONB)


class MetricResultModel(Base):
    __tablename__ = "metric_results"
    metric_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"),
        index=True,
    )
    stage_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stage_runs.stage_run_id", ondelete="RESTRICT"),
        index=True,
    )
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    metric_key: Mapped[str] = mapped_column(String(128), index=True)
    metric_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    document: Mapped[dict] = mapped_column(JSONB)
