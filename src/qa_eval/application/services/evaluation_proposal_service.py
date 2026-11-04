from __future__ import annotations

from jsonschema import Draft202012Validator
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
            if not item.enabled:
                item_results.append(
                    {
                        "key": item.key,
                        "valid": True,
                        "selected_applicability": ApplicabilityDecisionV1(
                            status="APPLICABLE"
                        ),
                        "candidates": [],
                        "findings": [],
                    }
                )
                continue
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
                    if item.rule.strategy == "THRESHOLD":
                        if item.rule.threshold is None:
                            raise ValueError("A rule threshold is required")
                        QualityContractService._validate_threshold(
                            item.rule.threshold, measurement.value_type
                        )
                        if (
                            measurement.minimum is not None
                            and item.rule.threshold < measurement.minimum
                        ):
                            raise ValueError(
                                "Rule threshold is below the measurement minimum"
                            )
                        if (
                            measurement.maximum is not None
                            and item.rule.threshold > measurement.maximum
                        ):
                            raise ValueError(
                                "Rule threshold exceeds the measurement maximum"
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
                    resolved_parameters = self._resolved_parameters(item, calculator)
                    decision = self.applicability.evaluate(
                        context, metric, calculator, binding, resolved_parameters
                    )
                    if candidate == item.selected_calculator:
                        parameter_findings = self._parameter_findings(
                            item.metric.key,
                            resolved_parameters,
                            calculator.parameter_schema,
                        )
                        findings.extend(parameter_findings)
                        missing_parameter_names = {
                            finding["details"].get("parameter")
                            for finding in parameter_findings
                            if finding["code"] == "REQUIRED_PARAMETER_MISSING"
                        }
                        if missing_parameter_names:
                            decision = decision.model_copy(
                                update={
                                    "findings": [
                                        finding
                                        for finding in decision.findings
                                        if not (
                                            finding.code == "MISSING_STATIC_CONTEXT"
                                            and set(finding.details.get("keys", []))
                                            <= missing_parameter_names
                                        )
                                    ]
                                }
                            )
                            if decision.status != "APPLICABLE" and not decision.findings:
                                decision = decision.model_copy(update={"status": "APPLICABLE"})
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
        enabled_items = [item for item in proposal.items if item.enabled]
        if not enabled_items:
            raise ValueError("Evaluation Proposal must select at least one item")
        return [
            QualityContractItemPublishRequest(
                key=item.key,
                metric_key=item.metric.key,
                metric_version=item.metric.version,
                measurement=item.metric.measurement,
                calculator_key=item.selected_calculator.key,
                calculator_version=item.selected_calculator.version,
                parameters=self._resolved_parameters(
                    item,
                    self.contracts._calculator(
                        item.selected_calculator.key,
                        item.selected_calculator.version,
                    ),
                ),
                rule=ContractRuleRequest.model_validate(item.rule.model_dump()),
            )
            for item in enabled_items
        ]

    @staticmethod
    def _resolved_parameters(item, calculator) -> dict:
        """Keep dataset binding separate unless the Calculator schema consumes it.

        Older drafts may contain content_role in parameters.  It is safe to drop
        that legacy value only when the selected descriptor does not define the
        property; every other unknown parameter remains subject to strict schema
        validation.
        """
        properties = calculator.parameter_schema.get("properties", {})
        parameters = dict(item.parameters)
        if "content_role" not in properties:
            parameters.pop("content_role", None)
        elif item.dataset_binding.get("content_role") is not None:
            parameters["content_role"] = item.dataset_binding["content_role"]
        return parameters

    @classmethod
    def _parameter_findings(cls, metric_id: str, parameters: dict, schema: dict) -> list[dict]:
        findings = []
        errors = sorted(
            Draft202012Validator(schema).iter_errors(parameters),
            key=lambda error: (list(error.path), error.validator or ""),
        )
        for error in errors:
            path = ".".join(str(value) for value in error.path)
            if error.validator == "required":
                missing = next(
                    (key for key in error.validator_value if key not in error.instance),
                    "unknown",
                )
                if missing == "declared_dataset_type":
                    message = "缺少数据集声明类型"
                    suggestion = "请展开 0207 类型一致性配置，填写数据集声明类型后保存"
                else:
                    message = f"缺少必填配置项：{missing}"
                    suggestion = f"请在指标配置中填写 {missing} 后重新保存"
                findings.append(
                    cls._finding(
                        "REQUIRED_PARAMETER_MISSING",
                        message,
                        metric_id=metric_id,
                        field_path=f"calculator_parameters.{missing}",
                        suggestion=suggestion,
                        action="EDIT_CALCULATOR_PARAMETERS",
                        parameter=missing,
                    )
                )
            elif error.validator == "additionalProperties":
                findings.append(
                    cls._finding(
                        "UNSUPPORTED_CALCULATOR_PARAMETER",
                        "存在计算器不支持的配置参数",
                        metric_id=metric_id,
                        field_path=f"calculator_parameters.{path}" if path else "calculator_parameters",
                        suggestion="系统生成了不受支持的计算参数，请重新保存配置",
                        action="EDIT_CALCULATOR_PARAMETERS",
                        diagnostic=error.message,
                    )
                )
            else:
                field = f"calculator_parameters.{path}" if path else "calculator_parameters"
                findings.append(
                    cls._finding(
                        "INVALID_CALCULATOR_PARAMETER",
                        f"计算参数 {path or '配置'} 的值无效",
                        metric_id=metric_id,
                        field_path=field,
                        suggestion="请按字段要求修改后重新保存",
                        action="EDIT_CALCULATOR_PARAMETERS",
                        diagnostic=error.message,
                    )
                )
        return findings

    @staticmethod
    def _finding(code: str, message: str, **values) -> dict:
        structured_keys = {
            "metric_id", "field_path", "suggestion", "action", "severity"
        }
        structured = {
            key: values.pop(key) for key in list(values) if key in structured_keys
        }
        return {"code": code, "message": message, "details": values, **structured}

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
