"""Configurable Aho-Corasick candidate matcher for metric 0202."""

from __future__ import annotations

import json
import unicodedata
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
)
from qa_eval.calculators.safety_candidate_common import (
    CandidateHit,
    execute_detector,
    stable_digest,
    validate_common,
)
from qa_eval.contracts.base import JsonObject


@dataclass(frozen=True)
class DictionaryTerm:
    dictionary_id: str
    version: str
    category: str
    term: str
    term_id: str


def _normalize(
    value: str, unicode_normalization: str, case_sensitive: bool
) -> tuple[str, list[int]]:
    output: list[str] = []
    coordinates: list[int] = []
    for index, char in enumerate(value):
        normalized = (
            unicodedata.normalize(unicode_normalization, char)
            if unicode_normalization != "NONE"
            else char
        )
        normalized = normalized if case_sensitive else normalized.casefold()
        output.append(normalized)
        coordinates.extend([index] * len(normalized))
    return "".join(output), coordinates


def _load_terms(parameters: JsonObject) -> list[DictionaryTerm]:
    raw_terms: Any = parameters.get("dictionary", [])
    dictionary_path = parameters.get("dictionary_path")
    if dictionary_path is not None:
        if raw_terms:
            raise CalculatorValidationError(
                "dictionary and dictionary_path are mutually exclusive"
            )
        if not isinstance(dictionary_path, str) or not dictionary_path:
            raise CalculatorValidationError(
                "dictionary_path must be a non-empty string"
            )
        path = Path(dictionary_path)
        if not path.is_file():
            raise CalculatorValidationError("dictionary_path is unavailable")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CalculatorValidationError(
                "dictionary_path must contain valid UTF-8 JSON"
            ) from exc
        raw_terms = document.get("terms") if isinstance(document, dict) else document
    if not isinstance(raw_terms, list):
        raise CalculatorValidationError("dictionary must be an array")
    terms: list[DictionaryTerm] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, item in enumerate(raw_terms):
        if not isinstance(item, dict):
            raise CalculatorValidationError(
                "dictionary entries must be objects", index=index
            )
        required = (
            "dictionary_id",
            "version",
            "category",
            "term",
            "enabled",
            "match_mode",
        )
        if any(key not in item for key in required):
            raise CalculatorValidationError(
                "dictionary entry is missing required fields", index=index
            )
        if item["match_mode"] != "EXACT" or not isinstance(item["enabled"], bool):
            raise CalculatorValidationError(
                "match_mode must be EXACT and enabled must be boolean", index=index
            )
        strings = {
            key: item[key] for key in ("dictionary_id", "version", "category", "term")
        }
        if not all(isinstance(value, str) and value for value in strings.values()):
            raise CalculatorValidationError(
                "dictionary identity and term fields must be non-empty strings",
                index=index,
            )
        if not item["enabled"]:
            continue
        term_id = item.get("term_id") or stable_digest(strings)
        if not isinstance(term_id, str) or not term_id:
            raise CalculatorValidationError(
                "term_id must be a non-empty string", index=index
            )
        key = (item["dictionary_id"], item["version"], item["category"], item["term"])
        if key in seen:
            raise CalculatorValidationError(
                "duplicate enabled dictionary entry", index=index
            )
        seen.add(key)
        terms.append(DictionaryTerm(*key, term_id))
    return sorted(
        terms,
        key=lambda value: (
            value.dictionary_id,
            value.version,
            value.category,
            value.term,
            value.term_id,
        ),
    )


class _AhoCorasick:
    def __init__(self, patterns: list[tuple[str, int]]):
        self.goto: list[dict[str, int]] = [{}]
        self.fail = [0]
        self.output: list[list[int]] = [[]]
        for pattern, term_index in patterns:
            state = 0
            for char in pattern:
                if char not in self.goto[state]:
                    self.goto[state][char] = len(self.goto)
                    self.goto.append({})
                    self.fail.append(0)
                    self.output.append([])
                state = self.goto[state][char]
            self.output[state].append(term_index)
        queue = deque(self.goto[0].values())
        while queue:
            state = queue.popleft()
            for char, target in self.goto[state].items():
                queue.append(target)
                fallback = self.fail[state]
                while fallback and char not in self.goto[fallback]:
                    fallback = self.fail[fallback]
                self.fail[target] = self.goto[fallback].get(char, 0)
                self.output[target].extend(self.output[self.fail[target]])

    def matches(self, text: str) -> Iterator[tuple[int, int]]:
        state = 0
        for end, char in enumerate(text):
            while state and char not in self.goto[state]:
                state = self.fail[state]
            state = self.goto[state].get(char, 0)
            for term_index in self.output[state]:
                yield end, term_index


