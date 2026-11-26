import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.application.agent.contracts import (
    AgentMessageResult,
    ContractConfirmationResult,
    EvaluationRunConfirmationResult,
    EvaluationRunObservationResult,
)
from qa_eval.application.agent.repository import SQLAlchemyAgentSessionRepository
from qa_eval.application.agent.skill_registry import SkillRegistry
from qa_eval.application.agent.tools.adapter import AgentToolAdapter, AgentToolError
from qa_eval.application.agent.tools.registry import (
    ToolNotAllowedError,
    ToolNotFoundError,
)
from qa_eval.application.services.evaluation_proposal_service import (
    EvaluationProposalService,
)
from qa_eval.contracts.proposals import EvaluationProposalV1
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.infrastructure.db.models.agent import (
    ConversationMessageModel,
    ProposalRevisionModel,
    SkillInvocationModel,
    ToolInvocationModel,
)
from qa_eval.infrastructure.llm.client import LLMError, StructuredLLMClient
from qa_eval.infrastructure.uuid7 import uuid7

_AGENT_SYSTEM_PROMPT = """你是 QA Eval 的数据质量评测 Agent。
你只能解释用户消息和后端确定性 Tool 提供的摘要，不得编造指标值、样本证据、执行状态或指南阈值。
Proposal 与 validation 是事实源；未执行的指标必须明确说尚未评测。
用简洁中文解释推荐理由并追问缺失配置，返回 JSON 对象 {"reply": "你的回复"}。
仅当用户明确给出修改时，可附带 edits 数组。每项用 key 指定提案项，可包含 enabled、
selected_calculator（必须来自当前候选）、parameters（仅需修改的键）、
dataset_binding（用户明确选择的 input_selectors）或 rule（如 threshold）。
禁止推断未确认的字段关系或阈值。"""


