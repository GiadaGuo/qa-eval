"""Resolve selectors against published SampleRecord v2 JSONL rows."""

import math
import re
from collections import defaultdict

from qa_eval.calculators.field_relation_consistency.engine import FieldView
from qa_eval.contracts.datasets import InlineTextPayload, StructuredPayload

MISSING = object()
POINTER_ROOTS = {"metadata", "annotations", "source", "contents"}
POINTER_RE = re.compile(r"(?:/[^/~]*(?:~[01][^/~]*)*)+")


def pointer_parts(path):
    if not POINTER_RE.fullmatch(path):
        raise ValueError("invalid JSON Pointer")
    parts = tuple(
        part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")
    )
    if len(parts) < 2 or parts[0] not in POINTER_ROOTS:
        raise ValueError(
            "JSON Pointer must address a field in a supported record section"
        )
    return parts


def read_pointer(root, parts):
    for part in parts:
        if isinstance(root, dict):
            root = root.get(part, MISSING)
        elif isinstance(root, list) and re.fullmatch(r"0|[1-9][0-9]{0,8}", part):
            index = int(part)
            root = root[index] if index < len(root) else MISSING
        else:
            return MISSING
    return root


def value_presence(value, spec):
    if value is MISSING:
        return "MISSING"
    if value is None:
        return "NULL"
    if isinstance(value, (str, list, dict)) and not (
        value.strip() if isinstance(value, str) else value
    ):
        return "EMPTY"
    if isinstance(value, str) and value.strip() in spec.get("missing_tokens", []):
        return "PLACEHOLDER"
    valid = {
        "text": isinstance(value, str),
        "string": isinstance(value, str),
        "datetime": isinstance(value, str),
        "number": type(value) is int or (type(value) is float and math.isfinite(value)),
        "boolean": type(value) is bool,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
    }
    return "PRESENT" if valid[spec["value_type"]] else "INVALID"


class LegacyAdapter:
    def __init__(self, fields):
        self.fields = fields
        self.pointers = {
            name: pointer_parts(spec["path"])
            for name, spec in fields.items()
            if spec["selector"] == "record_pointer"
        }
        self.roots = {parts[0] for parts in self.pointers.values()}

    def extract(self, record):
        by_role = defaultdict(list)
        for index, content in enumerate(record.contents):
            by_role[content.role].append((index, content))
        roots = {}
        for root in self.roots:
            item = getattr(record, root)
            if root in {"contents", "annotations"}:
                item = [
                    element.model_dump(mode="json", exclude_unset=True)
                    for element in item
                ]
            elif root == "source":
                item = item.model_dump(mode="json", exclude_unset=True)
            roots[root] = item
        base_ref = {
            "dataset_version_id": str(record.dataset_version_id),
            "sample_id": record.sample_id,
            "source_artifact_id": str(record.source.source_artifact_id),
            "source_row": record.source.source_row,
            "source_record_id": record.source.source_record_id,
            "source_fingerprint": record.lineage.input_fingerprint,
        }
        return {
            name: self._field(name, spec, record, by_role, roots, base_ref)
            for name, spec in self.fields.items()
        }

    def _field(self, name, spec, record, by_role, roots, base_ref):
        ref = base_ref | {"selector": spec["selector"]}
        origins = set(spec.get("source_paths", []))
        presence = None
        if spec["selector"] == "content_role":
            candidates = by_role[spec["role"]]
            ref["role"] = spec["role"]
            if "content_id" in spec:
                ref["content_id"] = spec["content_id"]
                candidates = [
                    pair
                    for pair in candidates
                    if pair[1].content_id == spec["content_id"]
                ]
            value = MISSING
            if len(candidates) > 1:
                presence = "AMBIGUOUS"
                ref["candidate_content_ids"] = [
                    item.content_id for _, item in candidates
                ]
            elif candidates:
                index, item = candidates[0]
                path = f"/contents/{index}/payload"
                ref.update(content_id=item.content_id, path=path)
                self._content_sources(item, origins, ref)
                if item.presence != "PRESENT":
                    presence = item.presence
                elif isinstance(item.payload, InlineTextPayload):
                    ref["path"] += "/text"
                    value = item.payload.text
                elif isinstance(item.payload, StructuredPayload):
                    ref["path"] += "/data"
                    value = item.payload.data
                else:
                    presence = "INVALID"
                origins.add("record:" + ref["path"])
        else:
            parts = self.pointers[name]
            ref["path"] = spec["path"]
            value = read_pointer(roots, parts)
            origins.add("record:" + spec["path"])
            if parts[0] == "contents" and re.fullmatch(r"0|[1-9][0-9]{0,8}", parts[1]):
                index = int(parts[1])
                if index < len(record.contents):
                    item = record.contents[index]
                    ref["content_id"] = item.content_id
                    if (
                        isinstance(item.payload, StructuredPayload)
                        and parts[2:4] == ("payload", "data")
                        and len(parts) > 4
                    ):
                        source_origins = set()
                        self._content_sources(item, source_origins, ref)
                        suffix = spec["path"].split("/payload/data", 1)[1]
                        origins.update(path + "#" + suffix for path in source_origins)
                        ref["source_parent_paths"] = sorted(source_origins)
                    else:
                        self._content_sources(item, origins, ref)
            if parts[0] == "metadata" and value is None:
                ref["upstream_null_may_include_missing"] = True
        ref["source_paths"] = sorted(
            path for path in origins if not path.startswith("record:")
        )
        return FieldView(
            name,
            None if value is MISSING else value,
            spec["value_type"],
            presence or value_presence(value, spec),
            ref,
            frozenset(origins),
            spec.get("semantic_tag"),
        )

    @staticmethod
    def _content_sources(item, origins, ref):
        paths = item.attributes.get("source_paths")
        if isinstance(paths, list):
            origins.update(path for path in paths if isinstance(path, str))
        for key in ("transform", "transform_version"):
            if key in item.attributes:
                ref[key] = item.attributes[key]
        ref["coordinate_space"] = "normalized-content-unicode-codepoints"
