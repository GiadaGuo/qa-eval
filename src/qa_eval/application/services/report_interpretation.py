"""Generate optional commentary from an immutable report and numeric aggregates."""

import json
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from statistics import median
from threading import Lock
from time import monotonic
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from qa_eval.application.services import single_metric_interpretation
from qa_eval.infrastructure.llm.client import LLMResponseError, LLMTransportError, StructuredLLMClient


class UnknownInterpretationReference(ValueError):
    pass


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
        self._jobs: dict[tuple[UUID, str], tuple[Future, float]] = {}
        self._durations: deque[float] = deque(maxlen=10)

    def status(self, report_id, *, version="r6"):
        with self._lock:
            entry = self._jobs.get((report_id, version))
            if entry is None:
                return {"status": "NOT_REQUESTED"}
            job, submitted_at = entry
            if not job.done():
                elapsed = int(monotonic() - submitted_at)
                estimate = max(1, round(median(self._durations))) if self._durations else 60
                generating = job.running()
                return {
                    "status": "RUNNING",
                    "phase": "GENERATING" if generating else "QUEUED",
                    "elapsed_seconds": elapsed,
                    "estimated_remaining_seconds": (
                        estimate - elapsed if generating and elapsed < estimate else None
                    ),
                }
            failure = job.exception()
            if failure is not None:
                detail = "生成解读失败，请重试。"
                if isinstance(failure, LLMTransportError):
                    detail = "生成时模型请求失败，请重试。"
                elif isinstance(failure, (LLMResponseError, ValidationError, UnknownInterpretationReference)):
                    detail = "模型返回的解读格式或依据引用校验失败，请重新生成。"
                return {"status": "FAILED", "detail": detail}
            return {"status": "READY", "interpretation": job.result()}

    def start(self, report_id, facts, client_factory, *, regenerate, version="r6"):
        with self._lock:
            cache_key = (report_id, version)
            entry = self._jobs.get(cache_key)
            if entry is not None and (not regenerate or not entry[0].done()):
                return
            if len(self._jobs) >= 64 and entry is None:
                expired = next((key for key, (future, _) in self._jobs.items() if future.done()), None)
                if expired is None:
                    raise ValueError("智能解读任务队列已满")
                del self._jobs[expired]
            submitted_at = monotonic()
            generate = single_metric_interpretation.generate if version == single_metric_interpretation.VERSION else self._generate
            self._jobs[cache_key] = (
                self._executor.submit(self._generate_timed, generate, facts, client_factory, submitted_at),
                submitted_at,
            )

    def _generate_timed(self, generate, facts, client_factory, submitted_at):
        result = generate(facts, client_factory)
        with self._lock:
            self._durations.append(monotonic() - submitted_at)
        return result

    @staticmethod
    def _generate(facts, client_factory):
        client: StructuredLLMClient = client_factory()
        try:
            result = ReportInterpretation.model_validate(client.complete_json(
                system=(
                    "根据输入中的结构化事实编写中文质量报告解读。输出 JSON 对象，包含 "
                    "conclusion（对象）、causes、risks、recommendations（对象数组）。"
                    "每项包含 text（不超过 800 字）和 evidence_keys（1 至 8 个字符串）。"
                    "逐字复制 facts 的顶层键作为引用，不使用嵌套字段名或自行缩写。"
                    "每个数组最多 6 项，缺少依据时返回空数组。"
                    "保留门禁结果和评分，区分未判定与确认违规，禁止编造记录或数量。"
                ),
                user=json.dumps({"facts": facts}, ensure_ascii=False),
            ))
            items = [result.conclusion, *result.causes, *result.risks, *result.recommendations]
            if any(key not in facts for item in items for key in item.evidence_keys):
                raise UnknownInterpretationReference("Unknown interpretation reference")
            return {
                "sections": result.model_dump(),
                "facts": {key: facts[key] for item in items for key in item.evidence_keys},
            }
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                close()


report_interpretation_jobs = ReportInterpretationJobs()
