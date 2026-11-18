"""Deterministic structured-sensitive-element candidate detector for metric 0202."""

from __future__ import annotations

import datetime as dt
import ipaddress
import re
from collections.abc import Iterator
from pathlib import Path

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

RULESET_VERSION = "structured-sensitive-elements@1.0.0"
CATEGORIES = (
    "MOBILE_PHONE",
    "NATIONAL_ID",
    "EMAIL",
    "BANK_CARD",
    "USCC",
    "IP_ADDRESS",
    "HOME_ADDRESS",
)

_PHONE = re.compile(r"(?<![0-9A-Za-z])1[3-9]\d{9}(?![0-9A-Za-z])")
_NATIONAL_ID = re.compile(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])")
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9_.+-])[A-Za-z0-9](?:[A-Za-z0-9._%+-]{0,62}[A-Za-z0-9])?@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z]{2,63})+(?![A-Za-z0-9_.-])"
)
_BANK = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_USCC = re.compile(
    r"(?<![0-9A-Z])[159Y][1239][0-9ABCDEFGHJKLMNPQRTUWXY]{16}(?![0-9A-Z])",
    re.IGNORECASE,
)
_IPV4 = re.compile(r"(?<![0-9A-Za-z_.:])(?:\d{1,3}\.){3}\d{1,3}(?![0-9A-Za-z_.:])")
_IPV6 = re.compile(
    r"(?<![0-9A-Za-z:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Za-z:.])"
)
_ADDRESS = re.compile(
    r"(?<![\u4e00-\u9fffA-Za-z0-9])"
    r"[\u4e00-\u9fffA-Za-z0-9]{2,48}"
    r"(?:路|街|巷|道|弄|小区|花园|家园|公寓|村|镇)"
    r"[\u4e00-\u9fffA-Za-z0-9-]{0,32}"
    r"(?:\d{1,5}号|\d{1,4}(?:栋|幢|号楼)|\d{1,4}单元|\d{1,4}(?:室|房))"
    r"[\u4e00-\u9fffA-Za-z0-9-]{0,16}"
    r"(?![\u4e00-\u9fffA-Za-z0-9])"
)
_ADDRESS_LABEL = re.compile(r"(?:家庭住址|居住地址|现住址|收件地址)\s*[：:]?\s*$")
_OFFICE_OR_PUBLIC = re.compile(
    r"(?:有限公司|公司|政府|委员会|公安局|法院|学校|医院|商场|酒店|办公地址|注册地址|经营场所|景区|公园|车站|机场)"
)
_RESIDENTIAL_DETAIL = re.compile(
    r"(?:小区|花园|家园|公寓|\d+(?:栋|幢|号楼|单元|室|房))"
)

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"
_USCC_ALPHABET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
_USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)


def valid_national_id(value: str) -> bool:
    value = value.upper()
    try:
        dt.date(int(value[6:10]), int(value[10:12]), int(value[12:14]))
    except ValueError:
        return False
    return (
        value[:6] != "000000"
        and value[14:17] != "000"
        and _ID_CHECK[
            sum(int(char) * weight for char, weight in zip(value[:17], _ID_WEIGHTS))
            % 11
        ]
        == value[-1]
    )


