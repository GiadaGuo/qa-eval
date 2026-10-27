from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.contracts.registry import (
    CalculatorDescriptorV1,
    MetricCalculatorBindingV1,
    MetricDefinitionV1,
)
from qa_eval.infrastructure.db.models.registry import (
    CalculatorImplementationModel,
    MetricCalculatorBindingModel,
    MetricDefinitionModel,
)

REGISTRY_ROOT = Path(__file__).resolve().parents[4] / "configs" / "registry"


class MetricRegistryService:
    def __init__(self, session: Session, registry_root: Path = REGISTRY_ROOT):
        self.session = session
        self.registry_root = registry_root

    def seed(self) -> None:
        metrics = [
            MetricDefinitionV1.model_validate_json(p.read_text())
            for p in sorted((self.registry_root / "metrics").glob("*.json"))
        ]
        calculators = [
            CalculatorDescriptorV1.model_validate_json(p.read_text())
            for p in sorted((self.registry_root / "calculators").glob("*.json"))
        ]
        bindings = [
            MetricCalculatorBindingV1.model_validate_json(p.read_text())
            for p in sorted((self.registry_root / "bindings").glob("*.json"))
        ]
        for item in metrics:
            self._upsert(
                MetricDefinitionModel,
                {"metric_key": item.metric_key, "version": item.version},
                item.model_dump(mode="json"),
            )
        for item in calculators:
            self._upsert(
                CalculatorImplementationModel,
                {"calculator_key": item.calculator_key, "version": item.version},
                item.model_dump(mode="json"),
            )
        known_metrics = {(x.metric_key, x.version) for x in metrics}
        known_calculators = {(x.calculator_key, x.version) for x in calculators}
        for item in bindings:
            if (item.metric_key, item.metric_version) not in known_metrics or (
                item.calculator_key,
                item.calculator_version,
            ) not in known_calculators:
                raise ValueError("binding references an unknown registry object")
            keys = {
                "metric_key": item.metric_key,
                "metric_version": item.metric_version,
                "calculator_key": item.calculator_key,
                "calculator_version": item.calculator_version,
            }
            self._upsert(
                MetricCalculatorBindingModel,
                keys,
                item.model_dump(mode="json"),
                priority=item.priority,
                enabled=item.enabled,
            )
        self.session.flush()

    def _upsert(self, model, keys: dict, document: dict, **extra) -> None:
        row = self.session.scalar(select(model).filter_by(**keys))
        if row is None:
            self.session.add(model(**keys, document=document, **extra))
        else:
            row.document = document
            for field, value in extra.items():
                setattr(row, field, value)

    def list_metrics(self):
        return [
            MetricDefinitionV1.model_validate(x.document)
            for x in self.session.scalars(
                select(MetricDefinitionModel).order_by(MetricDefinitionModel.metric_key)
            ).all()
        ]

    def get_metric(self, key: str, version: str):
        row = self.session.scalar(
            select(MetricDefinitionModel).filter_by(metric_key=key, version=version)
        )
        return None if row is None else MetricDefinitionV1.model_validate(row.document)

    def list_calculators(self):
        return [
            CalculatorDescriptorV1.model_validate(x.document)
            for x in self.session.scalars(
                select(CalculatorImplementationModel).order_by(
                    CalculatorImplementationModel.calculator_key
                )
            ).all()
        ]

    def get_calculator(self, key: str, version: str):
        row = self.session.scalar(
            select(CalculatorImplementationModel).filter_by(
                calculator_key=key, version=version
            )
        )
        return (
            None if row is None else CalculatorDescriptorV1.model_validate(row.document)
        )

    def list_bindings(self, metric_key: str | None = None):
        stmt = select(MetricCalculatorBindingModel).order_by(
            MetricCalculatorBindingModel.priority.desc()
        )
        if metric_key:
            stmt = stmt.where(MetricCalculatorBindingModel.metric_key == metric_key)
        return [
            MetricCalculatorBindingV1.model_validate(x.document)
            for x in self.session.scalars(stmt).all()
        ]
