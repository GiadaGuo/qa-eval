import uuid

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.types import JSONB, UUID


class MetricDefinitionModel(Base):
    __tablename__ = "metric_definitions"
    __table_args__ = (UniqueConstraint("metric_key", "version"),)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    metric_key: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict] = mapped_column(JSONB)


class CalculatorImplementationModel(Base):
    __tablename__ = "calculator_implementations"
    __table_args__ = (UniqueConstraint("calculator_key", "version"),)
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    calculator_key: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict] = mapped_column(JSONB)


class MetricCalculatorBindingModel(Base):
    __tablename__ = "metric_calculator_bindings"
    __table_args__ = (
        UniqueConstraint(
            "metric_key", "metric_version", "calculator_key", "calculator_version"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    metric_key: Mapped[str] = mapped_column(String(128), index=True)
    metric_version: Mapped[str] = mapped_column(String(64))
    calculator_key: Mapped[str] = mapped_column(String(128))
    calculator_version: Mapped[str] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean)
    document: Mapped[dict] = mapped_column(JSONB)
