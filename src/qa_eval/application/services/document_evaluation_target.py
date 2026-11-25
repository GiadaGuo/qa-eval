"""Document target settings shared by contract publication and compilation."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from qa_eval.calculators.document_completeness import (
    ApplicationInstructionsCalculator,
    BasicInformationCalculator,
    ConstructionProcessCalculator,
    ContentCharacteristicsCalculator,
)
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.infrastructure.db.models.document_version import DocumentVersionModel

OPERATORS = {
    cls.metric_key: cls.calculator_key
    for cls in (
        BasicInformationCalculator,
        ContentCharacteristicsCalculator,
        ConstructionProcessCalculator,
        ApplicationInstructionsCalculator,
    )
}


class DocumentMetricSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calculator_version: str = "1.0.0"
    parameters: dict = Field(default_factory=dict)
    weight: float = Field(default=0.25, gt=0, le=1)


class DocumentContractRequest(BaseModel):
    document_version_id: UUID
    name: str = "说明文档完整性"
    metrics: dict[str, DocumentMetricSettings] = Field(default_factory=dict)
    threshold: float = Field(default=90, ge=0, le=100)


def document_target(version):
    return {
        "document_version_id": str(version.id),
        "sha256": version.sha256,
        "uri": version.storage_uri,
        "filename": version.document["filename"],
    }


def contract_template(service, request):
    from math import isclose

    version = service.session.get(DocumentVersionModel, request.document_version_id)
    if version is None:
        raise ValueError("DocumentVersion not found")
    if request.metrics.keys() - OPERATORS.keys():
        raise ValueError("unknown documentation metric")
    items = []
    weights = {}
    for metric, key in OPERATORS.items():
        settings = request.metrics.get(metric, DocumentMetricSettings())
        descriptor = service._calculator(key, settings.calculator_version)
        binding = service._binding_values(metric, "1.0.0", key, descriptor.version)
        if binding is None or not binding.enabled:
            raise ValueError(f"enabled documentation binding required for {metric}")
        calculator = TrustedCalculatorLoader().load(descriptor)
        _, configuration = calculator.configuration(settings.parameters)
        items.append(
            {
                "key": metric,
                "metric": {
                    "key": metric,
                    "version": "1.0.0",
                    "measurement": "completeness_ratio",
                },
                "scope": "DOCUMENT",
                "calculation": {
                    "parameters": {
                        "configuration": configuration.model_dump(mode="json")
                    },
                    "implementation_constraints": {
                        "attributes": {
                            "calculator_key": key,
                            "calculator_version": descriptor.version,
                            "code_digest": descriptor.code_digest,
                            "configuration_sha256": configuration.digest(),
                        }
                    },
                },
                "rule": {"strategy": "OBSERVE_ONLY"},
                "severity": "WARNING",
                "on_missing": "UNKNOWN",
            }
        )
        weights[metric] = settings.weight
    if not isclose(sum(weights.values()), 1):
        raise ValueError("documentation weights must sum to 1")
    return {
        "contract_id": uuid4(),
        "contract_version_id": uuid4(),
        "version_label": "1.0.0",
        "name": request.name,
        "status": "PUBLISHED",
        "published_at": datetime.now(UTC),
        "document_target": {
            **document_target(version),
            "weights": weights,
            "threshold": request.threshold,
        },
        "applicability": {},
        "items": items,
        "gate": {"unknown_policy": "UNKNOWN", "fail_on_severities": ["WARNING"]},
    }