class DictionaryDetector:
    detector_id = "sensitive_dictionary_match"
    detector_version = "1.0.0"

    def __init__(
        self,
        terms: list[DictionaryTerm],
        unicode_normalization: str,
        case_sensitive: bool,
    ):
        self.terms = terms
        self.unicode_normalization = unicode_normalization
        self.case_sensitive = case_sensitive
        normalized_terms = [
            _normalize(term.term, unicode_normalization, case_sensitive)[0]
            for term in terms
        ]
        if any(not value for value in normalized_terms):
            raise CalculatorValidationError(
                "dictionary terms must remain non-empty after normalization"
            )
        self.patterns = normalized_terms
        self.automaton = _AhoCorasick(list(zip(normalized_terms, range(len(terms)))))
        self.config_fingerprint = stable_digest(
            {
                "terms": [term.__dict__ for term in terms],
                "unicode_normalization": unicode_normalization,
                "case_sensitive": case_sensitive,
                "algorithm": "aho-corasick@1.0.0",
            }
        )

    def hits(self, text: str) -> Iterator[CandidateHit]:
        normalized, coordinates = _normalize(
            text, self.unicode_normalization, self.case_sensitive
        )
        for normalized_end, term_index in self.automaton.matches(normalized):
            term = self.terms[term_index]
            normalized_start = normalized_end - len(self.patterns[term_index]) + 1
            start = coordinates[normalized_start]
            end = coordinates[normalized_end] + 1
            rule_id = f"dictionary:{term.dictionary_id}:{term.version}:{term.term_id}"
            yield CandidateHit(
                term.category,
                start,
                end,
                "[DICTIONARY_MATCH_REDACTED]",
                (rule_id,),
                {
                    "dictionary_id": term.dictionary_id,
                    "dictionary_version": term.version,
                    "term_id": term.term_id,
                    "match_mode": "EXACT",
                    "confidence_status": "SUSPECTED",
                },
            )

    def diagnostics(self) -> JsonObject:
        versions = sorted(
            {f"{term.dictionary_id}@{term.version}" for term in self.terms}
        )
        return {
            "algorithm": "AHO_CORASICK",
            "dictionary_entry_count": len(self.terms),
            "dictionary_versions": versions,
            "dictionary_content_hash": self.config_fingerprint,
            "case_sensitive": self.case_sensitive,
            "unicode_normalization": self.unicode_normalization,
            "matching_semantics": "candidate risk only; no semantic disambiguation",
        }


class SensitiveDictionaryMatchCalculator:
    calculator_key = "sensitive_dictionary_match"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        root = Path(__file__).resolve().parent
        return (Path(__file__), root / "safety_candidate_common.py")

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        validate_common(context, parameters)
        normalization = parameters.get("unicode_normalization", "NFKC")
        if normalization not in {"NONE", "NFC", "NFKC"}:
            raise CalculatorValidationError(
                "unicode_normalization must be NONE, NFC, or NFKC"
            )
        if not isinstance(parameters.get("case_sensitive", True), bool):
            raise CalculatorValidationError("case_sensitive must be boolean")
        _load_terms(parameters)
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        self.validate(context, parameters)
        terms = _load_terms(parameters)
        detector = DictionaryDetector(
            terms,
            parameters.get("unicode_normalization", "NFKC"),
            parameters.get("case_sensitive", True),
        )
        return execute_detector(
            context,
            parameters,
            detector,
            "sensitive-dictionary-hits.jsonl",
            [
                "enabled local EXACT dictionary terms over eligible logical text fields",
                "UTF-8 Chinese, English, and mixed text",
            ],
            [
                "dictionary hits are candidate risks and not confirmed violations",
                "no traditional/simplified conversion, pinyin, homophone, fuzzy matching, or semantic disambiguation",
                "normalization is per Unicode code point so original coordinates remain explicit",
                "HTML offsets use extracted visible-text coordinates",
            ],
        )


CALCULATOR = SensitiveDictionaryMatchCalculator()
