"""Generate optional commentary from an immutable report and numeric aggregates."""

import json
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from uuid import UUID

from pydantic import BaseModel, Field

from qa_eval.infrastructure.llm.client import StructuredLLMClient


class InterpretationItem(BaseModel):
    text: str = Field(min_length=1, max_length=800)
    evidence_keys: list[str] = Field(min_length=1, max_length=8)


class ReportInterpretation(BaseModel):
    conclusion: InterpretationItem
    causes: list[InterpretationItem] = Field(max_length=6)
    risks: list[InterpretationItem] = Field(max_length=6)
    recommendations: list[InterpretationItem] = Field(max_length=6)


class ReportInterpretationJobs:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="report-interpretation")
        self._lock = Lock()
        self._jobs: dict[UUID, Future] = {}

    def status(self, report_id):
        with self._lock:
            job = self._jobs.get(report_id)
            if job is None:
                return {"status": "NOT_REQUESTED"}
            if not job.done():
                return {"status": "RUNNING"}
            if job.exception():
                return {"status": "FAILED"}
            return {"status": "READY", "interpretation": job.result()}

    def start(self, report_id, facts, client_factory, *, regenerate):
        with self._lock:
            job = self._jobs.get(report_id)
            if job is not None and (not regenerate or not job.done()):
                return
            if len(self._jobs) >= 64 and job is None:
                expired = next((key for key, future in self._jobs.items() if future.done()), None)
                if expired is None:
                    raise ValueError("智能解读任务队列已满")
                del self._jobs[expired]
            self._jobs[report_id] = self._executor.submit(
                self._generate, facts, client_factory
            )

    @staticmethod
    def _generate(facts, client_factory):
        client: StructuredLLMClient = client_factory()
        try:
            result = ReportInterpretation.model_validate(client.complete_json(
                system=(
                    "根据输入中的结构化事实编写中文质量报告解读。输出 JSON 对象，包含 "
                    "conclusion（对象）、causes、risks、recommendations（对象数组）。"
                    "每项包含 text 和 evidence_keys，后者引用 facts 的真实键。"
                    "保留门禁结果和评分，区分未判定与确认违规，禁止编造记录或数量。"
                ),
                user=json.dumps({"facts": facts}, ensure_ascii=False),
            ))
            items = [result.conclusion, *result.causes, *result.risks, *result.recommendations]
            if any(key not in facts for item in items for key in item.evidence_keys):
                raise ValueError("Unknown interpretation reference")
            return {
                "sections": result.model_dump(),
                "facts": {key: facts[key] for item in items for key in item.evidence_keys},
            }
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                close()


report_interpretation_jobs = ReportInterpretationJobs()
