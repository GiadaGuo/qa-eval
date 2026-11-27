"""Native 0207 over frozen v3 selections with optional offline embeddings."""

import json
import os
import sqlite3
from collections import Counter
from contextlib import ExitStack
from copy import deepcopy
from functools import lru_cache
from itertools import islice
from pathlib import Path

from qa_eval.calculators.content_consistency import protocol_support, recall
from qa_eval.calculators.field_relation_consistency import embedding, engine
from qa_eval.calculators.field_relation_consistency.calculator import (
    DECISIONS,
    EVIDENCE_KIND,
    MATCHERS,
    statistics,
)
from qa_eval.calculators.field_relation_consistency.local_matching import (
    resource_digest,
)
from qa_eval.calculators.field_relation_consistency_v3 import (
    LogicalFieldRelationConsistencyCalculator,
    resolved_settings,
)
from qa_eval.calculators.protocol import (
    CalculatorValidationError,
    EvidenceOutput,
    NativeCalculatorOutput,
)
from qa_eval.calculators.resolved_consistency_fields import field_views
from qa_eval.contracts.calculator_results import (
    DECISION_SCHEMA_REF,
    CalculatorDecisionV1,
)

VERSION = "2.2.0"
EMBEDDING_DEFAULTS = {
    "model_id": embedding.MODEL_ID,
    "chunk_chars": 320,
    "max_chunks": 64,
    "aggregation": "max",
}
SCORE_METHODS = {"embedding_match", "semantic_match", "type_match"}
RESOURCE_CONFIG = (
    Path(__file__).resolve().parents[3]
    / "configs/field_relation_consistency/policy.json"
)
RECORD_WINDOW = 8
VECTOR_WINDOW = 512


def tfidf_resources():
    try:
        config = resolved_settings(json.loads(RESOURCE_CONFIG.read_bytes()))
        return {
            relation["parameters"]["tfidf"]["version"]: relation["parameters"]["tfidf"]
            for relation in config["relations"]
            if relation["method"] == "semantic_match"
        }
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def resource_options():
    try:
        _, identity = embedding.model_configuration()
        model = {
            "id": embedding.MODEL_ID,
            "label": "平台本地 BGE",
            "available": True,
            "identity": identity,
        }
    except CalculatorValidationError as exc:
        model = {
            "id": embedding.MODEL_ID,
            "label": "平台本地 BGE",
            "available": False,
            "reason": str(exc),
        }
    return {
        "models": [model],
        "tfidf": [
            {"id": key, "digest": resource_digest(value)}
            for key, value in sorted(tfidf_resources().items())
        ],
    }


def freeze_parameters(parameters):
    """Freeze server-owned resources once when accepting a new configuration."""
    options = deepcopy(parameters)
    model_identity = None
    resources = None
    for relation in options["relations"]:
        method, params = relation["method"], relation["parameters"]
        if method == "embedding_match":
            if model_identity is None:
                _, model_identity = embedding.model_configuration()
            if params.get("model_identity", model_identity) != model_identity:
                raise CalculatorValidationError("平台 BGE 身份已变更，请重新配置")
            params = EMBEDDING_DEFAULTS | params | {"model_identity": model_identity}
        elif method == "semantic_match":
            if resources is None:
                resources = tfidf_resources()
            if (resource := resources.get(params.get("resource_id"))) is None:
                raise CalculatorValidationError("找不到所选的 TF-IDF 平台资源")
            if "tfidf" in params and params["tfidf"] != resource:
                raise CalculatorValidationError("TF-IDF 平台资源已变更，请重新配置")
            params = params | {"tfidf": resource, "backend": "tfidf"}
        if method in SCORE_METHODS:
            params["threshold_version"] = (
                "trial-v1-"
                + resource_digest(
                    {
                        key: value
                        for key, value in params.items()
                        if key != "threshold_version"
                    }
                ).split(":")[1]
            )
        relation["parameters"] = params
    settings(options)
    return options