class AgentRuntime:
    """Bounded single-agent deterministic policy used when no LLM is configured."""

    def __init__(
        self,
        session: Session,
        *,
        tool_timeout_seconds: float = 10.0,
        max_tool_calls: int = 4,
        llm_client: StructuredLLMClient | None = None,
        artifact_store: ArtifactStore | None = None,
    ) -> None:
        self.session = session
        self.repository = SQLAlchemyAgentSessionRepository(session)
        self.llm_client = llm_client
        self.skill_registry = SkillRegistry()
        self.tool_adapter = AgentToolAdapter(
            session,
            timeout_seconds=tool_timeout_seconds,
            max_calls=max_tool_calls,
            artifact_store=artifact_store,
        )

    def handle_message(
        self,
        session_id: UUID,
        content: str,
        client_request_id: UUID,
    ) -> AgentMessageResult:
        agent_session = self.repository.get_session(session_id)
        if agent_session is None:
            raise LookupError("AgentSession not found")
        if agent_session.status != "ACTIVE":
            raise ValueError("AgentSession is not ACTIVE")

        user_message = ConversationMessageModel(
            message_id=uuid7(),
            session_id=session_id,
            role="USER",
            content=content,
            attachments=[
                {"type": "CLIENT_REQUEST", "client_request_id": str(client_request_id)}
            ],
        )
        self.repository.add(user_message)

        skill = self.skill_registry.select(content)
        agent_session.active_skill = skill.key
        skill_invocation = SkillInvocationModel(
            invocation_id=uuid7(),
            session_id=session_id,
            skill_key=skill.key,
            skill_version=skill.version,
            status="RUNNING",
            reason="Selected by deterministic V1 intent policy",
        )
        self.repository.add(skill_invocation)
        self.session.flush()

        tool_rows = []
        tool_results = []
        recommendation_result = None
        try:
            next_revision = len(self.repository.proposal_revisions(session_id)) + 1
            calls = (
                (
                    "get_dataset_profile",
                    {"dataset_version_id": str(agent_session.dataset_version_id)},
                ),
                (
                    "discover_metrics",
                    {
                        "dataset_version_id": str(agent_session.dataset_version_id),
                        "dimension": "DATA_QUALITY",
                    },
                ),
                (
                    "recommend_evaluation_proposal",
                    {
                        "dataset_version_id": str(agent_session.dataset_version_id),
                        "revision": next_revision,
                    },
                ),
            )
            for tool_name, arguments in calls:
                row = ToolInvocationModel(
                    invocation_id=uuid7(),
                    session_id=session_id,
                    tool_name=tool_name,
                    arguments_digest=self.tool_adapter.arguments_digest(arguments),
                    status="RUNNING",
                    result_refs=[],
                )
                self.repository.add(row)
                self.session.flush()
                tool_rows.append(row)
                try:
                    execution = self.tool_adapter.invoke(skill, tool_name, arguments)
                except (AgentToolError, ToolNotAllowedError, ToolNotFoundError) as exc:
                    row.status = "FAILED"
                    row.error = {
                        "code": getattr(exc, "code", "TOOL_EXECUTION_FAILED"),
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                    row.finished_at = datetime.now(UTC)
                    raise
                row.status = "SUCCEEDED"
                row.result_refs = [
                    {
                        "type": "INLINE_TOOL_RESULT",
                        "tool_name": tool_name,
                        "result": execution.result,
                    }
                ]
                row.finished_at = datetime.now(UTC)
                tool_results.append(execution.result)

            recommendation_result = tool_results[2]
            proposal_document = recommendation_result.get("proposal")
            validation_document = recommendation_result.get("validation")
            if proposal_document and tool_results[0]["profiler_key"] == "dataset_overview":
                previous = self.session.scalar(
                    select(ProposalRevisionModel)
                    .where(ProposalRevisionModel.session_id == session_id)
                    .order_by(ProposalRevisionModel.revision.desc()).limit(1)
                )
                if previous and previous.profile_fingerprint == proposal_document["profile_fingerprint"]:
                    proposal_document["items"] = previous.proposal["items"]
                    validation_document = EvaluationProposalService(
                        self.session, self.tool_adapter.artifact_store
                    ).validate(EvaluationProposalV1.model_validate(proposal_document)).model_dump(mode="json")
                    recommendation_result["validation"] = validation_document

            skill_invocation.status = "SUCCEEDED"
            skill_invocation.finished_at = datetime.now(UTC)
            metric_count = len(tool_results[1]["metrics"])
            available_count = sum(
                1
                for metric in tool_results[1]["metrics"]
                for calculator in metric["calculators"]
                if calculator["enabled"] and calculator["availability"] == "AVAILABLE"
            )
            proposed_count = len((proposal_document or {}).get("items", []))
            fallback_reply = (
                "已加载数据质量 Skill，读取当前数据集概要，并发现 "
                f"{metric_count} 个数据质量指标、{available_count} 个可用实现；"
                f"已生成 {proposed_count} 个候选提案项，可在方案中查看预检结果。"
            )
            reply, model_metadata = self._model_reply(
                content=content,
                dataset_version_id=agent_session.dataset_version_id,
                metric_count=metric_count,
                available_count=available_count,
                recommendation=recommendation_result,
                overview=tool_results[0]["profile"] if tool_results[0]["profiler_key"] == "dataset_overview" else {},
                fallback=fallback_reply,
                recent_messages=[
                    {"role": message.role, "content": message.content[:500]}
                    for message in self.repository.messages(session_id)[-5:-1]
                ] if tool_results[0]["profiler_key"] == "dataset_overview" and self.llm_client else [],
            )
            if proposal_document is not None:
                validation_document = recommendation_result.get("validation")
                proposal_revision = ProposalRevisionModel(
                    proposal_revision_id=uuid7(),
                    session_id=session_id,
                    revision=proposal_document["revision"],
                    proposal=proposal_document,
                    validation=validation_document,
                    profile_fingerprint=proposal_document["profile_fingerprint"],
                )
                self.repository.add(proposal_revision)

        except (AgentToolError, ToolNotAllowedError, ToolNotFoundError) as exc:
            skill_invocation.status = "FAILED"
            skill_invocation.reason = str(exc)
            skill_invocation.finished_at = datetime.now(UTC)
            reply = f"数据质量预检失败：{exc}"
            model_metadata = {"mode": "DETERMINISTIC_FALLBACK", "reason": "tool_failed"}

        assistant_message = ConversationMessageModel(
            message_id=uuid7(),
            session_id=session_id,
            role="ASSISTANT",
            content=reply,
            attachments=self._attachments(
                tool_rows, recommendation_result, model_metadata
            ),
        )
        self.repository.add(assistant_message)
        agent_session.updated_at = datetime.now(UTC)
        self.session.flush()
        return AgentMessageResult(
            message=assistant_message,
            skill_invocation=skill_invocation,
            tool_invocations=tool_rows,
        )

    def confirm_quality_contract(
        self,
        session_id: UUID,
        proposal_revision_id: UUID,
        *,
        name: str,
        version_label: str,
        client_request_id: UUID,
    ) -> ContractConfirmationResult:
        agent_session = self.repository.get_session(session_id)
        if agent_session is None:
            raise LookupError("AgentSession not found")
        if agent_session.status != "ACTIVE":
            raise ValueError("AgentSession is not ACTIVE")
        revision = self.session.get(ProposalRevisionModel, proposal_revision_id)
        if revision is None or revision.session_id != session_id:
            raise LookupError("Proposal revision not found in AgentSession")
        if not revision.validation or revision.validation.get("valid") is not True:
            raise ValueError("Only a valid Proposal revision can be confirmed")
        revision_id = str(proposal_revision_id)
        if any(
            attachment.get("type") == "HUMAN_CONFIRMATION"
            and attachment.get("proposal_revision_id") == revision_id
            for message in self.repository.messages(session_id)
            for attachment in message.attachments
        ):
            raise ValueError("Proposal revision has already been published")

        proposal = EvaluationProposalV1.model_validate(revision.proposal)
        if proposal.dataset_version_id != agent_session.dataset_version_id:
            raise ValueError("Proposal DatasetVersion does not match AgentSession")
        contract_items, observations = EvaluationProposalService(
            self.session, self.tool_adapter.artifact_store
        ).publication_items(
            proposal
        )
        arguments = {
            "dataset_version_id": str(agent_session.dataset_version_id),
            "name": name,
            "version_label": version_label,
            "items": [item.model_dump(mode="json") for item in contract_items],
            "observation_items": [item.model_dump(mode="json") for item in observations],
        }
        row = ToolInvocationModel(
            invocation_id=uuid7(),
            session_id=session_id,
            tool_name="create_quality_contract",
            arguments_digest=self.tool_adapter.arguments_digest(arguments),
            status="RUNNING",
            result_refs=[],
        )
        self.repository.add(row)
        self.session.flush()
        skill = self.skill_registry.load("data-quality")
        try:
            execution = self.tool_adapter.invoke(skill, row.tool_name, arguments)
        except (AgentToolError, ToolNotAllowedError, ToolNotFoundError) as exc:
            row.status = "FAILED"
            row.error = {
                "code": getattr(exc, "code", "TOOL_EXECUTION_FAILED"),
                "type": type(exc).__name__,
                "message": str(exc),
            }
            row.finished_at = datetime.now(UTC)
            raise ValueError(str(exc)) from exc

        result = execution.result
        row.status = "SUCCEEDED"
        row.result_refs = [{"type": "QUALITY_CONTRACT", **result}]
        row.finished_at = datetime.now(UTC)
        message = ConversationMessageModel(
            message_id=uuid7(),
            session_id=session_id,
            role="ASSISTANT",
            content=(
                f"已按人工确认发布 1 个多指标 Quality Contract，包含 "
                f"{result['item_count']} 个 items。"
            ),
            attachments=[
                {
                    "type": "HUMAN_CONFIRMATION",
                    "client_request_id": str(client_request_id),
                    "proposal_revision_id": revision_id,
                },
                {"type": "QUALITY_CONTRACT", **result},
            ],
        )
        self.repository.add(message)
        agent_session.updated_at = datetime.now(UTC)
        self.session.flush()
        return ContractConfirmationResult(
            message=message,
            tool_invocation=row,
            contract_version_id=result["contract_version_id"],
            contract_id=result["contract_id"],
            item_count=result["item_count"],
        )

    def confirm_evaluation_run(
        self,
        session_id: UUID,
        contract_version_id: UUID,
        *,
        client_request_id: UUID,
    ) -> EvaluationRunConfirmationResult:
        agent_session = self.repository.get_session(session_id)
        if agent_session is None:
            raise LookupError("AgentSession not found")
        if agent_session.status != "ACTIVE":
            raise ValueError("AgentSession is not ACTIVE")
        contract_id = str(contract_version_id)
        messages = self.repository.messages(session_id)
        owns_contract = any(
            attachment.get("type") == "QUALITY_CONTRACT"
            and attachment.get("contract_version_id") == contract_id
            for message in messages
            for attachment in message.attachments
        )
        if not owns_contract:
            raise ValueError("Quality Contract was not confirmed in this AgentSession")
        if any(
            attachment.get("type") == "EVALUATION_RUN"
            and attachment.get("contract_version_id") == contract_id
            for message in messages
            for attachment in message.attachments
        ):
            raise ValueError("Quality Contract already has a Run in this AgentSession")

        arguments = {"contract_version_id": contract_id}
        row, result = self._invoke_audited_tool(
            session_id, "create_evaluation_run", arguments
        )
        message = ConversationMessageModel(
            message_id=uuid7(),
            session_id=session_id,
            role="ASSISTANT",
            content=f"已提交真实 Evaluation Run：{result['run_id']}，当前状态 {result['status']}。",
            attachments=[
                {
                    "type": "RUN_SUBMISSION_CONFIRMATION",
                    "client_request_id": str(client_request_id),
                    "contract_version_id": contract_id,
                },
                {"type": "EVALUATION_RUN", **result},
            ],
        )
        self.repository.add(message)
        agent_session.updated_at = datetime.now(UTC)
        self.session.flush()
        return EvaluationRunConfirmationResult(
            message=message,
            tool_invocation=row,
            run_id=result["run_id"],
            status=result["status"],
        )

    def observe_evaluation_run(
        self, session_id: UUID, run_id: UUID
    ) -> EvaluationRunObservationResult:
        agent_session = self.repository.get_session(session_id)
        if agent_session is None:
            raise LookupError("AgentSession not found")
        messages = self.repository.messages(session_id)
        run_id_text = str(run_id)
        owns_run = any(
            attachment.get("type") == "EVALUATION_RUN"
            and attachment.get("run_id") == run_id_text
            for message in messages
            for attachment in message.attachments
        )
        if not owns_run:
            raise LookupError("EvaluationRun not found in AgentSession")

        _, status_result = self._invoke_audited_tool(
            session_id, "get_evaluation_status", {"run_id": run_id_text}
        )
        terminal = status_result["status"] in {
            "PARTIAL_SUCCEEDED",
            "SUCCEEDED",
            "FAILED",
            "CANCELED",
        }
        if not terminal:
            return EvaluationRunObservationResult(
                run_id=run_id, status=status_result["status"], terminal=False
            )

        existing = next(
            (
                attachment
                for message in messages
                for attachment in message.attachments
                if attachment.get("type") == "EVALUATION_RUN_COMPLETED"
                and attachment.get("run_id") == run_id_text
            ),
            None,
        )
        if existing is not None:
            return EvaluationRunObservationResult(
                run_id=run_id,
                status=status_result["status"],
                terminal=True,
                report_id=existing.get("report_id"),
                gate_status=existing.get("gate_status"),
            )

        _, report_result = self._invoke_audited_tool(
            session_id, "get_quality_report", {"run_id": run_id_text}
        )
        finalization_failed = (status_result.get("error") or {}).get(
            "phase"
        ) == "QUALITY_RESULT_FINALIZATION"
        if (
            not report_result["found"]
            and not finalization_failed
            and status_result["status"] not in {"FAILED", "CANCELED"}
        ):
            # EvaluationRunService persists the terminal Run before finalizing
            # its report. Keep polling through that short, valid transition.
            return EvaluationRunObservationResult(
                run_id=run_id,
                status=status_result["status"],
                terminal=False,
            )
        report_summary = (
            f"报告 Gate 状态 {report_result['gate_status']}，"
            f"已评测 {report_result['evaluated_count']} 项，"
            f"错误 {report_result['error_count']} 项，"
            f"确认问题 {report_result['confirmed_issue_count']} 个。"
            if report_result["found"]
            else "报告生成失败，请查看 Run error 后重试报告生成。"
        )
        completion = {
            "type": "EVALUATION_RUN_COMPLETED",
            "run_id": run_id_text,
            "status": status_result["status"],
            "report_id": report_result.get("report_id"),
            "gate_status": report_result.get("gate_status"),
        }
        message = ConversationMessageModel(
            message_id=uuid7(),
            session_id=session_id,
            role="ASSISTANT",
            content=(
                f"Evaluation Run {run_id_text} 已结束，执行状态 "
                f"{status_result['status']}。{report_summary}"
            ),
            attachments=[completion],
        )
        self.repository.add(message)
        agent_session.status = (
            "FAILED"
            if status_result["status"] in {"FAILED", "CANCELED"}
            else "COMPLETED"
        )
        agent_session.updated_at = datetime.now(UTC)
        self.session.flush()
        return EvaluationRunObservationResult(
            run_id=run_id,
            status=status_result["status"],
            terminal=True,
            completion_message_created=True,
            report_id=report_result.get("report_id"),
            gate_status=report_result.get("gate_status"),
        )

    def _invoke_audited_tool(
        self, session_id: UUID, tool_name: str, arguments: dict
    ) -> tuple[ToolInvocationModel, dict]:
        row = ToolInvocationModel(
            invocation_id=uuid7(),
            session_id=session_id,
            tool_name=tool_name,
            arguments_digest=self.tool_adapter.arguments_digest(arguments),
            status="RUNNING",
            result_refs=[],
        )
        self.repository.add(row)
        self.session.flush()
        try:
            execution = self.tool_adapter.invoke(
                self.skill_registry.load("data-quality"), tool_name, arguments
            )
        except (AgentToolError, ToolNotAllowedError, ToolNotFoundError) as exc:
            row.status = "FAILED"
            row.error = {
                "code": getattr(exc, "code", "TOOL_EXECUTION_FAILED"),
                "type": type(exc).__name__,
                "message": str(exc),
            }
            row.finished_at = datetime.now(UTC)
            raise ValueError(str(exc)) from exc
        row.status = "SUCCEEDED"
        row.result_refs = [
            {
                "type": "INLINE_TOOL_RESULT",
                "tool_name": tool_name,
                "result": execution.result,
            }
        ]
        row.finished_at = datetime.now(UTC)
        return row, execution.result

    def _model_reply(
        self,
        *,
        content: str,
        dataset_version_id: UUID,
        metric_count: int,
        available_count: int,
        recommendation: dict,
        fallback: str,
        overview: dict | None = None,
        recent_messages: list[dict] | None = None,
    ) -> tuple[str, dict]:
        if self.llm_client is None:
            return fallback, {
                "mode": "DETERMINISTIC_FALLBACK",
                "reason": "not_configured",
            }

        proposal = recommendation.get("proposal") or {}
        validation = recommendation.get("validation") or {}
        summary = {
            "user_message": content[:2000],
            "recent_messages": recent_messages or [],
            "dataset_overview": {
                "record_count": overview["record_count"],
                "size_bytes": overview["size_bytes"],
                "modalities": overview["modalities"],
                "artifact_kinds": overview["artifact_kinds"],
                "fields": [
                    {"name": field["name"][:100], "type": field["data_type"]}
                    for field in overview["fields"][:48]
                ],
                "semantics": [
                    {"semantic": binding["semantic"], "representation": binding["representation"]}
                    for binding in overview["semantics"][:24]
                ],
            } if overview else {},
            "dataset_version_id": str(dataset_version_id),
            "discovered_metric_count": metric_count,
            "available_calculator_count": available_count,
            "proposal_items": [
                {
                    "metric": item.get("metric"),
                    "selected_calculator": item.get("selected_calculator"),
                    "rule": item.get("rule"),
                    "key": item.get("key"),
                    "enabled": item.get("enabled"),
                    "candidates": item.get("candidates", [])[:6],
                    "parameters": list(item.get("parameter_schema", {}).get("properties", {}))[:20],
                    "required_parameters": item.get("parameter_schema", {}).get("required", [])[:20],
                }
                for item in proposal.get("items", [])[:24]
            ],
            "proposal_valid": validation.get("valid"),
            "guide_coverage": [
                {
                    "metric_key": item.get("metric_key"),
                    "status": item.get("status"),
                    "reason": str(item.get("reason", ""))[:240],
                    "candidates": [
                        {
                            "calculator": candidate["calculator"],
                            "status": candidate["applicability"]["status"],
                            "inputs": candidate.get("input_requirements", {}) | {
                                "slots": candidate.get("input_requirements", {}).get("slots", [])[:8],
                            },
                        }
                        for candidate in item.get("candidates", [])[:3]
                    ] if overview else [],
                }
                for item in recommendation.get("guide_coverage", [])[:24]
            ],
        }
        payload = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
        if len(payload) > 16000:
            for entry in reversed(summary["guide_coverage"]):
                entry.pop("candidates", None)
                payload = json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
                if len(payload) <= 16000:
                    break
        try:
            result = self.llm_client.complete_json(
                system=_AGENT_SYSTEM_PROMPT,
                user=payload,
            )
            reply = result.get("reply")
            if not isinstance(reply, str) or not reply.strip():
                raise ValueError("LLM response does not contain a non-empty reply")
            if overview and proposal and isinstance(result.get("edits"), list):
                try:
                    self._apply_proposal_edits(recommendation, result["edits"])
                except (ValueError, TypeError, AttributeError) as exc:
                    return f"{fallback} 本轮配置未保存，请确认算子和参数。", {
                        "mode": "DETERMINISTIC_FALLBACK", "reason": type(exc).__name__,
                    }
            return reply.strip(), {
                "mode": "MODEL",
                "model": self.llm_client.model,
            }
        except (LLMError, ValueError) as exc:
            return fallback, {
                "mode": "DETERMINISTIC_FALLBACK",
                "model": self.llm_client.model,
                "reason": type(exc).__name__,
            }

    def _apply_proposal_edits(self, recommendation, edits):
        proposal = EvaluationProposalV1.model_validate(recommendation["proposal"])
        service = EvaluationProposalService(self.session, self.tool_adapter.artifact_store)
        document = proposal.model_dump(mode="json")
        items = {item["key"]: item for item in document["items"]}
        for edit in edits[:32]:
            if not isinstance(edit, dict) or edit.get("key") not in items:
                raise ValueError("Unknown proposal item")
            item = items[edit["key"]]
            selection = edit.get("selected_calculator")
            if selection is not None and selection != item["selected_calculator"]:
                if selection not in item["candidates"]:
                    raise ValueError("Unknown candidate")
                calculator = service.contracts._calculator(selection["key"], selection["version"])
                item.update(
                    selected_calculator=selection,
                    parameter_schema=calculator.parameter_schema,
                    parameters={
                        key: value["default"]
                        for key, value in calculator.parameter_schema.get("properties", {}).items()
                        if "default" in value
                    },
                    dataset_binding={"mapping_digest": item["dataset_binding"].get("mapping_digest")},
                    execution_mode=calculator.execution.mode,
                    semantic_coverage=calculator.semantic_coverage,
                )
            if "enabled" in edit:
                item["enabled"] = edit["enabled"]
            for key in ("parameters", "rule"):
                if key in edit:
                    item[key] = item[key] | edit[key]
            if "dataset_binding" in edit:
                selectors = edit["dataset_binding"].get("input_selectors")
                if selectors is not None:
                    item["dataset_binding"] = item["dataset_binding"] | {"input_selectors": selectors}
        updated = EvaluationProposalV1.model_validate(document)
        recommendation["validation"] = service.validate(updated).model_dump(mode="json")
        proposal_document = recommendation["proposal"]
        proposal_document.update(updated.model_dump(mode="json"))

    @staticmethod
    def _attachments(tool_rows, recommendation_result, model_metadata) -> list[dict]:
        attachments = [
            {
                "type": "TOOL_INVOCATIONS",
                "invocation_ids": [str(row.invocation_id) for row in tool_rows],
            },
            {"type": "AGENT_RESPONSE_MODE", **model_metadata},
        ]
        proposal = (recommendation_result or {}).get("proposal")
        if proposal is not None:
            attachments.append(
                {
                    "type": "EVALUATION_PROPOSAL",
                    "proposal_id": proposal["proposal_id"],
                    "revision": proposal["revision"],
                }
            )
        return attachments
