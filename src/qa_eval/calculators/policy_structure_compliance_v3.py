import json
from collections import Counter
from dataclasses import replace
from pathlib import Path

from qa_eval.calculators import policy_resolved_fields
from qa_eval.calculators.policy_resolved_fields import (
    field,
    origins,
    policy_record,
    require_resolver,
)
from qa_eval.calculators.policy_structure_compliance.calculator import (
    DEFAULT_MAX_HTML_LENGTH,
    FLAGS,
    PARSER_VERSION,
    STATUSES,
    FlagResult,
    PolicyStructureComplianceCalculator,
)
from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    EvidenceOutput,
)
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.datasets import Annotation


class LogicalPolicyStructureComplianceCalculator(PolicyStructureComplianceCalculator):
    version = "1.1.0"

    def source_paths(self):
        return (
            *super().source_paths(),
            Path(__file__),
            Path(policy_resolved_fields.__file__),
        )

    def validate(self, context, parameters):
        require_resolver(context)
        return super().validate(replace(context, input_schema_ref="2.0"), parameters)

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
        html_role = str(parameters.get("html_role", "article.body.html"))
        plain_role = str(parameters.get("plain_role", "article.body.plain"))
        title_role = str(parameters.get("title_role", "article.title"))
        maximum = int(parameters.get("max_html_length_chars", DEFAULT_MAX_HTML_LENGTH))
        full_scan = parameters.get("long_text_policy", "REJECT") == "FULL_SCAN"
        evidence_limit = int(parameters.get("evidence_sample_limit", 0))
        counts = {flag: Counter({status: 0 for status in STATUSES}) for flag in FLAGS}
        totals = Counter()
        input_count = processed = error_count = evidence_count = 0
        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / "policy-structure-flags.jsonl"
        with evidence_path.open("w", encoding="utf-8") as evidence:
            for resolved in context.resolved_records():
                input_count += 1
                try:
                    annotations = [
                        Annotation.model_validate(item)
                        for item in (field(resolved, "annotations") or [])
                    ]
                    sample = policy_record(
                        resolved,
                        {
                            "document_type": field(resolved, "document_type"),
                            "extra": {"policyType": field(resolved, "policyType")},
                        },
                        roles={
                            "title": title_role,
                            "html": html_role,
                            "plain": plain_role,
                        },
                        annotations=annotations,
                    )
                    row, results, measurements = self._evaluate_sample(
                        sample,
                        html_role=html_role,
                        plain_role=plain_role,
                        title_role=title_role,
                        maximum=maximum,
                        full_scan=full_scan,
                    )
                    row["source_origins"] = origins(resolved)
                    processed += 1
                except Exception as exc:  # noqa: BLE001 - isolate individual records
                    error_count += 1
                    results = {
                        flag: FlagResult("UNAVAILABLE", "SAMPLE_PROCESSING_ERROR")
                        for flag in FLAGS
                    }
                    measurements = Counter()
                    row = {
                        "sample_id": str(resolved.sample_id),
                        "content_id": None,
                        "diagnostics": [
                            {
                                "code": "SAMPLE_PROCESSING_ERROR",
                                "type": type(exc).__name__,
                            }
                        ],
                    }
                for flag, result in results.items():
                    counts[flag][result.status] += 1
                totals.update(measurements)
                row["flags"] = {
                    flag: {
                        "status": result.status,
                        "reason_code": result.reason_code,
                        "violations": result.violations,
                        "diagnostics": result.diagnostics,
                    }
                    for flag, result in results.items()
                }
                if evidence_limit == 0 or evidence_count < evidence_limit:
                    evidence.write(
                        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                    )
                    evidence_count += 1
        if input_count == 0:
            return CalculatorOutput(
                status="MISSING",
                status_detail={"reason_code": "NO_INPUT_RECORDS"},
                observations=[],
                population={
                    "input_count": 0,
                    "evaluated_count": 0,
                    "excluded_count": 0,
                    "exclusions": [],
                },
                semantic_coverage=self._coverage(),
                diagnostics=diagnostics,
            )
        observations = self._observations(counts, totals)
        return CalculatorOutput(
            status="PARTIAL"
            if any(counts[flag]["UNAVAILABLE"] for flag in FLAGS)
            else "OK",
            status_detail={"reason_code": "SOME_FLAGS_UNAVAILABLE"}
            if any(counts[flag]["UNAVAILABLE"] for flag in FLAGS)
            else {},
            observations=observations,
            population={
                "input_count": input_count,
                "evaluated_count": processed,
                "excluded_count": input_count - processed,
                "exclusions": (
                    [{"reason": "sample_processing_error", "count": error_count}]
                    if error_count
                    else []
                ),
            },
            semantic_coverage=self._coverage(),
            evidence=[
                EvidenceOutput(
                    path=evidence_path,
                    kind="POLICY_STRUCTURE_FLAGS",
                    schema_ref="qa-eval://schemas/evidence/policy-structure-flags/1.0.0",
                    record_count=evidence_count,
                    error_count=error_count,
                    contains_raw_content=False,
                    redaction_policy="identifiers-coordinates-and-bounded-diagnostics-no-excerpts",
                    attributes={
                        "input_record_count": input_count,
                        "evidence_capped": evidence_count < input_count,
                        "parser_version": PARSER_VERSION,
                    },
                )
            ],
            diagnostics=[
                *diagnostics,
                {
                    "key": "flag_status_counts",
                    "counts": {key: dict(value) for key, value in counts.items()},
                },
            ],
        )
