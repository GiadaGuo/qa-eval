"""Template paths, source evidence and scoped document-format checks."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from functools import cache
from pathlib import Path

from . import structure as prior
from .structure import clean_title, compact, state

DRAFT_TITLE = re.compile(
    r"征求意见(?:稿|的公告|的通知)|(?:送审|讨论|征求意见)稿|政策解读|一图读懂|答记者问"
)


HEADING = re.compile(
    r"^(?:第[一二三四五六七八九十百〇零0-9]+[章节条]|[一二三四五六七八九十]+[、．]|[（(][一二三四五六七八九十0-9]+[）)]|\d+[.、．])"
)


END = re.compile(r"[。！？；）)》]$")


STRONG_FOREIGN = {
    "批复": re.compile(r"现批复如下|特此批复"),
    "函": re.compile(r"现函复如下|特此函复|特此函告"),
    "通知": re.compile(r"现(?:就|将).{1,65}通知如下"),
    "请示": re.compile(r"妥否.{0,5}请批示"),
    "公告": re.compile(r"特此公告|现公告如下"),
    "通告": re.compile(r"特此通告|现通告如下"),
    "通报": re.compile(r"现通报如下"),
    "报告": re.compile(r"特此报告|专此报告"),
}


def evidence(body, block, match=None):
    if match is not None:
        value = match[0]
        start = body.find(value, block.start, block.end)
        if start != -1 and body.find(value, start + len(value), block.end) == -1:
            return {
                **prior.span(start, start + len(value)),
                "content_id": block.content_id,
            }
    return block.span()


def regex_signals(template, cfg, lines, body, scope, candidate, row, title_type):
    used = set()
    for option in template["path_options"]:
        used.update(x for group in option["required"] for x in group)
        used.update(option["optional"])
        used.update(conditional["signal"] for conditional in option["conditional"])
    used.update(("subject_title", "hierarchy", "signoff"))
    matches = {}
    title_text = candidate["name"] if candidate else (row.get("title") or "")
    title_text = prior.compact(title_text)
    for signal_id in sorted(used):
        rule = cfg["signals"][signal_id]
        zone, matcher = rule["zone"], rule["matcher"]
        locations = []
        if matcher == "REGEX":
            scope_lines = (
                lines[:8]
                if zone == "opening"
                else lines[-6:]
                if zone == "closing"
                else lines
            )
            for block in scope_lines:
                if prior.compact(block.text) == title_text:
                    continue
                # Prefer a stable raw substring, fall back to the containing block if markup interrupts it.
                for pattern in rule["patterns"]:
                    found = compiled(pattern).search(block.text)
                    if found:
                        locations.append(evidence(body, block, found))
                        break
                if len(locations) == 5:
                    break
        elif matcher == "OUTER_TITLE":
            location = (
                candidate["title_location"] if candidate else prior.title_span(row)
            )
            if location and title_type:
                locations = [location]
        elif matcher == "SIGNED_PAIR":
            date_block, agency = signed_date(row, lines)
            if date_block and agency:
                locations = [agency.span(), date_block.span()]
        elif matcher == "HIERARCHY":
            locations = [
                block.span()
                for block in lines
                if block.heading_level
                or block.kind == "LIST_ITEM"
                or HEADING.match(block.text)
            ][:5]
            if len(locations) < 2:
                locations = []
        elif matcher == "ATTACHMENT_BOUNDARY":
            locations = (
                candidate["evidence"]
                if candidate
                else [ref["location"] for ref in scope["references"]]
            )
            if scope["status"] == "BOUNDARY_CANDIDATE" and not locations and lines:
                # Child boundaries are recorded separately even without a visible attachment description.
                locations = scope.get("boundary_locations", [])
            if scope["status"] != "BOUNDARY_CANDIDATE":
                locations = []
        applicable = not (
            matcher == "ATTACHMENT_BOUNDARY" and not scope["required"] and not candidate
        )
        matches[signal_id] = {
            "id": signal_id,
            "zone": zone,
            "classification": rule["classification"],
            "sources": rule["sources"],
            "status": "PASS"
            if locations
            else "UNKNOWN"
            if applicable
            else "NOT_APPLICABLE",
            "reason": "VISIBLE_CANDIDATE_EVIDENCE"
            if locations
            else "NO_CONDITION"
            if not applicable
            else "ATTACHMENT_BOUNDARY_UNRESOLVED"
            if matcher == "ATTACHMENT_BOUNDARY"
            else "SIGNOFF_NOT_CORROBORATED"
            if matcher == "SIGNED_PAIR"
            else "SIGNAL_NOT_LOCATED",
            "locations": locations,
        }
    return matches


def paths_for(template, matches, scope, substantive_notice):
    results = []
    for path in template["path_options"]:
        if substantive_notice and path["id"] != "forwarding":
            results.append(
                {
                    "id": path["id"],
                    "status": "NOT_APPLICABLE",
                    "missing_alternatives": [],
                    "required_groups_hit": 0,
                    "optional_hits": 0,
                }
            )
            continue
        missing = [
            group
            for group in path["required"]
            if not any(matches[s]["status"] == "PASS" for s in group)
        ]
        applicable = [
            condition["signal"]
            for condition in path["conditional"]
            if scope["required"]
        ]
        missing.extend(
            [signal] for signal in applicable if matches[signal]["status"] != "PASS"
        )
        support = sum(
            any(matches[s]["status"] == "PASS" for s in group)
            for group in path["required"]
        )
        optional_hits = sum(matches[s]["status"] == "PASS" for s in path["optional"])
        results.append(
            {
                "id": path["id"],
                "status": "UNKNOWN" if missing else "PASS",
                "missing_alternatives": missing,
                "required_groups_hit": support,
                "optional_hits": optional_hits,
            }
        )
    return sorted(
        results,
        key=lambda x: (
            x["status"] != "PASS",
            -x["required_groups_hit"],
            -x["optional_hits"],
            x["id"],
        ),
    )


def independent_evidence(matches):
    """Choose non-overlapping anchors, each from a distinct substantive function."""
    observed = []
    for signal in sorted(
        matches.values(),
        key=lambda x: (x["zone"] != "body", x["zone"] == "structure", x["id"]),
    ):
        if signal["status"] != "PASS":
            continue
        for loc in signal["locations"]:
            if any(
                loc["field"] == old["field"]
                and loc["start"] < old["end"]
                and old["start"] < loc["end"]
                for _, old in observed
            ):
                continue
            observed.append((signal["id"], loc))
            break
    return [{"signal": name, "location": loc} for name, loc in observed]


ISSUER_END = re.compile(
    r"(?:政府|委员会|办公室|厅|局|党组|党委|中心|部门|总队|公司|协会|办公厅)$"
)


DOC_DATE = re.compile(r"([0-9]{4})年([0-9]{1,2})月([0-9]{1,2})日")


DATE_SHAPE = re.compile(
    r"[0-9零〇○一二三四五六七八九]{4}年[0-9一二三四五六七八九十]{1,3}月[0-9一二三四五六七八九十廿]{1,3}日"
)


NUMBER = re.compile(
    r"[\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z0-9·]{0,27}〔[0-9]{4}〕[1-9][0-9]*号"
)


NUMBER_SHAPE = re.compile(
    r"(?:(?:发文字号|文号)[：:]\s*)?([\u3400-\u9fffA-Za-z][\u3400-\u9fffA-Za-z0-9·]{0,27}[〔\[【（(][0-9]{4}[〕\]】）)]第?[0-9]+号)"
)


ORDER_NUMBER = re.compile(r"第\s*[1-9][0-9]*\s*号")


def issuers(row):
    result = set()
    for field in ("organization", "publishunit", "releaseunit"):
        if state(row.get(field)) == "present":
            result.update(
                compact(name)
                for name in re.split(r"[、，,；;]", str(row[field]))
                if name.strip()
            )
    return result


def signed_date(row, lines):
    agencies = issuers(row)
    for ix in range(len(lines) - 1, 0, -1):
        if len(lines) - ix > 12:
            break
        block = lines[ix]
        if not DATE_SHAPE.fullmatch(compact(block.text)):
            continue
        agency = lines[ix - 1]
        if compact(agency.text) in agencies:
            return block, agency
        if ISSUER_END.search(agency.text) and len(compact(agency.text)) <= 45:
            # A plausible but uncorroborated signature survives as UNKNOWN evidence.
            return block, None
    return None, None


def declared_pair_result(found, declared):
    if not found or not declared:
        return None
    by_title = {clean_title(name): (number, block) for number, name, block in declared}
    if len(by_title) != len(declared) or set(by_title) != {
        clean_title(item["name"]) for item in found
    }:
        return None
    for item in found:
        expected, _ = by_title[clean_title(item["name"])]
        if expected != item["number"]:
            return None
    if len(declared) > 1 and (
        any(not number for number, _, _ in declared)
        or len({number for number, _, _ in declared}) != len(declared)
    ):
        return None
    return [block.span() for _, _, block in declared] + [
        loc for item in found for loc in item["evidence"]
    ]


def integers_date(text):
    match = DOC_DATE.fullmatch(text)
    if not match:
        return None
    try:
        parsed = date(*map(int, match.groups()))
    except ValueError:
        return None
    return (
        parsed
        if match[2] == str(parsed.month) and match[3] == str(parsed.day)
        else None
    )


@cache
def compiled(pattern):
    return re.compile(pattern)


TEMPLATE_PATH = Path(__file__).with_name("templates.json")
SUPPORTED = frozenset(("通知", "意见", "批复", "函", "办法", "规定", "细则", "方案"))


@cache
def library():
    return json.loads(TEMPLATE_PATH.read_bytes())


def normative_rule(key, status, reason, locations, cfg):
    rule = cfg["normative_rules"][key]
    return {
        "id": key,
        "status": status,
        "reason": reason,
        "locations": locations,
        "classification": "NORMATIVE",
        "applicability": "NOT_APPLICABLE"
        if status == "NOT_APPLICABLE"
        else "UNVERIFIED"
        if status == "UNKNOWN"
        else "APPLICABLE",
        "source_url": cfg["sources"][rule["sources"][-1]]["url"],
        "clause": rule["clause"],
    }


def normative(row, kind, lines, scope, found, declared, anchor, cfg):
    results = []
    for key in cfg["templates"][kind]["normative_check_refs"]:
        status, reason, locations = "UNKNOWN", "REVIEW_REQUIRED", []
        if key == "N2":
            reason = "NO_CORROBORATED_ISSUE_NUMBER"
            for block in lines[:9]:
                match = NUMBER_SHAPE.fullmatch(block.text)
                if not match or not any(
                    match[1] == row.get(field) for field in ("issue", "filenumber")
                ):
                    continue
                locations = [block.span()]
                number = match[1]
                year = re.search(r"[〔\[【（(]([0-9]{4})", number)
                if year and int(year[1]) > 2012:
                    if NUMBER.fullmatch(number):
                        status, reason = "PASS", "CORROBORATED_NUMBER"
                    elif row.get("verified_original") and row.get(
                        "verified_standard_scope"
                    ):
                        status, reason = "FAIL", "VERIFIED_ORIGINAL_NUMBER_FORMAT"
                    else:
                        reason = "NUMBER_FIDELITY_UNVERIFIED"
                else:
                    reason = "STANDARD_VERSION_UNCONFIRMED"
                break
        elif key == "N3":
            block, agency = signed_date(row, lines)
            reason = "SIGNED_DATE_NOT_CORROBORATED"
            if block and agency:
                locations = [agency.span(), block.span()]
                day = integers_date(block.text)
                if day and day >= date(2012, 7, 1):
                    status, reason = "PASS", "CORROBORATED_SIGNOFF"
        elif key == "N4":
            reason = "ATTACHMENT_PAIR_UNVERIFIED"
            if anchor:
                relevant = [
                    x
                    for x in declared
                    if prior.clean_title(x[1]) == prior.clean_title(anchor["name"])
                ]
                paired = declared_pair_result([anchor], relevant)
            else:
                paired = (
                    None
                    if scope["unresolved"]
                    else declared_pair_result(found, declared)
                )
            if paired:
                status, reason, locations = (
                    "PASS",
                    "CORROBORATED_ATTACHMENT_PAIR",
                    paired,
                )
            elif (
                not (
                    scope["required"]
                    or scope["references"]
                    or scope["external_asset_signal"]
                    or scope["inline_marker_signal"]
                    or found
                    or anchor
                )
                and lines
                and signed_date(row, lines)[0] == lines[-1]
            ):
                status, reason = (
                    "NOT_APPLICABLE",
                    "SIGNED_END_WITHOUT_ATTACHMENT_SIGNALS",
                )
        elif key == "N8":
            reason = "PHYSICAL_LAYOUT_UNOBSERVABLE"
        results.append(normative_rule(key, status, reason, locations, cfg))
    return results


def inspect(row):
    cfg = library()
    title = row.get("title") or ""
    source = row.get("content") or ""
    raw_html = row.get("is_html", True)
    integrity = bool(raw_html and prior.INCOMPLETE_TAG.search(source.rstrip()[-1024:]))
    if len(source) > 2_000_000:
        return {
            "type_candidate": prior.type_name(title),
            "segments": [],
            "reasons": ["BODY_SIZE_LIMIT"],
        }
    all_lines = prior.blocks(source, row.get("body_id", "content"), raw_html)
    integrity |= bool(all_lines and prior.TRUNCATION.search(all_lines[-1].text))
    outer = prior.type_name(title)
    scope, found, declared = prior.find_boundaries(row, source, all_lines, title, outer)
    stop = found[0]["line_ix"] if found else len(all_lines)
    sections = [(None, True, outer, all_lines[:stop])]
    for idx, anchor in enumerate(found):
        end = found[idx + 1]["line_ix"] if idx + 1 < len(found) else len(all_lines)
        kind = prior.type_name(anchor["name"])
        sections.append(
            (
                anchor,
                scope["required"] and kind in prior.POLICY,
                kind,
                all_lines[anchor["title_ix"] : end],
            )
        )
    segments = []
    reasons = []
    if scope["required"] and scope["unresolved"]:
        reasons.append("REQUIRED_ATTACHMENT_UNRESOLVED")
    if integrity:
        reasons.append("BODY_INTEGRITY_UNVERIFIED")
    for idx, (anchor, required, kind, lines) in enumerate(sections):
        template = cfg["templates"].get(kind)
        match_specs = {
            **scope,
            "boundary_locations": [x["title_location"] for x in found],
        }
        matches = (
            regex_signals(template, cfg, lines, source, match_specs, anchor, row, kind)
            if template
            else {}
        )
        paths = (
            paths_for(
                template,
                matches,
                scope,
                kind == "通知" and not anchor and scope["required"],
            )
            if template
            else []
        )
        has_body = bool(
            template
            and any(
                matches[s]["status"] == "PASS" and matches[s]["zone"] == "body"
                for p in template["path_options"]
                for group in p["required"]
                for s in group
            )
        )
        supplied = (
            []
            if anchor
            else sorted(
                {
                    v
                    for v in (
                        row.get("type"),
                        row.get("policyType"),
                        row.get("document_type"),
                    )
                    if isinstance(v, str) and v in prior.TYPES
                }
            )
        )
        foreign = sorted(
            name
            for name, pattern in STRONG_FOREIGN.items()
            if name != kind
            and any(
                pattern.search(prior.QUOTED.sub("", block.text))
                for block in lines[:12] + lines[-4:]
            )
        )
        blockers = []
        if not lines:
            blockers.append("MISSING_BODY")
        elif not anchor and DRAFT_TITLE.search(title):
            blockers.append("DRAFT_OR_INTERPRETATION")
        if (
            (not anchor and row.get("type") == "解读")
            or any(v != kind for v in supplied)
            or foreign
        ):
            blockers.append("TYPE_CONFLICT")
        elif not kind or not has_body:
            blockers.append("TYPE_UNCONFIRMED")
        elif kind not in SUPPORTED:
            blockers.append("TEMPLATE_NOT_CALIBRATED")
        for rule in matches.values():
            rule["applicability"] = (
                "NOT_APPLICABLE" if rule["status"] == "NOT_APPLICABLE" else "APPLICABLE"
            )
        if kind and lines and not integrity:
            checks = normative(row, kind, lines, scope, found, declared, anchor, cfg)
            if blockers:
                for check in checks:
                    if check["status"] in {"PASS", "FAIL"}:
                        check.update(
                            status="UNKNOWN",
                            reason="DOCUMENT_SCOPE_UNCONFIRMED",
                            applicability="UNVERIFIED",
                        )
        else:
            checks = (
                [
                    normative_rule(k, "UNKNOWN", "BODY_UNAVAILABLE", [], cfg)
                    for k in template["normative_check_refs"]
                ]
                if template
                else []
            )
        anchors = independent_evidence(matches)
        segments.append(
            {
                "id": "main" if not idx else f"attachment-{idx}",
                "parent": "main" if idx else None,
                "required": required,
                "type_candidate": kind,
                "metadata_candidates": supplied,
                "competing_types": foreign,
                "reasons": blockers,
                "paths": paths,
                "signals": list(matches.values()),
                "normative": checks,
                "anchors": anchors,
                "location": lines[0].span() | {"end": lines[-1].end}
                if lines and not integrity
                else None,
                "last_clause": bool(lines and END.search(lines[-1].text)),
                "structure_types": dict(Counter(block.kind for block in lines)),
            }
        )
    if integrity:
        for segment in segments:
            for rule in segment["signals"]:
                rule["locations"] = []
                if rule["status"] == "PASS":
                    rule.update(status="UNKNOWN", reason="SOURCE_COORDINATE_UNVERIFIED")
            segment["anchors"] = []
    return {
        "type_candidate": outer,
        "segments": segments,
        "reasons": reasons,
        "attachment_status": scope["status"],
    }
