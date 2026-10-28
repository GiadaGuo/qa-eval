import hashlib
import json
from dataclasses import dataclass
from time import monotonic
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.application.agent.skill_registry import SkillDescriptor
from qa_eval.application.agent.tools.contracts import (
    CalculatorSummary,
    DatasetProfileToolResult,
    DiscoverMetricsInput,
    DiscoverMetricsToolResult,
    GetDatasetProfileInput,
    MetricDiscoveryItem,
    RecommendEvaluationProposalInput,
    RecommendEvaluationProposalToolResult,
)
from qa_eval.application.agent.tools.registry import ToolDefinition, ToolRegistry
from qa_eval.application.services.evaluation_recommendation_service import (
    EvaluationRecommendationService,
)
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.infrastructure.db.models.dataset_profile import DatasetProfileModel


class AgentToolError(RuntimeError):
    code = "AGENT_TOOL_ERROR"


class AgentToolTimeoutError(AgentToolError):
    code = "AGENT_TOOL_TIMEOUT"


class AgentToolCallLimitError(AgentToolError):
    code = "AGENT_TOOL_CALL_LIMIT"


@dataclass(frozen=True)
class ToolExecution:
    name: str
    arguments_digest: str
    result: dict[str, Any]
    elapsed_seconds: float


class AgentToolAdapter:
    def __init__(
        self,
        session: Session,
        *,
        timeout_seconds: float = 10.0,
        max_calls: int = 4,
    ) -> None:
        self.session = session
        self.timeout_seconds = timeout_seconds
        self.max_calls = max_calls
        self.call_count = 0
        self.registry = ToolRegistry()
        self.registry.register(
            ToolDefinition(
                name="get_dataset_profile",
                description="读取当前 Dataset Version 最新的 canonical Profile。",
                input_model=GetDatasetProfileInput,
                handler=self._get_dataset_profile,
            )
        )
        self.registry.register(
            ToolDefinition(
                name="recommend_evaluation_proposal",
                description=(
                    "基于 Registry、Binding、Applicability 与 Demo policy 生成确定性方案。"
                ),
                input_model=RecommendEvaluationProposalInput,
                handler=self._recommend_evaluation_proposal,
            )
        )
        self.registry.register(
            ToolDefinition(
                name="discover_metrics",
                description="查询平台注册的数据质量 Metric、Calculator 与 Binding。",
                input_model=DiscoverMetricsInput,
                handler=self._discover_metrics,
            )
        )

    def invoke(
        self,
        skill: SkillDescriptor,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> ToolExecution:
        if self.call_count >= self.max_calls:
            raise AgentToolCallLimitError(
                f"Agent tool call limit exceeded: {self.max_calls}"
            )
        definition = self.registry.get(tool_name, allowed_tools=skill.allowed_tools)
        self.call_count += 1
        digest = self.arguments_digest(arguments)
        started = monotonic()
        try:
            validated = definition.input_model.model_validate(arguments)
        except ValidationError as exc:
            raise AgentToolError(f"Invalid tool arguments: {tool_name}") from exc
        try:
            result = definition.handler(validated)
        except ValueError as exc:
            raise AgentToolError(f"Tool failed: {tool_name}: {exc}") from exc
        elapsed = monotonic() - started
        if elapsed > self.timeout_seconds:
            raise AgentToolTimeoutError(
                f"Tool exceeded {self.timeout_seconds:g}s timeout: {tool_name}"
            )
        return ToolExecution(
            name=tool_name,
            arguments_digest=digest,
            result=result.model_dump(mode="json"),
            elapsed_seconds=elapsed,
        )

    @staticmethod
    def arguments_digest(arguments: dict[str, Any]) -> str:
        canonical = json.dumps(
            arguments, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"

    def _get_dataset_profile(self, payload: BaseModel) -> DatasetProfileToolResult:
        args = GetDatasetProfileInput.model_validate(payload.model_dump())
        profile = self.session.scalar(
            select(DatasetProfileModel)
            .where(DatasetProfileModel.dataset_version_id == args.dataset_version_id)
            .order_by(DatasetProfileModel.created_at.desc(), DatasetProfileModel.id.desc())
            .limit(1)
        )
        if profile is None:
            raise AgentToolError("Dataset Profile not found")
        return DatasetProfileToolResult(
            dataset_version_id=args.dataset_version_id,
            profile_fingerprint=f"sha256:{profile.checksum_sha256}",
            profiler_key=profile.profiler_key,
            profiler_version=profile.profiler_version,
            profile=profile.document,
        )

    def _discover_metrics(self, payload: BaseModel) -> DiscoverMetricsToolResult:
        args = DiscoverMetricsInput.model_validate(payload.model_dump())
        registry = MetricRegistryService(self.session)
        calculators = {
            (item.calculator_key, item.version): item
            for item in registry.list_calculators()
        }
        bindings_by_metric: dict[tuple[str, str], list] = {}
        for binding in registry.list_bindings():
            bindings_by_metric.setdefault(
                (binding.metric_key, binding.metric_version), []
            ).append(binding)
        items = []
        for metric in registry.list_metrics():
            if metric.dimension != args.dimension:
                continue
            candidates = []
            for binding in bindings_by_metric.get((metric.metric_key, metric.version), []):
                calculator = calculators.get(
                    (binding.calculator_key, binding.calculator_version)
                )
                if calculator is None:
                    continue
                candidates.append(
                    CalculatorSummary(
                        key=calculator.calculator_key,
                        version=calculator.version,
                        availability=calculator.availability.status,
                        deterministic=calculator.deterministic,
                        cost=calculator.resource_profile.cost,
                        enabled=binding.enabled,
                    )
                )
            items.append(
                MetricDiscoveryItem(
                    metric_key=metric.metric_key,
                    version=metric.version,
                    name=metric.name,
                    calculators=candidates,
                )
            )
        return DiscoverMetricsToolResult(
            dataset_version_id=args.dataset_version_id,
            dimension=args.dimension,
            metrics=items,
        )

    def _recommend_evaluation_proposal(
        self, payload: BaseModel
    ) -> RecommendEvaluationProposalToolResult:
        args = RecommendEvaluationProposalInput.model_validate(payload.model_dump())
        result = EvaluationRecommendationService(self.session).recommend(
            args.dataset_version_id,
            revision=args.revision,
        )
        return RecommendEvaluationProposalToolResult.model_validate(
            result.model_dump(mode="json")
        )
