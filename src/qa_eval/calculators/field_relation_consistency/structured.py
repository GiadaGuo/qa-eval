"""Deterministic comparisons for fields with a declared shared meaning."""

import re
import unicodedata
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from qa_eval.calculators.field_relation_consistency.engine import compare, unknown

DOC_NUMBER = re.compile(
    r"([^\s\[\]〔〕【】()（）《》：:，。；、]{1,60})([〔\[【])(\d{4})([〕\]】])(\d{1,12})号"
)
OWN_NUMBER = re.compile(
    r"(?m)^[ \t]*(?:发文字号|本件文号|公文文号)[ \t]*[：:][ \t]*(?P<value>[^\n\r]+)"
)
NUMBER = re.compile(r"([+-]?(?:[0-9]{1,30}(?:\.[0-9]{1,20})?|\.[0-9]{1,20}))\s*(\S.*)?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def normalized_text(value):
    return " ".join(unicodedata.normalize("NFKC", value).split())


def exact_match(left, right, parameters):
    if not left.semantic_tag or left.semantic_tag != right.semantic_tag:
        return unknown("SEMANTIC_ATTRIBUTE_UNCONFIRMED")
    profile = parameters.get("normalizer", "unicode_whitespace_v1")
    a, b = left.value, right.value
    if profile == "document_number_v1":
        a, b = document_number(a), document_number(b)
        if a is None or b is None:
            return unknown("DOCUMENT_NUMBER_UNPARSEABLE", normalizer=profile)
    elif isinstance(a, str) and isinstance(b, str):
        a, b = normalized_text(a), normalized_text(b)
        if profile == "unicode_casefold_v1":
            a, b = a.casefold(), b.casefold()
    return compare(a == b, normalizer=profile)


def document_number(value):
    if not isinstance(value, str):
        return None
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))
    match = DOC_NUMBER.fullmatch(compact)
    if not match:
        return None
    prefix, opening, year, closing, sequence = match.groups()
    if {"〔": "〕", "[": "]", "【": "】"}[opening] != closing:
        return None
    return prefix, year, sequence


def own_document_number(value):
    # Only explicitly labelled document metadata qualifies. A plain mention,
    # quoted item, or attachment cannot establish a conflict for this document.
    matches = OWN_NUMBER.finditer(value)
    match = next(matches, None)
    if match is None or next(matches, None) is not None:
        return None, None
    prefix = value[: match.start()].rstrip()
    # The explicit metadata line must be in the outer header, before any
    # ordinary paragraphs, document/attachment section, or quoted title body.
    lines = prefix.splitlines()
    if (
        len(prefix) > 800
        or len(lines) > 8
        or re.search(
            r"(?m)(?:^\s*(?:附件|第一条|正文|根据|依据|引用|转发)|[。；]|^.{1,60}[：:]\s*$)",
            prefix,
        )
    ):
        return None, None
    parsed = document_number(match["value"])
    return parsed, list(match.span("value")) if parsed else None


def parse_date(value, zone, naive_zone, precision):
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        if DATE.fullmatch(value):
            result = date.fromisoformat(value) if precision == "day" else None
        else:
            # fromisoformat also accepts week dates and compact timestamps.
            if not re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value):
                return None
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                if not naive_zone:
                    return None
                naive = ZoneInfo(naive_zone)
                a, b = (parsed.replace(tzinfo=naive, fold=fold) for fold in (0, 1))
                if a.utcoffset() != b.utcoffset():
                    return None
                parsed = a
            if precision == "day" and not zone:
                return None
            parsed = parsed.astimezone(ZoneInfo(zone) if precision == "day" else UTC)
            result = parsed.date() if precision == "day" else parsed
        return result
    except (ValueError, OverflowError):
        return None


def numeric(value, default_unit, units):
    if type(value) in (int, float):
        token, unit = str(value), default_unit
    elif isinstance(value, str):
        match = NUMBER.fullmatch(normalized_text(value))
        if not match:
            return None
        token, unit = match.groups()
        unit = unit or default_unit
    else:
        return None
    if unit not in units:
        return None
    quantity = Decimal(token)
    if not quantity.is_finite() or abs(quantity.adjusted()) > 100:
        return None
    return quantity * Decimal(str(units[unit]))


def structured_match(left, right, parameters):
    kind = parameters["attribute"]
    extractor = parameters.get("right_extractor")
    details = {"attribute": kind, "rule_version": "structured_v1"}
    if not left.semantic_tag or left.semantic_tag != right.semantic_tag:
        return unknown("SEMANTIC_ATTRIBUTE_UNCONFIRMED", **details)
    if kind == "document_number":
        a = document_number(left.value)
        if extractor:
            b, span = own_document_number(right.value)
            details.update(right_extractor=extractor, right_span=span)
        else:
            b = document_number(right.value)
        if a is None or b is None:
            return unknown("OWN_DOCUMENT_NUMBER_UNCONFIRMED", **details)
        return compare(a == b, **details)
    if kind == "date":
        precision = parameters.get("precision", "day")
        zone = parameters.get("timezone")
        a = parse_date(
            left.value, zone, parameters.get("left_naive_timezone"), precision
        )
        b = parse_date(
            right.value, zone, parameters.get("right_naive_timezone"), precision
        )
        if a is None or b is None:
            return unknown("DATE_TIMEZONE_OR_PRECISION_UNKNOWN", **details)
        if a.year > parameters.get("maximum_year", 2098) or b.year > parameters.get(
            "maximum_year", 2098
        ):
            return unknown("DATE_SENTINEL_OR_OUTSIDE_DOMAIN", **details)
        details.update(precision=precision, timezone=zone)
        return compare(a == b, **details)

    units = parameters.get("unit_factors", {"1": 1})
    a = numeric(left.value, parameters.get("left_unit", "1"), units)
    b = numeric(right.value, parameters.get("right_unit", "1"), units)
    if a is None or b is None:
        return unknown("NUMBER_OR_UNIT_UNKNOWN", **details)
    absolute = Decimal(str(parameters.get("abs_tol", 0)))
    relative = Decimal(str(parameters.get("rel_tol", 0)))
    tolerance = absolute + relative * max(abs(a), abs(b))
    return compare(
        abs(a - b) <= tolerance,
        absolute_tolerance=str(absolute),
        relative_tolerance=str(relative),
        **details,
    )
