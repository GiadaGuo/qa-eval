from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.protocol import CalculatorError
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
SHARED_EXECUTION_REQUIRED_FIELDS = (
    "operator_type",
    "prompt_family",
    "prompt_version",
    "rubric",
    "rubric_version",
    "rubric_output_schema",
    "output_field",
    "aggregation_strategy",
)


class InvalidRegistryDescriptorError(ValueError):
    def __init__(self, details: dict[str, Any]):
        self.details = details
        super().__init__(
            f"Invalid Calculator descriptor: {details['calculator_key']}@"
            f"{details['version']}"
        )


class MetricRegistryService:
    def __init__(self, session: Session, registry_root: Path = REGISTRY_ROOT):
        self.session = session
        self.registry_root = registry_root

    def sync(self) -> dict[str, Any]:
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
        repairs = []
        for item in calculators:
            existing = self.session.scalar(
                select(CalculatorImplementationModel).filter_by(
                    calculator_key=item.calculator_key, version=item.version
                )
            )
            if existing is not None:
                invalid = self._invalid_calculator_details(existing)
                if invalid is not None:
                    repairs.append(
                        {
                            "calculator_key": item.calculator_key,
                            "version": item.version,
                            "missing_fields": invalid["missing_fields"],
                        }
                    )
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
            metric = next(
                value
                for value in metrics
                if (value.metric_key, value.version)
                == (item.metric_key, item.metric_version)
            )
            unknown_measurements = set(item.produced_measurements) - {
                value.key for value in metric.measurements
            }
            if unknown_measurements:
                raise ValueError(
                    "binding produced_measurements are not declared by the metric: "
                    + ", ".join(sorted(unknown_measurements))
                )
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
        return {
            "metrics": len(metrics),
            "calculators": len(calculators),
            "bindings": len(bindings),
            "repaired_calculators": repairs,
        }

    # Transitional programmatic alias. HTTP reads never invoke it implicitly.
    seed = sync

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
        descriptors = []
        for row in self._calculator_rows():
            try:
                descriptors.append(CalculatorDescriptorV1.model_validate(row.document))
            except ValidationError:
                # Invalid rows remain visible through validation/readiness diagnostics,
                # but can never be returned as executable Calculator descriptors.
                continue
        return descriptors

    def calculator_validation(self) -> dict[str, Any]:
        rows = self._calculator_rows()
        invalid = [
            details
            for row in rows
            if (details := self._invalid_calculator_details(row)) is not None
        ]
        total = len(rows)
        return {
            "valid": total - len(invalid),
            "invalid": invalid,
            "total": total,
        }

    def get_calculator(self, key: str, version: str):
        row = self.session.scalar(
            select(CalculatorImplementationModel).filter_by(
                calculator_key=key, version=version
            )
        )
        if row is None:
            return None
        try:
            return CalculatorDescriptorV1.model_validate(row.document)
        except ValidationError as exc:
            raise InvalidRegistryDescriptorError(
                self._invalid_calculator_details(row, exc)
            ) from exc

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

    def runtime_readiness(self) -> dict:
        loader = TrustedCalculatorLoader()
        items = []
        for row in self._calculator_rows():
            try:
                descriptor = CalculatorDescriptorV1.model_validate(row.document)
            except ValidationError as exc:
                details = self._invalid_calculator_details(row, exc)
                items.append(
                    {
                        "calculator_key": row.calculator_key,
                        "version": row.version,
                        "ready": False,
                        "error": {
                            "code": "INVALID_REGISTRY_DESCRIPTOR",
                            "type": "ValidationError",
                            "message": "Calculator descriptor failed canonical validation",
                            "details": details,
                        },
                        "availability": {
                            "status": "INVALID",
                            "reason": "Registry document is not canonical",
                        },
                    }
                )
                continue
            availability = {
                "status": descriptor.availability.status,
                "reason": descriptor.availability.reason,
            }
            if descriptor.availability.status != "AVAILABLE":
                # Registry-declared placeholder: not executable by design, so a
                # load refusal is expected rather than a deployment defect.
                items.append(
                    {
                        "calculator_key": descriptor.calculator_key,
                        "version": descriptor.version,
                        "ready": False,
                        "error": None,
                        "availability": availability,
                    }
                )
                continue
            try:
                loader.load(descriptor)
                items.append(
                    {
                        "calculator_key": descriptor.calculator_key,
                        "version": descriptor.version,
                        "ready": True,
                        "error": None,
                        "availability": availability,
                    }
                )
            except CalculatorError as exc:
                items.append(
                    {
                        "calculator_key": descriptor.calculator_key,
                        "version": descriptor.version,
                        "ready": False,
                        "error": {
                            "code": exc.code,
                            "type": type(exc).__name__,
                            "message": str(exc),
                            "details": exc.details,
                        },
                        "availability": availability,
                    }
                )
        executable = [
            item
            for item in items
            if item["availability"]["status"] in {"AVAILABLE", "INVALID"}
        ]
        return {
            "ready": bool(items) and all(item["ready"] for item in executable),
            "calculators": items,
        }

    def _calculator_rows(self) -> list[CalculatorImplementationModel]:
        return list(
            self.session.scalars(
                select(CalculatorImplementationModel).order_by(
                    CalculatorImplementationModel.calculator_key,
                    CalculatorImplementationModel.version,
                )
            ).all()
        )

    @staticmethod
    def _invalid_calculator_details(
        row: CalculatorImplementationModel,
        error: ValidationError | None = None,
    ) -> dict[str, Any] | None:
        if error is None:
            try:
                CalculatorDescriptorV1.model_validate(row.document)
                return None
            except ValidationError as exc:
                error = exc
        execution = row.document.get("execution", {})
        missing_fields = []
        if execution.get("mode") == "SHARED_INFERENCE":
            missing_fields = [
                f"execution.{field}"
                for field in SHARED_EXECUTION_REQUIRED_FIELDS
                if not execution.get(field)
            ]
        return {
            "calculator_key": row.calculator_key,
            "version": row.version,
            "missing_fields": missing_fields,
            "errors": [
                {
                    "location": ".".join(str(part) for part in item["loc"]),
                    "type": item["type"],
                    "message": item["msg"],
                }
                for item in error.errors(include_url=False, include_input=False)
            ],
        }
