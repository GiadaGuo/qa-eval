from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from qa_eval.application.services.applicability_service import ApplicabilityService
from qa_eval.application.services.evaluation_proposal_service import (
    EvaluationProposalService,
)
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.contracts.proposals import (
    EvaluationProposalV1,
    EvaluationRecommendationV1,
)
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.uuid7 import uuid7

DEMO_POLICY_V1 = {
    "0204": {
        "measurement": "complete_record_ratio",
        "operator": "GTE",
        "threshold": 0.95,
        "severity": "ERROR",
        "on_missing": "UNKNOWN",
    },
    "02080103": {
        "measurement": "exact_affected_record_ratio",
        "operator": "LTE",
        "threshold": 0.05,
        "severity": "ERROR",
        "on_missing": "UNKNOWN",
    },
}

_STATUS_RANK = {
    "APPLICABLE": 0,
    "NEEDS_INPUT": 1,
    "UNAVAILABLE": 2,
    "NOT_APPLICABLE": 3,
}
_COST_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


class EvaluationRecommendationService:
    def __init__(self, session: Session):
        self.session = session
        self.registry = MetricRegistryService(session)
        self.applicability = ApplicabilityService()
        self.proposals = EvaluationProposalService(session)

    def recommend(
        self, dataset_version_id: UUID, *, revision: int
    ) -> EvaluationRecommendationV1:
        version = self.session.get(DatasetVersionModel, dataset_version_id)
        if version is None:
            raise ValueError("DatasetVersion not found")
        if version.status != "PUBLISHED":
            raise ValueError("Recommendation requires a PUBLISHED DatasetVersion")
        dataset = self.session.get(DatasetModel, version.dataset_id)
        if dataset is None:
            raise ValueError("Dataset not found")
        context, fingerprint = self.applicability.context_for(
            self.session, version, dataset
        )
        if fingerprint is None:
            raise ValueError("Recommendation requires a Dataset Profile")

        calculators = {
            (item.calculator_key, item.version): item
            for item in self.registry.list_calculators()
        }
        bindings_by_metric: dict[tuple[str, str], list] = {}
        for binding in self.registry.list_bindings():
            bindings_by_metric.setdefault(
                (binding.metric_key, binding.metric_version), []
            ).append(binding)

        proposal_items = []
        coverage = []
        for metric in self.registry.list_metrics():
            if metric.dimension != "DATA_QUALITY":
                continue
            evaluated = []
            for binding in bindings_by_metric.get((metric.metric_key, metric.version), []):
                calculator = calculators.get(
                    (binding.calculator_key, binding.calculator_version)
                )
                if calculator is None:
                    continue
                parameters = self._parameters(calculator, context.content_roles)
                decision = self.applicability.evaluate(
                    context, metric, calculator, binding, parameters
                )
                evaluated.append((calculator, binding, parameters, decision))

            evaluated.sort(key=self._candidate_sort_key)
            applicable = [item for item in evaluated if item[3].status == "APPLICABLE"]
            selected = applicable[0] if applicable else None
            policy = DEMO_POLICY_V1.get(metric.metric_key)
            coverage_status, reason = self._coverage_status(evaluated)
            if selected is not None and policy is None:
                coverage_status = "NEEDS_INPUT"
                reason = "No explicit recommendation rule policy is configured"
                selected = None

            if selected is not None:
                calculator, _, parameters, _ = selected
                proposal_items.append(
                    {
                        "key": f"metric-{metric.metric_key}",
                        "metric": {
                            "key": metric.metric_key,
                            "version": metric.version,
                            "measurement": policy["measurement"],
                        },
                        "candidates": [
                            {"key": item[0].calculator_key, "version": item[0].version}
                            for item in evaluated
                        ],
                        "selected_calculator": {
                            "key": calculator.calculator_key,
                            "version": calculator.version,
                        },
                        "parameters": parameters,
                        "rule": {
                            key: value
                            for key, value in policy.items()
                            if key != "measurement"
                        },
                        "rationale": (
                            "Candidate is applicable and ranked by deterministic, cost, "
                            "calculator key, and version"
                        ),
                        "limitations": [
                            "Threshold is QA Eval DEMO_POLICY_V1, not a guide threshold",
                            *metric.definition.limitations,
                        ],
                    }
                )

            coverage.append(
                {
                    "metric_key": metric.metric_key,
                    "metric_version": metric.version,
                    "metric_name": metric.name,
                    "status": coverage_status,
                    "reason": reason,
                    "selected_calculator": None
                    if selected is None
                    else {
                        "key": selected[0].calculator_key,
                        "version": selected[0].version,
                    },
                    "candidates": [
                        {
                            "calculator": {
                                "key": calculator.calculator_key,
                                "version": calculator.version,
                            },
                            "applicability": decision,
                            "deterministic": calculator.deterministic,
                            "cost": calculator.resource_profile.cost,
                        }
                        for calculator, _, _, decision in evaluated
                    ],
                }
            )

        proposal = None
        validation = None
        if proposal_items:
            proposal = EvaluationProposalV1.model_validate(
                {
                    "proposal_id": uuid7(),
                    "revision": revision,
                    "dataset_version_id": dataset_version_id,
                    "profile_fingerprint": fingerprint,
                    "created_by": "AGENT",
                    "items": proposal_items,
                    "created_at": datetime.now(UTC),
                }
            )
            validation = self.proposals.validate(proposal)

        return EvaluationRecommendationV1(
            proposal=proposal,
            validation=validation,
            guide_coverage=coverage,
            recommendations=[
                "Only APPLICABLE items with an explicit Demo policy enter the Proposal",
                "Review Demo thresholds before publishing a Quality Contract",
            ],
        )

    @staticmethod
    def _parameters(calculator, content_roles: set[str]) -> dict:
        properties = calculator.parameter_schema.get("properties", {})
        parameters = {
            key: value["default"]
            for key, value in properties.items()
            if "default" in value
        }
        requires_one = calculator.applicability_constraints.get("requires_one_of", [])
        if "content_role" in requires_one and content_roles:
            preferred = "article.body.plain"
            parameters["content_role"] = (
                preferred if preferred in content_roles else min(content_roles)
            )
        return parameters

    @staticmethod
    def _candidate_sort_key(item) -> tuple:
        calculator, _, _, decision = item
        return (
            _STATUS_RANK[decision.status],
            0 if calculator.deterministic else 1,
            _COST_RANK[calculator.resource_profile.cost],
            calculator.calculator_key,
            calculator.version,
        )

    @staticmethod
    def _coverage_status(evaluated) -> tuple[str, str]:
        if not evaluated:
            return "UNAVAILABLE", "No registered Calculator binding"
        best = min(evaluated, key=EvaluationRecommendationService._candidate_sort_key)
        decision = best[3]
        if decision.status == "APPLICABLE":
            return "APPLICABLE", "Executable candidate selected"
        return decision.status, decision.findings[0].message


__all__ = ["DEMO_POLICY_V1", "EvaluationRecommendationService"]
