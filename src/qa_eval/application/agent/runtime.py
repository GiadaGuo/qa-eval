import json
from datetime import UTC, datetime
from uuid import UUID

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
用简洁中文回答，并返回 JSON 对象：{"reply": "你的回复"}。"""


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
            if proposal_document is not None:
                proposal_revision = ProposalRevisionModel(
                    proposal_revision_id=uuid7(),
                    session_id=session_id,
                    revision=proposal_document["revision"],
                    proposal=proposal_document,
                    validation=validation_document,
                    profile_fingerprint=proposal_document["profile_fingerprint"],
                )
                self.repository.add(proposal_revision)

            skill_invocation.status = "SUCCEEDED"
            skill_invocation.finished_at = datetime.now(UTC)
            metric_count = len(tool_results[1]["metrics"])
            available_count = sum(
                1
                for metric in tool_results[1]["metrics"]
                for calculator in metric["calculators"]
                if calculator["enabled"]
                and calculator["availability"] == "AVAILABLE"
            )
            proposed_count = len((proposal_document or {}).get("items", []))
            fallback_reply = (
                "已加载数据质量 Skill，读取当前 Dataset Profile，并发现 "
                f"{metric_count} 个数据质量指标、{available_count} 个可用实现；"
                f"确定性推荐生成了 {proposed_count} 个可执行 Proposal item。"
            )
            reply, model_metadata = self._model_reply(
                content=content,
                dataset_version_id=agent_session.dataset_version_id,
                metric_count=metric_count,
                available_count=available_count,
                recommendation=recommendation_result,
                fallback=fallback_reply,
            )
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
        arguments = {
            "dataset_version_id": str(agent_session.dataset_version_id),
            "name": name,
            "version_label": version_label,
            "items": [
                {
                    "key": item.key,
                    "metric_key": item.metric.key,
                    "metric_version": item.metric.version,
                    "measurement": item.metric.measurement,
                    "calculator_key": item.selected_calculator.key,
                    "calculator_version": item.selected_calculator.version,
                    "parameters": item.parameters,
                    "rule": item.rule.model_dump(mode="json"),
                }
                for item in proposal.items
            ],
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
        finalization_failed = (
            (status_result.get("error") or {}).get("phase")
            == "QUALITY_RESULT_FINALIZATION"
        )
        if not report_result["found"] and not finalization_failed:
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
            {"type": "INLINE_TOOL_RESULT", "tool_name": tool_name, "result": execution.result}
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
    ) -> tuple[str, dict]:
        if self.llm_client is None:
            return fallback, {"mode": "DETERMINISTIC_FALLBACK", "reason": "not_configured"}

        proposal = recommendation.get("proposal") or {}
        validation = recommendation.get("validation") or {}
        summary = {
            "user_message": content,
            "dataset_version_id": str(dataset_version_id),
            "discovered_metric_count": metric_count,
            "available_calculator_count": available_count,
            "proposal_items": [
                {
                    "metric": item.get("metric"),
                    "selected_calculator": item.get("selected_calculator"),
                    "rule": item.get("rule"),
                    "limitations": item.get("limitations", []),
                }
                for item in proposal.get("items", [])
            ],
            "proposal_valid": validation.get("valid"),
            "guide_coverage": [
                {
                    "metric_key": item.get("metric_key"),
                    "status": item.get("status"),
                    "reason": item.get("reason"),
                }
                for item in recommendation.get("guide_coverage", [])
            ],
        }
        try:
            result = self.llm_client.complete_json(
                system=_AGENT_SYSTEM_PROMPT,
                user=json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
            )
            reply = result.get("reply")
            if not isinstance(reply, str) or not reply.strip():
                raise ValueError("LLM response does not contain a non-empty reply")
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