def valid_luhn(value: str) -> bool:
    digits = [int(char) for char in value if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def valid_uscc(value: str) -> bool:
    value = value.upper()
    if len(value) != 18 or any(char not in _USCC_ALPHABET for char in value):
        return False
    expected = (
        31
        - sum(
            _USCC_ALPHABET.index(char) * weight
            for char, weight in zip(value[:17], _USCC_WEIGHTS)
        )
        % 31
    ) % 31
    return _USCC_ALPHABET[expected] == value[-1]


def _mask(value: str, category: str) -> str:
    if category == "MOBILE_PHONE":
        return value[:3] + "****" + value[-4:]
    if category == "NATIONAL_ID":
        return value[:3] + "***********" + value[-4:]
    if category == "BANK_CARD":
        digits = "".join(char for char in value if char.isdigit())
        return "**** **** **** " + digits[-4:]
    if category == "EMAIL":
        local, domain = value.split("@", 1)
        return local[:1] + "***@" + domain
    if category == "IP_ADDRESS":
        return "[IP_ADDRESS_REDACTED]"
    if category == "USCC":
        return value[:2] + "**************" + value[-2:]
    return "[DETAILED_HOME_ADDRESS_REDACTED]"


class StructuredDetector:
    detector_id = "structured_sensitive_element_regex"
    detector_version = "1.0.0"

    def __init__(self, enabled: frozenset[str]):
        self.enabled = enabled
        self.config_fingerprint = stable_digest(
            {"ruleset": RULESET_VERSION, "enabled_categories": sorted(enabled)}
        )

    def hits(self, text: str) -> Iterator[CandidateHit]:
        validators = {
            "MOBILE_PHONE": (_PHONE, lambda _: True, "mobile.cn.boundary.v1"),
            "NATIONAL_ID": (_NATIONAL_ID, valid_national_id, "national-id.cn.mod11.v1"),
            "EMAIL": (_EMAIL, lambda _: True, "email.common.boundary.v1"),
            "BANK_CARD": (_BANK, valid_luhn, "bank-card.luhn.v1"),
            "USCC": (_USCC, valid_uscc, "uscc.gb32100.v1"),
        }
        for category in CATEGORIES:
            if category not in self.enabled:
                continue
            if category in validators:
                pattern, validator, rule_id = validators[category]
                for match in pattern.finditer(text):
                    if validator(match.group()):
                        yield CandidateHit(
                            category,
                            match.start(),
                            match.end(),
                            _mask(match.group(), category),
                            (rule_id,),
                            {"validation": "STRUCTURE_VALID"},
                        )
            elif category == "IP_ADDRESS":
                seen: set[tuple[int, int]] = set()
                for version, pattern in (("IPv4", _IPV4), ("IPv6", _IPV6)):
                    for match in pattern.finditer(text):
                        span = match.span()
                        if span in seen or not match.group():
                            continue
                        try:
                            parsed = ipaddress.ip_address(match.group())
                        except ValueError:
                            continue
                        if parsed.version != int(version[-1]):
                            continue
                        seen.add(span)
                        yield CandidateHit(
                            category,
                            *span,
                            _mask(match.group(), category),
                            (f"ip.{version.lower()}.ipaddress.v1",),
                            {"ip_version": version, "validation": "STRUCTURE_VALID"},
                        )
            else:
                for match in _ADDRESS.finditer(text):
                    value = match.group()
                    prefix = text[max(0, match.start() - 16) : match.start()]
                    if _OFFICE_OR_PUBLIC.search(value) or _OFFICE_OR_PUBLIC.search(
                        prefix
                    ):
                        continue
                    if not (
                        _RESIDENTIAL_DETAIL.search(value)
                        or _ADDRESS_LABEL.search(prefix)
                    ):
                        continue
                    yield CandidateHit(
                        category,
                        match.start(),
                        match.end(),
                        _mask(value, category),
                        ("home-address.cn.structure-keywords.v1",),
                        {
                            "confidence_status": "SUSPECTED",
                            "validation": "DETAILED_ADDRESS_SHAPE",
                        },
                    )

    def diagnostics(self) -> JsonObject:
        return {
            "ruleset_version": RULESET_VERSION,
            "enabled_categories": sorted(self.enabled),
            "validation_semantics": "structure-valid candidates; not confirmed privacy leakage",
        }


class StructuredSensitiveElementCalculator:
    calculator_key = "structured_sensitive_element_regex"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        root = Path(__file__).resolve().parent
        return (Path(__file__), root / "safety_candidate_common.py")

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        validate_common(context, parameters)
        enabled = parameters.get("enabled_categories", list(CATEGORIES))
        if (
            not isinstance(enabled, list)
            or not enabled
            or not all(isinstance(value, str) for value in enabled)
        ):
            raise CalculatorValidationError(
                "enabled_categories must be a non-empty string list"
            )
        if len(enabled) != len(set(enabled)) or set(enabled) - set(CATEGORIES):
            raise CalculatorValidationError(
                "enabled_categories contains duplicates or unsupported values"
            )
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        self.validate(context, parameters)
        enabled = frozenset(parameters.get("enabled_categories", list(CATEGORIES)))
        return execute_detector(
            context,
            parameters,
            StructuredDetector(enabled),
            "structured-sensitive-element-hits.jsonl",
            [
                "seven configurable deterministic structured-sensitive-element candidate types",
                "full eligible logical text fields",
            ],
            [
                "format validity does not establish privacy leakage or non-compliance",
                "home addresses are heuristic SUSPECTED candidates",
                "HTML offsets use extracted visible-text coordinates",
                "no external validation, geocoding, DNS, network lookup, or model inference",
            ],
        )


CALCULATOR = StructuredSensitiveElementCalculator()
