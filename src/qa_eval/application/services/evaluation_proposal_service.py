from __future__ import annotations

from sqlalchemy.orm import Session

from qa_eval.application.services.applicability_service import ApplicabilityService
from qa_eval.application.services.quality_contract_service import (
    ContractRuleRequest,
    QualityContractItemPublishRequest,
    QualityContractService,
)
from qa_eval.contracts.proposals import (
    ApplicabilityDecisionV1,
    EvaluationProposalV1,
    EvaluationProposalValidationV1,
)
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel


class EvaluationProposalService:
    def __init__(self, session: Session):
        self.session = session
        self.contracts = QualityContractService(session)
        self.applicability = ApplicabilityService()

    def validate(
        self, proposal: EvaluationProposalV1
    ) -> EvaluationProposalValidationV1:
        global_findings: list[dict] = []
        version = self.session.get(DatasetVersionModel, proposal.dataset_version_id)
        if version is None:
            return self._invalid_without_dataset(
                proposal, "DATASET_VERSION_NOT_FOUND", "DatasetVersion not found"
            )
        if version.status != "PUBLISHED":
            global_findings.append(
                self._finding(
                    "DATASET_VERSION_NOT_PUBLISHED",
                    "Proposal requires a PUBLISHED DatasetVersion",
                    status=version.status,
                )
            )
        dataset = self.session.get(DatasetModel, version.dataset_id)
        if dataset is None:
            return self._invalid_without_dataset(
                proposal, "DATASET_NOT_FOUND", "Dataset not found"
            )
        context, current_profile_fingerprint = ApplicabilityService.context_for(
            self.session, version, dataset
        )
        if current_profile_fingerprint is None:
            global_findings.append(
                self._finding(
                    "PROFILE_MISSING",
                    "DatasetVersion has no Dataset Profile",
                )
            )
        elif proposal.profile_fingerprint != current_profile_fingerprint:
            global_findings.append(
                self._finding(
                    "PROFILE_FINGERPRINT_MISMATCH",
                    "Proposal does not reference the current Dataset Profile",
                    expected=current_profile_fingerprint,
                    actual=proposal.profile_fingerprint,
                )
            )

        item_results = []
        for item in proposal.items:
            findings: list[dict] = []
            try:
                metric = self.contracts._metric(item.metric.key, item.metric.version)
            except ValueError as exc:
                missing = ApplicabilityDecisionV1(
                    status="UNAVAILABLE",
                    findings=[self._finding("METRIC_NOT_REGISTERED", str(exc))],
                )
                item_results.append(
                    {
                        "key": item.key,
                        "valid": False,
                        "selected_applicability": missing,
                        "candidates": [
                            {
                                "calculator": candidate,
                                "binding": None,
                                "applicability": missing,
                            }
                            for candidate in item.candidates
                        ],
                        "findings": missing.findings,
                    }
                )
                continue

            measurement = next(
                (
                    value
                    for value in metric.measurements
                    if value.key == item.metric.measurement
                ),
                None,
            )
            if measurement is None:
                findings.append(
                    self._finding(
                        "MEASUREMENT_NOT_REGISTERED",
                        "Proposal measurement is not registered by the Metric",
                        measurement=item.metric.measurement,
                    )
                )
            else:
                try:
                    QualityContractService._validate_threshold(
                        item.rule.threshold, measurement.value_type
                    )
                except ValueError as exc:
                    findings.append(self._finding("INVALID_THRESHOLD", str(exc)))

            candidate_results = []
            selected_decision = None
            for candidate in item.candidates:
                try:
                    calculator = self.contracts._calculator(
                        candidate.key, candidate.version
                    )
                    binding = self.contracts._binding_values(
                        item.metric.key,
                        item.metric.version,
                        candidate.key,
                        candidate.version,
                    )
                    decision = self.applicability.evaluate(
                        context, metric, calculator, binding, item.parameters
                    )
                    if candidate == item.selected_calculator:
                        try:
                            QualityContractService._validate_parameters(
                                item.parameters, calculator.parameter_schema
                            )
                        except ValueError as exc:
                            findings.append(
                                self._finding("INVALID_PARAMETERS", str(exc))
                            )
                except ValueError as exc:
                    binding = None
                    decision = ApplicabilityDecisionV1(
                        status="UNAVAILABLE",
                        findings=[self._finding("REGISTRY_OBJECT_NOT_FOUND", str(exc))],
                    )
                candidate_results.append(
                    {
                        "calculator": candidate,
                        "binding": binding,
                        "applicability": decision,
                    }
                )
                if candidate == item.selected_calculator:
                    selected_decision = decision

            assert selected_decision is not None
            if selected_decision.status != "APPLICABLE":
                findings.extend(
                    value.model_dump(mode="json")
                    for value in selected_decision.findings
                )
            item_results.append(
                {
                    "key": item.key,
                    "valid": not findings,
                    "selected_applicability": selected_decision,
                    "candidates": candidate_results,
                    "findings": findings,
                }
            )

        return EvaluationProposalValidationV1.model_validate(
            {
                "proposal_id": proposal.proposal_id,
                "revision": proposal.revision,
                "valid": not global_findings
                and all(item["valid"] for item in item_results),
                "current_profile_fingerprint": current_profile_fingerprint,
                "items": item_results,
                "findings": global_findings,
            }
        )

    def to_contract_items(
        self, proposal: EvaluationProposalV1
    ) -> list[QualityContractItemPublishRequest]:
        validation = self.validate(proposal)
        if not validation.valid:
            raise ValueError(
                "Evaluation Proposal must be valid before Contract publication"
            )
        return [
            QualityContractItemPublishRequest(
                key=item.key,
                metric_key=item.metric.key,
                metric_version=item.metric.version,
                measurement=item.metric.measurement,
                calculator_key=item.selected_calculator.key,
                calculator_version=item.selected_calculator.version,
                parameters=item.parameters,
                rule=ContractRuleRequest.model_validate(item.rule.model_dump()),
            )
            for item in proposal.items
        ]

    @staticmethod
    def _finding(code: str, message: str, **details) -> dict:
        return {"code": code, "message": message, "details": details}

    def _invalid_without_dataset(self, proposal, code, message):
        decision = ApplicabilityDecisionV1(
            status="UNAVAILABLE", findings=[self._finding(code, message)]
        )
        return EvaluationProposalValidationV1.model_validate(
            {
                "proposal_id": proposal.proposal_id,
                "revision": proposal.revision,
                "valid": False,
                "current_profile_fingerprint": None,
                "items": [
                    {
                        "key": item.key,
                        "valid": False,
                        "selected_applicability": decision,
                        "candidates": [
                            {
                                "calculator": candidate,
                                "binding": None,
                                "applicability": decision,
                            }
                            for candidate in item.candidates
                        ],
                        "findings": decision.findings,
                    }
                    for item in proposal.items
                ],
                "findings": decision.findings,
            }
        )


__all__ = ["EvaluationProposalService"]