def settings(parameters):
    """Validate graph rules with the historical validator and restore new matchers."""
    projected = deepcopy(parameters)
    new_matchers = {}
    for relation in projected["relations"]:
        method = relation["method"]
        params = relation["parameters"]
        if method in SCORE_METHODS and not params["fail_below"] < params["pass_at"]:
            raise CalculatorValidationError("不一致阈值必须低于一致阈值")
        if method == "semantic_match":
            params.pop("resource_id", None)
        if method != "embedding_match":
            continue
        if relation.get("veto_on_conflict"):
            raise CalculatorValidationError("Embedding 相似度不能触发一票否决")
        if any(
            projected["fields"].get(relation[side], {}).get("value_type")
            not in {"text", "string"}
            for side in ("left", "right")
        ):
            raise CalculatorValidationError("Embedding 匹配需要两个文本字段")
        if not params.get("model_identity") or not params.get("threshold_version"):
            raise CalculatorValidationError("Embedding 配置缺少冻结的模型或阈值身份")
        new_matchers[relation["id"]] = params
        relation["method"] = "exact_match"
        relation["parameters"] = (
            {"applies_when": params["applies_when"]} if "applies_when" in params else {}
        )
    validated = resolved_settings(projected)
    for relation in validated["relations"]:
        if relation["id"] in new_matchers:
            relation["method"] = "embedding_match"
            relation["parameters"] = new_matchers[relation["id"]]
    return validated


def eligible(left, right, fields, parameters):
    if (
        left.presence != "PRESENT"
        or right.presence != "PRESENT"
        or left.origins & right.origins
    ):
        return False
    if condition := parameters.get("applies_when"):
        field = fields[condition["field"]]
        return field.presence == "PRESENT" and field.value == condition["equals"]
    return True


def decision_locations(result, embedding_parameters):
    locations = []
    origins = {}
    for edge in result["relations"]:
        params = embedding_parameters.get(edge["id"])
        if params is None:
            continue
        details = edge["details"]
        for key in ("threshold_version", "fail_below", "pass_at", "model_identity"):
            details.setdefault(key, params[key])
        for side in ("left", "right"):
            alias = edge[side]
            origin = result["fields"][alias]["source"].get("source_origin")
            if origin:
                origins[alias] = origin
            fragment = details.get(f"{side}_fragment")
            if not origin or origin["transform"] or not fragment:
                continue
            start, end = fragment["excerpt_span"]
            locations.append(
                {
                    "content_id": alias,
                    "coordinate_space": "source_unicode_codepoint",
                    "start": start,
                    "end": end,
                    "attributes": {"relation_id": edge["id"], "side": side},
                }
            )
    if origins:
        result["source_origins"] = origins
    return locations


def vector_cache(stack, configured, destination):
    try:
        return stack.enter_context(recall.open_cache(configured))
    except sqlite3.Error:
        return stack.enter_context(recall.open_cache(destination))


