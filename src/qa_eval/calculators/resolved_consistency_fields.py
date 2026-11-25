"""Translate frozen selectors into the field views used by 0207 and 0208."""

from dataclasses import asdict
from decimal import Decimal

from qa_eval.calculators.field_relation_consistency.engine import FieldView
from qa_eval.calculators.field_relation_consistency.legacy import (
    MISSING,
    value_presence,
)


def field_specs(key, parameters):
    fields = parameters["fields"]
    if key == "field_relation_consistency":
        return fields
    return {
        str(index): {"value_type": "text"} | spec for index, spec in enumerate(fields)
    }


def _json_value(value):
    # The raw reader uses Decimal; the legacy rules and JSON evidence use float.
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


def field_views(record, specs, dataset_version_id):
    views = {}
    for name, spec in specs.items():
        resolved = record.values[name]
        value = _json_value(resolved.value)
        presence = (
            value_presence(MISSING if resolved.status == "MISSING" else value, spec)
            if resolved.status in {"PRESENT", "EMPTY", "NULL", "MISSING"}
            else "INVALID"
        )
        ref = {
            "sample_id": str(record.sample_id),
            "dataset_version_id": str(dataset_version_id),
            "selector": "input_selector",
            "source_paths": sorted(spec.get("source_paths", [])),
        }
        origins = set(ref["source_paths"])
        if origin := resolved.origin:
            path = "/" + "/".join(
                part.replace("~", "~0").replace("/", "~1")
                for part in origin.field_tokens
            )
            origins.add("record:" + path)
            ref.update(path=path, source_origin=asdict(origin))
            if isinstance(value, str):
                ref["coordinate_space"] = (
                    "resolved-text-unicode-codepoints"
                    if origin.transform
                    else "source-field-unicode-codepoints"
                )
        views[name] = FieldView(
            name,
            value,
            spec["value_type"],
            presence,
            ref,
            frozenset(origins),
            spec.get("semantic_tag"),
        )
    return views
