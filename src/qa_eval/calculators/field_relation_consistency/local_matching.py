"""Full-text local TF-IDF and independently inferred rule categories."""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from itertools import pairwise
from typing import Protocol

from qa_eval.calculators.field_relation_consistency.engine import Match, unknown
from qa_eval.calculators.field_relation_consistency.structured import normalized_text


def resource_digest(resource):
    data = json.dumps(
        resource, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + sha256(data.encode()).hexdigest()


def terms(text, sizes):
    for match in re.finditer(r"[^\W_]+", text.lower()):
        word = match.group()
        for size in sizes:
            for index in range(len(word) - size + 1):
                yield word[index : index + size]


def vector(text, resource):
    counts = Counter(terms(normalized_text(text), resource["ngrams"]))
    idf = resource["idf"]
    return {
        term: (1 + math.log(counts[term])) * idf.get(term, resource["default_idf"])
        for term in sorted(counts)
        if idf.get(term, resource["default_idf"]) > 0
    }


def vector_norm(value):
    return math.sqrt(math.fsum(weight * weight for weight in value.values()))


def threshold_decision(raw, parameters, details):
    low, high = parameters["fail_below"], parameters["pass_at"]
    calibration = parameters.get("calibration")
    details.update(
        threshold_version=parameters["threshold_version"],
        fail_below=low,
        pass_at=high,
        score_basis="BINARY_THRESHOLD_SUPPORT_UNCALIBRATED"
        if calibration is None
        else "VERSIONED_SUPPORT_MAPPING",
        calibration_version=calibration["version"] if calibration else None,
        calibration_digest=parameters.get("_calibration_digest"),
    )
    if low <= raw < high:
        return Match("UNKNOWN", "ABSTENTION_INTERVAL", raw_score=raw, details=details)
    passed = raw >= high
    score = float(passed)
    if calibration:
        points = calibration["points"]
        for (x1, y1), (x2, y2) in pairwise(points):
            if raw <= x2:
                score = y1 + (y2 - y1) * (raw - x1) / (x2 - x1)
                break
    return Match(
        "PASS" if passed else "FAIL",
        "SUPPORT_AT_THRESHOLD" if passed else "SUPPORT_BELOW_THRESHOLD",
        raw_score=raw,
        score=score,
        details=details,
    )


def semantic_match(left, right, parameters):
    resource = parameters["tfidf"]
    size = parameters.get("block_chars", 600)
    details = {
        "algorithm": "character-ngram-tfidf-v1",
        "resource_version": resource["version"],
        "resource_digest": parameters["_tfidf_digest"],
        "block_chars": size,
    }
    if len(right.value) < parameters.get("minimum_body_chars", 40):
        return unknown("INSUFFICIENT_BODY_TEXT", **details)
    query = vector(left.value, resource)
    norm = vector_norm(query)
    if not norm:
        return unknown("NO_TITLE_FEATURES", **details)
    # Process all blocks. Length-weighted cosine and corpus-wide query feature
    # coverage prevent a single relevant fragment from representing a long body.
    covered = set()
    contributions = []
    characters = 0
    blocks = 0
    for offset in range(0, len(right.value), size):
        block = right.value[offset : offset + size]
        blocks += 1
        candidate = vector(block, resource)
        length = len(block)
        characters += length
        matching = query.keys() & candidate.keys()
        covered.update(matching)
        candidate_norm = vector_norm(candidate)
        if candidate_norm:
            dot = math.fsum(query[term] * candidate[term] for term in sorted(matching))
            contributions.append(length * min(1.0, dot / (norm * candidate_norm)))
    if not contributions:
        return unknown(
            "NO_BODY_FEATURES",
            processed_characters=characters,
            block_count=blocks,
            **details,
        )
    cosine = math.fsum(contributions) / characters
    overlap = math.fsum(query[term] for term in sorted(covered)) / math.fsum(
        query.values()
    )
    # Similarity is evidence of topical overlap. It cannot veto a record.
    raw = min(1.0, cosine * (0.5 + 0.5 * overlap))
    details.update(
        block_count=blocks,
        processed_characters=characters,
        mean_block_cosine=cosine,
        keyword_coverage=overlap,
    )
    return threshold_decision(raw, parameters, details)


@dataclass(frozen=True)
class Prediction:
    label: str | None
    reason: str
    anchors: list[dict]


class CategoryClassifier(Protocol):
    def predict(self, text: str, dictionary: dict) -> Prediction: ...


class RuleClassifier:
    def predict(self, text, dictionary):
        scores = Counter()
        evidence = []
        for label, rule in dictionary["categories"].items():
            for anchor in rule["anchors"]:
                # Match literal anchors only, keeping original code-point coordinates.
                mode, value = anchor["mode"], anchor["text"]
                if mode == "suffix":
                    end = len(text.rstrip())
                    start = end - len(value) if text[:end].endswith(value) else -1
                elif mode == "line_prefix":
                    match = re.search(r"(?m)^[ \t]*(" + re.escape(value) + ")", text)
                    start = match.start(1) if match else -1
                else:
                    start = text.find(value)
                if start >= 0:
                    scores[label] += anchor.get("weight", 1)
                    evidence.append(
                        {"label": label, "span": [start, start + len(value)]}
                    )
        if not scores:
            return Prediction(None, "NO_CATEGORY_ANCHOR", [])
        ranking = scores.most_common(2)
        label, score = ranking[0]
        margin = score - (ranking[1][1] if len(ranking) == 2 else 0)
        if score < dictionary["min_support"] or margin < dictionary["min_margin"]:
            return Prediction(None, "CATEGORY_AMBIGUOUS", evidence)
        return Prediction(label, "CATEGORY_IDENTIFIED", evidence)


def type_match(left, right, parameters):
    dictionary = parameters["dictionary"]
    details = {
        "algorithm": "literal-category-rules-v1",
        "resource_version": dictionary["version"],
        "resource_digest": parameters["_dictionary_digest"],
        "taxonomy": dictionary["taxonomy"],
    }
    if left.semantic_tag != dictionary["taxonomy"]:
        return unknown("SEMANTIC_ATTRIBUTE_UNCONFIRMED", **details)
    declared = normalized_text(left.value)
    label = dictionary["verified_labels"].get(declared)
    if label is None:
        return unknown("CATEGORY_MAPPING_UNVERIFIED", **details)
    prediction = RuleClassifier().predict(right.value, dictionary)
    details.update(
        predicted_category=prediction.label, right_anchors=prediction.anchors
    )
    if prediction.label is None:
        return unknown(prediction.reason, **details)
    return threshold_decision(float(prediction.label == label), parameters, details)
