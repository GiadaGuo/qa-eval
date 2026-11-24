"""Evaluate explicit relations over field views, independent of the wire format."""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from hashlib import sha256
from typing import Any, Protocol


@dataclass(frozen=True)
class FieldView:
    field_id: str
    value: Any
    value_type: str
    presence: str
    source_ref: dict
    origins: frozenset[str] = frozenset()
    semantic_tag: str | None = None

    def evidence(self):
        summary = None
        if self.presence == "PRESENT":
            encoded = json.dumps(self.value, ensure_ascii=True, sort_keys=True)
            summary = {
                "sha256": sha256(encoded.encode()).hexdigest(),
                "json_characters": len(encoded),
            }
        return {
            "field_id": self.field_id,
            "presence": self.presence,
            "value_type": self.value_type,
            "semantic_tag": self.semantic_tag,
            "source": self.source_ref,
            "value_summary": summary,
        }


@dataclass(frozen=True)
class Match:
    status: str
    reason: str
    raw_score: float | None = None
    score: float | None = None
    confirmed_conflict: bool = False
    details: dict = field(default_factory=dict)


class Matcher(Protocol):
    def __call__(
        self, left: FieldView, right: FieldView, parameters: dict
    ) -> Match: ...


def unknown(reason, **details):
    return Match("UNKNOWN", reason, details=details)


def compare(equal, **details):
    score = float(equal)
    return Match(
        "PASS" if equal else "FAIL",
        "VALUES_AGREE" if equal else "VALUES_DIFFER",
        score,
        score,
        confirmed_conflict=not equal,
        details=details,
    )


def evaluate(fields: dict[str, FieldView], options: dict, matchers: dict[str, Matcher]):
    results = []
    applicable = evaluated = total = Decimal(0)
    required_unknown = veto = False
    seen = set()
    evidence = {}

    for relation in options["relations"]:
        left, right = (fields[relation[side]] for side in ("left", "right"))
        parameters = relation["parameters"]
        condition = parameters.get("applies_when")
        endpoints = frozenset((left.origins, right.origins))
        if condition and fields[condition["field"]].presence != "PRESENT":
            match = unknown("APPLICABILITY_UNKNOWN")
        elif condition and fields[condition["field"]].value != condition["equals"]:
            match = Match("NOT_APPLICABLE", "APPLICABILITY_CONDITION_FALSE")
        elif left.presence != "PRESENT" or right.presence != "PRESENT":
            match = unknown(f"FIELD_{left.presence}_{right.presence}")
        elif left.origins & right.origins:
            match = unknown("SAME_SOURCE")
        elif all(endpoints) and endpoints in seen:
            match = Match("NOT_APPLICABLE", "DUPLICATE_SOURCE_EDGE")
        else:
            if all(endpoints):
                seen.add(endpoints)
            match = matchers[relation["method"]](left, right, parameters)

        weight = Decimal(str(relation["weight"]))
        if match.status != "NOT_APPLICABLE":
            applicable += weight
        if match.status in {"PASS", "FAIL"}:
            evaluated += weight
            total += weight * Decimal(str(match.score))
        elif match.status == "UNKNOWN" and relation["required"]:
            required_unknown = True
        veto |= match.confirmed_conflict and relation.get("veto_on_conflict", False)

        for view in (left, right):
            if view.field_id not in evidence:
                evidence[view.field_id] = view.evidence()
        results.append(
            {
                "id": relation["id"],
                "method": relation["method"],
                "status": match.status,
                "reason": match.reason,
                "raw_score": match.raw_score,
                "score": match.score,
                "confirmed_conflict": match.confirmed_conflict,
                "required": relation["required"],
                "veto": match.confirmed_conflict
                and relation.get("veto_on_conflict", False),
                "weight": relation["weight"],
                "left": left.field_id,
                "right": right.field_id,
                "details": match.details,
            }
        )

    coverage = evaluated / applicable if applicable else None
    score = total / evaluated if evaluated else None
    aggregation = options.get("aggregation", {})
    minimum = Decimal(str(aggregation.get("min_coverage", 0.6)))
    threshold = Decimal(str(aggregation.get("pass_threshold", 0.8)))
    if veto:
        label, reason = "0", "CONFIRMED_CONFLICT_VETO"
    elif not applicable:
        label, reason = "NOT_EVALUATED", "NO_APPLICABLE_RELATION"
    elif required_unknown:
        label, reason = "NOT_EVALUATED", "REQUIRED_RELATION_UNKNOWN"
    elif score is None:
        label, reason = "NOT_EVALUATED", "NO_EVALUABLE_RELATION"
    elif coverage < minimum:
        label, reason = "NOT_EVALUATED", "COVERAGE_BELOW_MINIMUM"
    elif score >= threshold:
        label, reason = "1", "SCORE_AT_THRESHOLD_OR_ABOVE"
    else:
        label, reason = "0", "SCORE_BELOW_THRESHOLD"
    return {
        "label": label,
        "reason": reason,
        "coverage": float(coverage) if coverage is not None else None,
        "score": float(score) if score is not None else None,
        "relations": results,
        "fields": evidence,
    }