class EmbeddingFieldRelationConsistencyCalculator(
    LogicalFieldRelationConsistencyCalculator
):
    version = VERSION

    def source_paths(self):
        return (
            *super().source_paths(),
            *(
                Path(module.__file__)
                for module in (embedding, recall, protocol_support)
            ),
            Path(__file__),
        )

    def validate(self, context, parameters):
        settings(parameters)
        return []

    def execute(self, context, parameters):
        options = settings(parameters)
        if context.input_schema_ref != "3.0" or context.resolved_records is None:
            raise CalculatorValidationError("field relations require a v3 Resolver")
        context.output_dir.mkdir(parents=True, exist_ok=True)
        destination = context.output_dir / DECISIONS
        relations = [
            relation
            for relation in options["relations"]
            if relation["method"] == "embedding_match"
        ]
        embedding_parameters = {
            relation["id"]: relation["parameters"] for relation in relations
        }
        labels, exclusions, reasons, outcomes = (
            Counter(),
            Counter(),
            Counter(),
            Counter(),
        )
        conflicts = 0
        encoder = None
        with ExitStack() as stack:
            cache = None
            if relations:
                local_cache = context.output_dir / "embedding-cache.sqlite"
                cache = vector_cache(
                    stack,
                    os.getenv("QA_EVAL_0206_EMBEDDING_CACHE") or local_cache,
                    local_cache,
                )
            output = stack.enter_context(destination.open("w", encoding="utf-8"))
            if relations:
                with embedding.MODEL_ACCESS:
                    try:
                        path, expected = embedding.model_configuration()
                        if all(
                            relation["parameters"]["model_identity"] == expected
                            for relation in relations
                        ):
                            encoder = embedding.Encoder(
                                "bge", path, cache, embedding.BATCH_SIZE
                            )
                            if encoder.identity != expected:
                                encoder = None
                    except Exception:  # noqa: BLE001 - isolate the offline provider
                        # Availability may have changed since publication.
                        encoder = None
            records = iter(context.resolved_records())
            while window := list(islice(records, RECORD_WINDOW)):
                rows = [
                    (
                        str(record.sample_id),
                        field_views(
                            record, options["fields"], context.dataset_version_id
                        ),
                    )
                    for record in window
                ]
                with embedding.MODEL_ACCESS:
                    matches = self._encode(rows, relations, encoder) if encoder else {}
                for sample_id, fields in rows:

                    def match(
                        left, right, params, sample_id=sample_id, matches=matches
                    ):
                        key = (sample_id, left.field_id, right.field_id)
                        if key in matches:
                            return matches[key]
                        return engine.unknown(
                            "EMBEDDING_MODEL_UNAVAILABLE",
                            model_identity=params["model_identity"],
                            threshold_version=params["threshold_version"],
                            fail_below=params["fail_below"],
                            pass_at=params["pass_at"],
                        )

                    result = engine.evaluate(
                        fields, options, MATCHERS | {"embedding_match": match}
                    )
                    label, reason = result.pop("label"), result.pop("reason")
                    labels[label] += 1
                    if label == "NOT_EVALUATED":
                        exclusions[reason] += 1
                    for relation in result["relations"]:
                        outcomes[f"{relation['id']}/{relation['status']}"] += 1
                        if relation["status"] == "UNKNOWN":
                            reasons[f"{relation['id']}/{relation['reason']}"] += 1
                        conflicts += relation["confirmed_conflict"]
                    decision = CalculatorDecisionV1(
                        row_id=sample_id,
                        sample_id=sample_id,
                        rule_id="field_relation_consistency_v2",
                        status=label,
                        target="sample",
                        reason=reason,
                        locations=decision_locations(result, embedding_parameters),
                        evidence_refs=[
                            {"artifact_key": DECISIONS, "row_id": sample_id}
                        ],
                        attributes=result,
                    )
                    output.write(decision.model_dump_json() + "\n")
                count = sum(labels.values())
                if context.progress and (count % 1000 < RECORD_WINDOW):
                    context.progress(count, {})
        values = statistics(labels, exclusions, reasons, outcomes, conflicts)
        count = values["input_count"]
        status = "OK" if values["evaluable_record_count"] else "MISSING"
        if count == exclusions["NO_APPLICABLE_RELATION"]:
            status = "NOT_APPLICABLE"
        return NativeCalculatorOutput(
            values,
            status=status,
            status_detail={
                "business_calibration_status": "PENDING",
                "coverage_scope": "FULL_INPUT_ARTIFACT",
                "aggregation": {"min_coverage": 0.6, "pass_threshold": 0.8}
                | options.get("aggregation", {}),
                "embedding_cache_hits": encoder.hits if encoder else 0,
                "embedding_cache_misses": encoder.misses if encoder else 0,
            },
            decisions=EvidenceOutput(
                destination,
                EVIDENCE_KIND,
                DECISION_SCHEMA_REF,
                count,
                contains_raw_content=bool(relations),
                redaction_policy="bounded-160-codepoint-excerpts-and-resolved-value-positions"
                if relations
                else "identifiers-positions-and-value-hashes-no-excerpts",
                attributes={"business_calibration_status": "PENDING"},
            ),
        )

    @staticmethod
    def _encode(rows, relations, encoder):
        matches = {}
        pending = {}
        chunk_count = 0

        @lru_cache(maxsize=64)
        def split(text, chunk_chars, max_chunks):
            try:
                return embedding.split_field(
                    text,
                    {"chunk_chars": chunk_chars, "max_chunks": max_chunks},
                    encoder,
                )
            except Exception:  # noqa: BLE001 - isolate tokenizer errors
                return []

        def flush():
            vectors = embedding.encode_window(
                encoder,
                (
                    text
                    for _, left, right in pending.values()
                    if left and right
                    for _, _, text in (*left, *right)
                ),
            )
            matches.update(
                (key, embedding.match(params, left, right, vectors))
                for key, (params, left, right) in pending.items()
            )
            pending.clear()

        for sample_id, fields in rows:
            for relation in relations:
                left, right = (fields[relation[side]] for side in ("left", "right"))
                params = relation["parameters"]
                if not eligible(left, right, fields, params):
                    continue
                size, limit = params["chunk_chars"], params["max_chunks"]
                a, b = split(left.value, size, limit), split(right.value, size, limit)
                count = len(a) + len(b) if a and b else 0
                if chunk_count + count > VECTOR_WINDOW:
                    flush()
                    chunk_count = 0
                pending[(sample_id, left.field_id, right.field_id)] = (params, a, b)
                chunk_count += count
        flush()
        return matches
