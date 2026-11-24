"""Validate graph semantics and versioned matcher resources before streaming."""

import json
import math
from copy import deepcopy
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jsonschema import Draft202012Validator

from qa_eval.calculators.field_relation_consistency.legacy import pointer_parts
from qa_eval.calculators.field_relation_consistency.local_matching import (
    resource_digest,
)
from qa_eval.calculators.field_relation_consistency.structured import normalized_text
from qa_eval.calculators.protocol import CalculatorValidationError

SCHEMA_PATH = Path(__file__).with_name("parameters.schema.json")
VALIDATOR = Draft202012Validator(json.loads(SCHEMA_PATH.read_bytes()))
TEXT = {"text", "string"}


def field_identity(spec):
    if spec["selector"] == "record_pointer":
        return ("record_pointer", pointer_parts(spec["path"]))
    return ("content_role", spec["role"], spec.get("content_id"))


def validate_resource(params, name, fingerprints):
    resource = params[name]
    version = resource["version"]
    digest = resource_digest(resource)
    key = (name, version)
    if fingerprints.setdefault(key, digest) != digest:
        raise ValueError(f"resource {name}/{version} is defined multiple ways")
    params[f"_{name}_digest"] = digest


def validate_matcher(method, left, right, relation, fingerprints):
    params = relation["parameters"]
    kinds = {left["value_type"], right["value_type"]}
    if relation.get("veto_on_conflict") and method in {"semantic_match", "type_match"}:
        raise ValueError("statistical or rule-based category signals cannot veto")
    if method in {"semantic_match", "type_match"} and not kinds <= TEXT:
        raise ValueError(f"{method} requires strings")
    if method == "exact_match":
        if not kinds <= TEXT and (
            len(kinds) != 1 or not kinds <= {"number", "boolean"}
        ):
            raise ValueError("exact_match requires compatible scalar values")
        if "normalizer" in params and not kinds <= TEXT:
            raise ValueError("text normalizer requires strings")
    if method == "structured_match":
        attribute = params["attribute"]
        allowed = {
            "document_number": TEXT,
            "date": TEXT | {"datetime"},
            "number": TEXT | {"number"},
        }[attribute]
        if not kinds <= allowed:
            raise ValueError(
                f"structured_match {attribute} has incompatible field types"
            )
        for key in ("timezone", "left_naive_timezone", "right_naive_timezone"):
            if key in params:
                ZoneInfo(params[key])
        if attribute == "number":
            units = params.get("unit_factors", {"1": 1})
            for key in ("left_unit", "right_unit"):
                if key in params and params[key] not in units:
                    raise ValueError("default unit must be present in unit_factors")
    if method in {"semantic_match", "type_match"}:
        thresholds = (params["fail_below"], params["pass_at"])
        threshold_key = ("thresholds", params["threshold_version"])
        if fingerprints.setdefault(threshold_key, thresholds) != thresholds:
            raise ValueError("threshold version has conflicting definitions")
        if not params["fail_below"] < params["pass_at"]:
            raise ValueError("fail_below must be smaller than pass_at")
        if "calibration" in params:
            points = params["calibration"]["points"]
            if (
                points[0][0] != 0
                or points[-1][0] != 1
                or any(x2 <= x1 or y2 < y1 for (x1, y1), (x2, y2) in pairwise(points))
            ):
                raise ValueError(
                    "calibration points must span [0,1], with increasing inputs and monotonic outputs"
                )
            validate_resource(params, "calibration", fingerprints)
        if method == "semantic_match":
            validate_resource(params, "tfidf", fingerprints)
        else:
            d = params["dictionary"]
            if not set(d["verified_labels"].values()) <= d["categories"].keys():
                raise ValueError("verified label maps to an undefined category")
            if any(label != normalized_text(label) for label in d["verified_labels"]):
                raise ValueError("category labels must use normalized keys")
            validate_resource(params, "dictionary", fingerprints)


def settings(parameters):
    issue = next(VALIDATOR.iter_errors(parameters), None)
    if issue is not None:
        raise CalculatorValidationError(
            "invalid field relation parameters",
            path=list(issue.absolute_path),
            detail=issue.message,
        )
    # JSON Schema's number type also accepts Python NaN in-process.
    try:
        json.dumps(parameters, allow_nan=False)
        options = deepcopy(parameters)
        fields = options["fields"]
        identities = {name: field_identity(spec) for name, spec in fields.items()}
        ids, edges, fingerprints = set(), set(), {}
        for relation in options["relations"]:
            identifier = relation["id"]
            left, right = relation["left"], relation["right"]
            if identifier in ids:
                raise ValueError(f"duplicate relation id: {identifier}")
            ids.add(identifier)
            if left not in fields or right not in fields:
                raise ValueError(f"undefined endpoint: {identifier}")
            condition = relation["parameters"].get("applies_when")
            if condition and condition["field"] not in fields:
                raise ValueError(f"undefined applicability field: {identifier}")
            edge = frozenset((identities[left], identities[right]))
            if len(edge) != 2 or edge in edges:
                raise ValueError(f"duplicate or self relation: {identifier}")
            edges.add(edge)
            if set(fields[left].get("source_paths", [])) & set(
                fields[right].get("source_paths", [])
            ):
                raise ValueError(f"same-source fields: {identifier}")
            if not math.isfinite(relation["weight"]):
                raise ValueError(f"invalid weight: {identifier}")
            validate_matcher(
                relation["method"], fields[left], fields[right], relation, fingerprints
            )
        return options
    except (ValueError, ZoneInfoNotFoundError, OverflowError) as exc:
        raise CalculatorValidationError(str(exc)) from exc
