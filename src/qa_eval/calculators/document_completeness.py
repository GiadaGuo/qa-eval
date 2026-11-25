from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from qa_eval.application.document_evaluation.evaluator import DocumentEvaluator
from qa_eval.application.document_evaluation.parser import parse_document
from qa_eval.calculators.protocol import (
    CalculatorError,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.config import get_settings
from qa_eval.infrastructure.llm.client import OpenAICompatibleLLMClient

MEASUREMENTS = ("satisfied_aspect_count", "required_aspect_count", "completeness_ratio")
EVIDENCE_SCHEMA = "qa-eval://schemas/evidence/document-aspect-checks/1.0.0"


class Aspect(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    item_code: str = Field(min_length=1)
    item_name: str = Field(min_length=1)
    satisfied_standard: str = Field(min_length=1)
    counterexamples: list[str] = Field(default_factory=list)
    notes: str | None = None
    applicable: bool = True


class AspectConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    items: list[Aspect]

    @model_validator(mode="after")
    def unique_codes(self):
        if len({item.item_code for item in self.items}) != len(self.items):
            raise ValueError("检查项编码必须唯一")
        return self

    def digest(self):
        data = json.dumps(
            self.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return "sha256:" + hashlib.sha256(data).hexdigest()


class DocumentCompletenessCalculator:
    version = "1.0.0"

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    def source_paths(self):
        return Path(__file__), self.rubric_path

    @property
    def rubric_path(self):
        return Path(__file__).with_name("document_completeness_rubric.json")

    def configuration(self, parameters):
        rubric = json.loads(self.rubric_path.read_bytes())[self.metric_key]
        default = AspectConfiguration.model_validate(rubric["configuration"])
        if default.digest() != rubric["sha256"]:
            raise CalculatorError("默认检查项指纹不一致")
        if set(parameters) - {"configuration"}:
            raise CalculatorValidationError("不支持的说明文档算子参数")
        try:
            config = AspectConfiguration.model_validate(
                parameters.get("configuration", default.model_dump())
            )
        except ValidationError as exc:
            raise CalculatorValidationError("检查项配置无效") from exc
        if config.version == default.version and config != default:
            raise CalculatorValidationError("修改默认检查项时须使用新配置版本")
        return rubric["metric_name"], config

    def validate(self, context, parameters):
        if (context.metric_key, context.metric_version) != (
            self.metric_key,
            self.version,
        ):
            raise CalculatorValidationError("指标与说明文档算子不匹配")
        source = context.input_artifacts.get("DOCUMENT")
        if context.parsed_document is None and (source is None or not source.is_file()):
            raise CalculatorValidationError("缺少 DOCUMENT 输入")
        self.configuration(parameters)
        return []

    def execute(self, context, parameters):
        self.validate(context, parameters)
        metric_name, config = self.configuration(parameters)
        source = context.input_artifacts.get("DOCUMENT")
        try:
            document = context.parsed_document or parse_document(
                source.read_bytes(), source.suffix.lower()
            )
        except (ValueError, OSError) as exc:
            raise CalculatorValidationError(f"说明文档解析失败：{exc}") from exc

        applicable = [item.model_dump() for item in config.items if item.applicable]
        judgments = {}
        if applicable:
            client = self.llm_client
            if client is None:
                settings = get_settings()
                # This gateway's qwen3.8 accepts the chat-template switch.
                client = OpenAICompatibleLLMClient.from_settings(
                    settings,
                    qwen_enable_thinking=False
                    if settings.llm_model == "qwen3.8"
                    else None,
                )
            if client is None:
                raise CalculatorError("LLM 网关未配置")
            try:
                # Full input, including the suffix. Gateway/context failures fail the run.
                outcome = DocumentEvaluator(client).evaluate_metric(
                    {
                        "metric_code": self.metric_key,
                        "metric_name": metric_name,
                        "items": applicable,
                    },
                    document,
                    formal=True,
                )
            finally:
                if self.llm_client is None:
                    client.close()
            if outcome.status != "OK":
                raise CalculatorError("说明文档判定失败", reason=outcome.error)
            judgments = {item["item_code"]: item for item in outcome.items}

        a = sum(item["status"] == "SATISFIED" for item in judgments.values())
        b = len(applicable)
        excluded = len(config.items) - b
        metadata = {
            "document_sha256": document.sha256,
            "configuration_version": config.version,
            "configuration_sha256": config.digest(),
        }
        path = context.output_dir / "document-aspect-checks.jsonl"
        context.output_dir.mkdir(parents=True, exist_ok=True)
        contains_quotes = False
        with path.open("w", encoding="utf-8") as stream:
            for aspect in config.items:
                judgment = judgments.get(aspect.item_code)
                row = {
                    **metadata,
                    "item_code": aspect.item_code,
                    "row_id": aspect.item_code,
                    "outcome": judgment["status"] if judgment else "NOT_APPLICABLE",
                    "reason": judgment["reason"]
                    if judgment
                    else "配置明确排除该检查项",
                    "inspected_scope": {"block_count": len(document.blocks)}
                    if judgment
                    else None,
                    "quotes": [],
                }
                for quote in judgment["document_evidence"] if judgment else []:
                    blocks = document.blocks[
                        quote["line_start"] - 1 : quote["line_end"]
                    ]
                    row["quotes"].append(
                        {
                            "excerpt": quote["excerpt"],
                            "blocks": [
                                {"block_id": block.block_id, "locator": block.locator}
                                for block in blocks
                            ],
                        }
                    )
                    contains_quotes = True
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")

        observations = (
            [
                {"key": key, "value_type": "NUMBER", "value": value, "unit": unit}
                for key, value, unit in zip(
                    MEASUREMENTS,
                    (a, b, a / b if b else None),
                    ("aspect", "aspect", "ratio"),
                    strict=True,
                )
            ]
            if b
            else []
        )
        if observations:
            observations[-1].update(numerator=a, denominator=b)
        return CalculatorOutput(
            status="OK" if b else "NOT_APPLICABLE",
            status_detail={
                **metadata,
                **({} if b else {"reason": "NO_APPLICABLE_ASPECTS"}),
            },
            observations=observations,
            population={
                "input_count": len(config.items),
                "evaluated_count": b,
                "excluded_count": excluded,
                "exclusions": [
                    {"reason": "CONFIGURED_NOT_APPLICABLE", "count": excluded}
                ]
                if excluded
                else [],
            },
            semantic_coverage={
                "level": "FULL",
                "covered": ["document disclosure completeness"],
                "excluded": ["external factual verification"],
            },
            evidence=[
                EvidenceOutput(
                    path=path,
                    kind="DOCUMENT_ASPECT_CHECKS",
                    schema_ref=EVIDENCE_SCHEMA,
                    record_count=len(config.items),
                    subject_scope="DOCUMENT",
                    contains_raw_content=contains_quotes,
                    redaction_policy="verbatim-document-excerpts",
                )
            ],
        )


class BasicInformationCalculator(DocumentCompletenessCalculator):
    calculator_key = "document_basic_information"
    metric_key = "0101"


class ContentCharacteristicsCalculator(DocumentCompletenessCalculator):
    calculator_key = "document_content_characteristics"
    metric_key = "0102"


class ConstructionProcessCalculator(DocumentCompletenessCalculator):
    calculator_key = "document_construction_process"
    metric_key = "0103"


class ApplicationInstructionsCalculator(DocumentCompletenessCalculator):
    calculator_key = "document_application_instructions"
    metric_key = "0104"
