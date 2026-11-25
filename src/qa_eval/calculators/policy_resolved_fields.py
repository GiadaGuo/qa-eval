"""Resolved policy fields in the shape consumed by the frozen rule functions."""

from dataclasses import asdict, dataclass

from qa_eval.calculators.protocol import CalculatorValidationError
from qa_eval.contracts.datasets import Annotation, ContentItem, InlineTextPayload

TEXT_FIELDS = {
    "title": ("article.title", "plain"),
    "html": ("article.body.html", "html"),
    "plain": ("article.body.plain", "plain"),
}


def require_resolver(context):
    if context.input_schema_ref != "3.0" or context.resolved_records is None:
        raise CalculatorValidationError("calculator requires a v3 Resolver")


def field(record, key):
    value = record.values[key]
    if value.status in {"PRESENT", "EMPTY"}:
        return value.value
    if value.status not in {"UNBOUND", "MISSING", "NULL"}:
        raise CalculatorValidationError(
            "Unable to resolve policy field", slot=key, reason=value.status
        )
    return None


def origins(record):
    return {
        key: asdict(record.values[key].origin)
        for key in TEXT_FIELDS
        if record.values[key].origin is not None
    }


@dataclass
class PolicyRecord:
    sample_id: str
    contents: list[ContentItem]
    metadata: dict
    annotations: list[Annotation]
    source: object = None


def policy_record(
    record, metadata, *, roles=None, annotations=(), html_attributes=None
):
    contents = []
    for key, (role, format_name) in TEXT_FIELDS.items():
        value = field(record, key)
        if value is None:
            continue
        contents.append(
            ContentItem(
                content_id=key,
                role=roles[key] if roles else role,
                modality="text",
                presence="PRESENT",
                payload=InlineTextPayload(
                    type="inline_text", format=format_name, text=value
                ),
                attributes=(html_attributes or {}) if key == "html" else {},
            )
        )
    return PolicyRecord(str(record.sample_id), contents, metadata, list(annotations))
