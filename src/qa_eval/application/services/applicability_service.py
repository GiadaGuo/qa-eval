from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.protocol import CalculatorError
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.proposals import ApplicabilityDecisionV1
from qa_eval.contracts.registry import (
    CalculatorDescriptorV1,
    MetricCalculatorBindingV1,
    MetricDefinitionV1,
)
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_profile import DatasetProfileModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel


@dataclass(frozen=True)
class DatasetApplicabilityContext:
    modalities: set[str]
    content_roles: set[str]
    artifact_kinds: set[str]
    has_profile: bool
    metadata_fields: set[str] = field(default_factory=set)


class ApplicabilityService:
    def __init__(self, loader: TrustedCalculatorLoader | None = None):
        self.loader = loader or TrustedCalculatorLoader()

    @staticmethod
    def context_for(
        session: Session,
        version: DatasetVersionModel,
        dataset: DatasetModel,
    ) -> tuple[DatasetApplicabilityContext, str | None]:
        profile_row = session.scalars(
            select(DatasetProfileModel)
            .where(DatasetProfileModel.dataset_version_id == version.id)
            .order_by(DatasetProfileModel.created_at.desc())
        ).first()
        profile = profile_row.document if profile_row is not None else {}
        structure = profile.get("structure") or {}
        mapping = (version.artifact_refs or {}).get("field_mapping", {})
        mapped_roles = {
            item.get("target", {}).get("role")
            for item in mapping.get("mappings", [])
            if item.get("action") == "MAP"
            and item.get("target", {}).get("kind") == "CONTENT"
            and item.get("target", {}).get("role")
        }
        content_roles = mapped_roles | set(structure.get("content_roles", []))
        metadata_fields = {
            item.get("name") or item.get("field")
            for item in profile.get("field_profiles", [])
            if item.get("name") or item.get("field")
        }
        refs = version.artifact_refs or {}
        artifacts = set()
        if refs.get("normalized"):
            artifacts.add("NORMALIZED_DATA")
        if refs.get("manifest"):
            artifacts.add("DATASET_MANIFEST")
        if profile_row is not None:
            artifacts.add("DATASET_PROFILE")
        fingerprint = (
            None if profile_row is None else f"sha256:{profile_row.checksum_sha256}"
        )
        return (
            DatasetApplicabilityContext(
                modalities=set(dataset.modality),
                content_roles=content_roles,
                artifact_kinds=artifacts,
                has_profile=profile_row is not None,
                metadata_fields=metadata_fields,
            ),
            fingerprint,
        )

    def evaluate(
        self,
        context: DatasetApplicabilityContext,
        metric: MetricDefinitionV1,
        calculator: CalculatorDescriptorV1,
        binding: MetricCalculatorBindingV1,
        parameters: JsonObject,
    ) -> ApplicabilityDecisionV1:
        findings: list[dict] = []
        if not binding.enabled:
            findings.append(
                self._finding(
                    "BINDING_DISABLED", "Metric/Calculator binding is disabled"
                )
            )
        if calculator.availability.status != "AVAILABLE":
            findings.append(
                self._finding(
                    "CALCULATOR_UNAVAILABLE",
                    calculator.availability.reason or "Calculator is not available",
                    status=calculator.availability.status,
                )
            )
        if findings:
            return ApplicabilityDecisionV1(status="UNAVAILABLE", findings=findings)

        try:
            self.loader.load(calculator)
        except CalculatorError as exc:
            return ApplicabilityDecisionV1(
                status="UNAVAILABLE",
                findings=[
                    self._finding(
                        "RUNTIME_UNAVAILABLE",
                        str(exc),
                        error_code=exc.code,
                    )
                ],
            )

        compatible_modalities = (
            set(metric.applicability.modalities)
            & set(calculator.capabilities.modalities)
            & context.modalities
        )
        if not compatible_modalities:
            return ApplicabilityDecisionV1(
                status="NOT_APPLICABLE",
                findings=[
                    self._finding(
                        "MODALITY_MISMATCH",
                        "Dataset modality is incompatible with the Metric/Calculator",
                        dataset_modalities=sorted(context.modalities),
                        metric_modalities=metric.applicability.modalities,
                        calculator_modalities=calculator.capabilities.modalities,
                    )
                ],
            )

        execution_roles = calculator.execution.input_roles
        required_roles = set(metric.applicability.required_roles) | set(
            calculator.applicability_constraints.get("required_content_roles", [])
        )
        if execution_roles:
            overrides = parameters.get("content_roles", {})
            required_roles |= {
                str(overrides.get(item.alias, item.role))
                for item in execution_roles
                if item.required
            }
        else:
            required_roles |= set(calculator.input_contract.role_selectors)
        if content_role := parameters.get("content_role"):
            required_roles.add(str(content_role))
        static_context = {
            **calculator.execution.static_context,
            **parameters.get("static_context", {}),
            **{
                key: parameters[key]
                for key in calculator.execution.required_static_context
                if key in parameters
            },
        }
        missing_context = set(calculator.execution.required_static_context) - set(
            static_context
        )
        if missing_context:
            findings.append(
                self._finding(
                    "MISSING_STATIC_CONTEXT",
                    "Shared inference is missing required metric context",
                    keys=sorted(missing_context),
                )
            )
        missing_roles = required_roles - context.content_roles
        if missing_roles:
            findings.append(
                self._finding(
                    "MISSING_CONTENT_ROLE",
                    "DatasetVersion is missing required content roles",
                    roles=sorted(missing_roles),
                )
            )

        required_artifacts = set(calculator.input_artifact_requirements)
        missing_artifacts = required_artifacts - context.artifact_kinds
        if missing_artifacts:
            findings.append(
                self._finding(
                    "MISSING_INPUT_ARTIFACT",
                    "DatasetVersion is missing required input artifacts",
                    artifacts=sorted(missing_artifacts),
                )
            )

        if calculator.applicability_constraints.get("requires_one_of") and not any(
            parameters.get(key)
            for key in calculator.applicability_constraints["requires_one_of"]
        ):
            findings.append(
                self._finding(
                    "MISSING_SELECTOR_PARAMETER",
                    "Calculator requires a user-selected field or role",
                    parameters=calculator.applicability_constraints["requires_one_of"],
                )
            )
        if (metadata_field := parameters.get("metadata_field")) and (
            context.has_profile and str(metadata_field) not in context.metadata_fields
        ):
            findings.append(
                self._finding(
                    "MISSING_METADATA_FIELD",
                    "Dataset profile does not contain the selected metadata field",
                    field=str(metadata_field),
                )
            )

        if findings:
            return ApplicabilityDecisionV1(status="NEEDS_INPUT", findings=findings)
        return ApplicabilityDecisionV1(status="APPLICABLE", findings=[])

    @staticmethod
    def _finding(code: str, message: str, **details) -> dict:
        return {"code": code, "message": message, "details": details}


__all__ = ["ApplicabilityService", "DatasetApplicabilityContext"]
