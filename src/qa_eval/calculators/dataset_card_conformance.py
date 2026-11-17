"""BG-01 dataset-card structural conformance over immutable ProfileSnapshot 1.0."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.snapshots import ProfileSnapshotV1
from qa_eval.paths import PROJECT_ROOT

_RULE_REF = re.compile(r"^[a-z0-9][a-z0-9-]*@[0-9]+\.[0-9]+\.[0-9]+$")
_ELEMENTS = (
    "basic_information",
    "content_characteristics",
    "construction_process",
    "application_description",
)
_UNEVALUATED = {"NOT_EVALUATED", "NOT_APPLICABLE"}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _resolve(document: JsonObject, field_path: str) -> tuple[bool, Any]:
    if not field_path.startswith("$."):
        return False, None
    current: Any = document
    for segment in field_path[2:].split("."):
        if not isinstance(current, dict) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _is_placeholder(value: Any, placeholder_rules: list[JsonObject]) -> bool:
    for rule in placeholder_rules:
        parameters = rule.get("parameters", {})
        candidate = (
            value.strip()
            if parameters.get("trim_strings") and isinstance(value, str)
            else value
        )
        if candidate in parameters.get("values", []):
            return True
    return False


class DatasetCardConformanceCalculator:
    calculator_key = "dataset_card_conformance"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        return (Path(__file__),)

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        snapshot_path = context.input_artifacts.get("PROFILE_SNAPSHOT")
        if snapshot_path is None or not snapshot_path.is_file():
            raise CalculatorValidationError("PROFILE_SNAPSHOT input is unavailable")
        rule_ref = parameters.get("rule_set_ref", "bg01-card-rules@1.0.0")
        if not isinstance(rule_ref, str) or not _RULE_REF.fullmatch(rule_ref):
            raise CalculatorValidationError("rule_set_ref must use key@semver")
        self._load_snapshot(snapshot_path)
        self._load_rule_set(rule_ref)
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
        snapshot = self._load_snapshot(context.input_artifacts["PROFILE_SNAPSHOT"])
        rule_ref = str(parameters.get("rule_set_ref", "bg01-card-rules@1.0.0"))
        rule_set = self._load_rule_set(rule_ref)
        document = (snapshot.card or {}).get("document")
        declarations = document if isinstance(document, dict) else {}
        rules = rule_set["rules"]
        placeholder_rules = [
            rule
            for rule in rules
            if rule["kind"] == "PLACEHOLDER"
            and rule["enabled"]
            and rule["approval_status"] == "APPROVED"
        ]

        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / "bg01-card-checks.jsonl"
        rows: list[JsonObject] = []
        element_complete = 0
        for element in _ELEMENTS:
            value = declarations.get(element)
            complete = isinstance(value, dict) and bool(value)
            if complete:
                element_complete += 1
            rows.append(
                self._row(
                    comparison_id=f"element.{element}",
                    field_path=f"$.{element}",
                    outcome="MATCH" if complete else "MISSING",
                    rule_id=f"element.{element}",
                    rule_version=rule_set["version"],
                    declared=value,
                    reason_code=None if complete else "DECLARATION_MISSING",
                )
            )

        required_matches = required_evaluated = 0
        government_matches = government_evaluated = 0
        covered = list(_ELEMENTS)
        excluded: list[str] = []
        limitations: set[str] = set()
        for rule in rules:
            if rule["kind"] not in {"FIELD_REQUIREMENT", "ENUM_REFERENCE"}:
                continue
            if not rule["enabled"] or rule["approval_status"] != "APPROVED":
                reason = (
                    "RULE_NOT_APPROVED"
                    if rule["approval_status"] != "APPROVED"
                    else "RULE_NOT_CONFIGURED"
                )
                rows.append(
                    self._row(
                        comparison_id=rule["rule_id"],
                        field_path=rule.get("field_path"),
                        outcome="NOT_EVALUATED",
                        rule_id=rule["rule_id"],
                        rule_version=rule_set["version"],
                        declared=None,
                        reason_code=reason,
                    )
                )
                excluded.append(rule["rule_id"])
                limitations.add(reason)
                continue

            if rule["kind"] == "FIELD_REQUIREMENT":
                level = rule.get("requirement_level")
                if level == "OPTIONAL":
                    rows.append(
                        self._row(
                            comparison_id=rule["rule_id"],
                            field_path=rule.get("field_path"),
                            outcome="NOT_APPLICABLE",
                            rule_id=rule["rule_id"],
                            rule_version=rule_set["version"],
                            declared=None,
                            reason_code="RULE_NOT_CONFIGURED",
                        )
                    )
                    excluded.append(rule["rule_id"])
                    continue
                if level == "CONDITIONAL" and not self._condition_applies(
                    declarations, rule
                ):
                    rows.append(
                        self._row(
                            comparison_id=rule["rule_id"],
                            field_path=rule.get("field_path"),
                            outcome="NOT_APPLICABLE",
                            rule_id=rule["rule_id"],
                            rule_version=rule_set["version"],
                            declared=None,
                            reason_code="RULE_NOT_CONFIGURED",
                        )
                    )
                    excluded.append(rule["rule_id"])
                    continue
                found, value = _resolve(declarations, rule["field_path"])
                placeholder = found and _is_placeholder(value, placeholder_rules)
                outcome = (
                    "MATCH"
                    if found and not placeholder
                    else "PLACEHOLDER"
                    if placeholder
                    else "MISSING"
                )
                required_evaluated += 1
                if outcome == "MATCH":
                    required_matches += 1
                rows.append(
                    self._row(
                        comparison_id=rule["rule_id"],
                        field_path=rule["field_path"],
                        outcome=outcome,
                        rule_id=rule["rule_id"],
                        rule_version=rule_set["version"],
                        declared=value if found else None,
                        reason_code=None
                        if outcome == "MATCH"
                        else "DECLARATION_MISSING",
                    )
                )
                covered.append(rule["rule_id"])
                continue

            field_path = rule.get("field_path")
            allowed = rule.get("parameters", {}).get("values")
            if not field_path or not isinstance(allowed, list) or not allowed:
                rows.append(
                    self._row(
                        comparison_id=rule["rule_id"],
                        field_path=field_path,
                        outcome="NOT_EVALUATED",
                        rule_id=rule["rule_id"],
                        rule_version=rule_set["version"],
                        declared=None,
                        reason_code="RULE_NOT_CONFIGURED",
                    )
                )
                excluded.append(rule["rule_id"])
                limitations.add("RULE_NOT_CONFIGURED")
                continue
            found, value = _resolve(declarations, field_path)
            outcome = (
                "MATCH"
                if found and value in allowed
                else "INVALID"
                if found
                else "MISSING"
            )
            government_evaluated += 1
            if outcome == "MATCH":
                government_matches += 1
            rows.append(
                self._row(
                    comparison_id=rule["rule_id"],
                    field_path=field_path,
                    outcome=outcome,
                    rule_id=rule["rule_id"],
                    rule_version=rule_set["version"],
                    declared=value if found else None,
                    reason_code=None if outcome == "MATCH" else "DECLARATION_MISSING",
                )
            )
            covered.append(rule["rule_id"])

        with evidence_path.open("w", encoding="utf-8") as evidence_file:
            for index, row in enumerate(rows, 1):
                evidence_file.write(
                    json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                )
                if context.progress is not None and index % 20 == 0:
                    context.progress(index, {"evidence_rows": index})

        observations: list[JsonObject] = [
            {
                "key": "four_element_completeness_ratio",
                "value": element_complete / len(_ELEMENTS),
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": element_complete,
                "denominator": len(_ELEMENTS),
            }
        ]
        if required_evaluated:
            observations.append(
                {
                    "key": "required_field_completeness_ratio",
                    "value": required_matches / required_evaluated,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": required_matches,
                    "denominator": required_evaluated,
                }
            )
        if government_evaluated:
            observations.append(
                {
                    "key": "government_field_valid_ratio",
                    "value": government_matches / government_evaluated,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": government_matches,
                    "denominator": government_evaluated,
                }
            )
        excluded_count = sum(row["outcome"] in _UNEVALUATED for row in rows)
        status = "PARTIAL" if excluded_count else "OK"
        return CalculatorOutput(
            status=status,
            status_detail={
                "rule_set": {
                    "key": rule_set["rule_set_key"],
                    "version": rule_set["version"],
                },
                "not_evaluated_reasons": sorted(limitations),
            },
            observations=observations,
            population={
                "input_count": len(rows),
                "evaluated_count": len(rows) - excluded_count,
                "excluded_count": excluded_count,
                "exclusions": [
                    {"reason": reason, "count": count}
                    for reason, count in sorted(
                        Counter(
                            row["reason_code"]
                            for row in rows
                            if row["outcome"] in _UNEVALUATED
                        ).items()
                    )
                ],
            },
            semantic_coverage={
                "level": "PARTIAL" if excluded_count else "FULL",
                "covered": covered,
                "excluded": excluded,
                "limitations": sorted(limitations),
            },
            evidence=[
                EvidenceOutput(
                    path=evidence_path,
                    kind="BG01_CARD_CHECKS",
                    schema_ref="qa-eval://schemas/evidence/bg01-card-checks/1.0.0",
                    record_count=len(rows),
                    subject_scope="DATASET",
                    attributes={
                        "completeness_status": "COMPLETE",
                        "detected_count": len(rows),
                        "emitted_count": len(rows),
                        "truncated": False,
                    },
                )
            ],
            diagnostics=diagnostics,
        )

    @staticmethod
    def _condition_applies(document: JsonObject, rule: JsonObject) -> bool:
        condition = rule.get("parameters", {}).get("condition")
        if not isinstance(condition, dict):
            return False
        found, value = _resolve(document, str(condition.get("field_path", "")))
        operator = condition.get("operator")
        if operator == "PRESENT":
            return found and value not in (None, "")
        if operator == "EQUALS":
            return found and value == condition.get("value")
        return False

    @staticmethod
    def _row(
        *,
        comparison_id: str,
        field_path: str | None,
        outcome: str,
        rule_id: str,
        rule_version: str,
        declared: Any,
        reason_code: str | None,
    ) -> JsonObject:
        return {
            "comparison_id": comparison_id,
            "scope": "FIELD",
            "field_path": field_path,
            "outcome": outcome,
            "rule_id": rule_id,
            "rule_version": rule_version,
            "declared_value_hash": None
            if declared is None
            else _canonical_hash(declared),
            "observed_value_hash": None,
            "reason_code": reason_code,
            "actual_redacted": None if declared is None else type(declared).__name__,
        }

    @staticmethod
    def _load_snapshot(path: Path) -> ProfileSnapshotV1:
        try:
            return ProfileSnapshotV1.model_validate_json(path.read_bytes())
        except (OSError, ValidationError, ValueError) as exc:
            raise CalculatorValidationError("invalid ProfileSnapshot input") from exc

    @staticmethod
    def _load_rule_set(reference: str) -> JsonObject:
        key, version = reference.split("@", 1)
        path = PROJECT_ROOT / "configs" / "card-rules" / f"{key}-{version}.json"
        schema_path = (
            PROJECT_ROOT / "schemas" / "qa-eval" / "card-rule-set" / "1.0.schema.json"
        )
        if not path.is_file():
            raise CalculatorValidationError(
                "configured RuleSet is unavailable", rule_set_ref=reference
            )
        try:
            rule_set = json.loads(path.read_text(encoding="utf-8"))
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft202012Validator(schema).validate(rule_set)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise CalculatorValidationError(
                "configured RuleSet is invalid", rule_set_ref=reference
            ) from exc
        if rule_set["rule_set_key"] != key or rule_set["version"] != version:
            raise CalculatorValidationError(
                "RuleSet identity does not match rule_set_ref"
            )
        return rule_set
