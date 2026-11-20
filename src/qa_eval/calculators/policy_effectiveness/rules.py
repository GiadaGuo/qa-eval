"""R1–R5 from the Phase 1 full-text policy effectiveness review."""

import calendar
import re
from datetime import date, timedelta

STATUS = r"(?:【(?:已)?(?:废止|失效)】|（(?:已)?(?:废止|失效)）|\[(?:已)?(?:废止|失效)\]|\((?:已)?(?:废止|失效)\))"
SELF = r"本(?:考核|资金管理|实施|暂行|管理)?(?:办法|通知|文件|意见|方案|细则|政策|措施|规定|条例|制度|通告|公告|决定|指引|法)"
DAY = r"[0-9０-９]{4}(?:年[0-9０-９]{1,2}月[0-9０-９]{1,2}日|[-/][0-9０-９]{1,2}[-/][0-9０-９]{1,2}(?![0-9０-９]))"
ACTION = r"施行|执行|实施|实行|生效"
START = rf"自\s*(?P<start>{DAY})\s*(?:日)?(?:起|开始)?\s*(?:{ACTION})"
ANCHOR = rf"自[^，,。；;！？?\n]{{1,34}}?(?:{ACTION})"
LINK = r"(?:\s*[，,]\s*由[^，,。；;]{1,24}负责解释)?\s*[，,]?\s*"
PREFIX = rf"{SELF}\s*(?:{ANCHOR}{LINK}|适用于[0-9]{{4}}年度{LINK})?"
CUT = r"(?:至|截至|截止(?:到)?|到)"
STOP = r"(?:废止|失效|停止(?:施行|执行|实施))"
NUM = r"[0-9０-９]{1,3}|[一二两三四五六七八九十]{1,3}"
END = r"(?=\s*(?:[，,。；;！？、）)]|$))"
DOMAIN = r"房票|证书|许可证|权证|资格|资质|项目|协议|合同|使用权|申请期限|备案通知书|签证|疫苗|延期付款|延期申请|延期缴费|延期期限|延期验收"
OTHER_SUBJECT = (
    r"原[^。；;，,]{1,45}|《[^》]{1,100}》(?:[（(][^）)]{1,35}[）)])?|[^。；;，,]{0,30}(?:"
    + DOMAIN
    + r")"
)
PATTERNS = [
    (rule, kind, scope, re.compile(pattern))
    for rule, kind, scope, pattern in [
        (
            "R2",
            "FIXED_END",
            "SELF",
            rf"{PREFIX}(?:有效期\s*{CUT}[:：]?\s*|(?<=[，,])\s*至\s*)(?P<day>{DAY})",
        ),
        ("R2", "FIXED_END", "SELF", rf"{SELF}\s*{ANCHOR}\s*至\s*(?P<day>{DAY})"),
        (
            "R3",
            "RELATIVE_TERM",
            "SELF",
            rf"{PREFIX}有效期(?:为)?\s*(?P<count>{NUM})\s*(?P<unit>年|个月|月){END}",
        ),
        (
            "R4",
            "CESSATION",
            "SELF",
            rf"{SELF}\s*(?:自\s*(?P<day>{DAY})\s*起?\s*)?(?:即行|自动|已|予以)?{STOP}{END}",
        ),
        (
            "R4",
            "CESSATION",
            "OTHER",
            rf"(?:{OTHER_SUBJECT})\s*(?:同时|自动|予以)?{STOP}{END}",
        ),
        (
            "R5",
            "EXTENSION",
            "SELF",
            rf"{SELF}\s*(?:(?:原)?有效期(?:由\s*(?P<old>{DAY})\s*)?|继续有效期?)\s*(?:明确)?(?:延长|延期|续期)\s*(?:至|到)\s*(?P<day>{DAY})",
        ),
        (
            "R5",
            "EXTENSION",
            "SELF",
            rf"{SELF}\s*(?:延长|延期|续期)\s*(?:至|到)\s*(?P<day>{DAY})",
        ),
        (
            "R5",
            "CONTINUATION",
            "SELF",
            rf"{SELF}\s*继续有效(?:\s*至\s*(?P<day>{DAY}))?{END}",
        ),
        ("R5", "CONTINUATION", "UNCERTAIN", r"效力\s*[:：]\s*继续有效"),
    ]
]
START_PATTERN = re.compile(rf"{SELF}\s*{START}(?=\s*(?:至|[，,。；;！？、）)]|$))")
FALLBACK = re.compile(
    rf"(?P<R5>有效期(?:延长|延期)至\s*(?:{DAY})?|继续有效)|"
    rf"(?P<R2>有效期\s*{CUT}\s*[:：]?\s*(?:{DAY})?)|"
    rf"(?P<R3>有效期(?:为)?\s*(?:{NUM})\s*(?:年|个月|月))|"
    rf"(?P<R4>{STOP})"
)
QUOTES = re.compile(r"《[^》]*》|“[^”]*”|「[^」]*」")
CLEANUP = r"关于(?:决定|宣布)?废止.+的通知|行政规范性文件清理结果|规范性文件目录"
TYPE = r"办法|意见|方案|细则|规定|条例|指引|制度|措施|政策|管理规则|法"
EXCLUDED = (
    r"政策解读|图文解读|视频解读|负责人解读|图解|一文读懂|(?:答记者问|问答)(?:$|[】）])|"
    r"征求意见|草案|新闻发布会|(?:项目核准|项目备案|工程立项申请|候车亭改造修复项目).{0,12}批复|"
    r"(?:核准|注销).{1,85}(?:资质|许可证)|辐射安全许可|办件单号|"
    r"统计调查项目目录|项目情况表|项目$|(?:资格证|许可证|资质证书)$|"
    r"关于认定.{1,100}(?:商号|名单)的通知|关于.{1,60}(?:任职|销案摘牌|组成人员)的通知"
)


def parse_day(raw):
    if not isinstance(raw, str):
        return None
    parts = re.fullmatch(DAY, raw.strip())
    if parts:
        try:
            return date(*map(int, re.findall(r"\d+", parts[0])))
        except ValueError:
            pass
    return None


def add_term(start, count, unit):
    if not count.isdecimal():
        digits = dict(zip("一二三四五六七八九", range(1, 10)))
        count = count.replace("两", "二")
        if count in digits:
            count = digits[count]
        elif re.fullmatch(r"[一二三四五六七八九]?十[一二三四五六七八九]?", count):
            tens, ones = count.split("十")
            count = 10 * digits.get(tens, 1) + digits.get(ones, 0)
        else:
            return None
    else:
        count = int(count)
    if count <= 0:
        return None
    year, month = divmod(
        start.year * 12 + start.month - 1 + count * (12 if unit == "年" else 1), 12
    )
    if year > 9999:
        return None
    return date(
        year, month + 1, min(start.day, calendar.monthrange(year, month + 1)[1])
    )


def classify(title, body, document_type):
    plain = re.sub(r"<[^>]+>|" + STATUS, "", title).strip()
    doc_type = document_type or ""
    if re.search(EXCLUDED, plain) or re.search(
        r"图解|问答|证照|行政许可|项目核准", doc_type
    ):
        return "NOT_APPLICABLE", "EXCLUDED_DOCUMENT"
    policy_title = re.search(r"(?:" + TYPE + r")(?:[（(][^）)]*[）)])?$", plain)
    operative_clause = re.search(rf"{SELF}\s*(?:{ANCHOR}|有效期)", body)
    if "解读" in doc_type:
        if (
            plain.endswith(("通知", "通告", "意见", "办法", "规定", "方案"))
            or START_PATTERN.search(body)
            or re.search(rf"{SELF}\s*有效期\s*{CUT}\s*{DAY}", body)
        ):
            return "APPLICABILITY_UNCERTAIN", "DOCUMENT_TYPE_TITLE_CONFLICT"
        return "NOT_APPLICABLE", "EXCLUDED_DOCUMENT"
    if policy_title:
        return "APPLICABLE", "POLICY_TITLE"
    if re.search(r"(?:印发|下发|批转).*(?:" + TYPE + r").*的通知", plain, re.DOTALL):
        return "APPLICABLE", "POLICY_ISSUANCE_TITLE"
    if re.search(CLEANUP, plain) and plain.endswith(("通知", "公告", "决定")):
        return "APPLICABLE", "CLEANUP_INSTRUMENT_TITLE"
    if plain.endswith(("通知", "通告", "公示", "公告", "决定")) and operative_clause:
        return "APPLICABLE", "OPERATIVE_CLAUSE_IN_BODY"
    return "APPLICABILITY_UNCERTAIN", "INSUFFICIENT_DOCUMENT_TYPE_EVIDENCE"


def signal(rule, kind, scope, match, source, *, day=None, **more):
    return dict(
        rule_id=rule,
        signal_type=kind,
        target_scope=scope,
        date=day,
        start=match.start(),
        end=match.end(),
        source_field=source,
        **more,
    )


CONDITIONAL = re.compile(
    r"如(?:果)?|若|假设|建议|拟定?|待|一旦|违反|部分|引用|依据|根据|按照"
)


def clause_prefix(text, position, delimiters):
    start = max(text.rfind(delimiter, 0, position) for delimiter in delimiters) + 1
    return text[start:position]


def extract(title, body, applicability):
    excluded = applicability == "NOT_APPLICABLE"
    for match in re.finditer(STATUS, title):
        if not any(
            q.start() <= match.start() < q.end() for q in QUOTES.finditer(title)
        ):
            yield signal(
                "R1", "TITLE_CESSATION", "OTHER" if excluded else "SELF", match, "title"
            )
    match = re.search(r"关于(?:决定|宣布)?废止.+的通知", title)
    if match:
        yield signal("R4", "CESSATION_MENTION", "OTHER", match, "title")

    quotes = list(QUOTES.finditer(body))
    strong = []
    for rule, kind, scope, pattern in PATTERNS:
        for match in pattern.finditer(body):
            if scope == "SELF" and any(
                q.start() <= match.start() < q.end() for q in quotes
            ):
                continue
            prefix = clause_prefix(body, match.start(), "。；;！？\n，,")
            if scope == "SELF" and CONDITIONAL.search(prefix):
                continue
            groups = match.groupdict()
            start = re.search(START, match[0])
            expiry = groups.get("day")
            strong.append((rule, match.span()))
            yield signal(
                rule,
                kind,
                "OTHER" if excluded else scope,
                match,
                "content",
                day=expiry,
                explicit_replacement="原有效期" in match[0],
                start_raw=start["start"] if start else None,
                replaces_raw=groups.get("old"),
                count=groups.get("count"),
                unit=groups.get("unit"),
            )
    for match in START_PATTERN.finditer(body):
        if not any(
            q.start() <= match.start() < q.end() for q in quotes
        ) and not CONDITIONAL.search(
            clause_prefix(body, match.start(), "。；;！？\n，,")
        ):
            yield signal(
                None,
                "COMMENCEMENT",
                "OTHER" if excluded else "SELF",
                match,
                "content",
                day=match["start"],
            )
    for match in FALLBACK.finditer(body):
        rule = match.lastgroup
        if any(r == rule and lo <= match.start() < hi for r, (lo, hi) in strong):
            continue
        prefix = clause_prefix(body, match.start(), "。；;！？\n")
        cleanup = re.search(CLEANUP, title) and rule in ("R4", "R5")
        external = (
            excluded
            or cleanup
            or any(q.start() <= match.start() < q.end() for q in quotes)
            or re.search(DOMAIN, prefix)
            or re.search(r"同时\s*$", prefix)
        )
        day = re.search(DAY, match[0])
        yield signal(
            rule,
            "UNRESOLVED_MENTION",
            "OTHER" if external else "UNCERTAIN",
            match,
            "content",
            day=day[0] if day else None,
        )


def adjudicate(signals, reference, publication, observed):
    own = [s for s in signals if s["target_scope"] == "SELF"]
    starts = set()
    terms = set()
    stops = set()
    extensions = set()
    invalid = False
    undated_stop = False
    continuation = False
    for item in own:
        kind = item["signal_type"]
        day = parse_day(item["date"])
        start = parse_day(item.get("start_raw"))
        if start:
            starts.add(start)
        invalid |= bool(item["date"] and not day or item.get("start_raw") and not start)
        if kind == "COMMENCEMENT" and day:
            starts.add(day)
        elif kind == "FIXED_END" and day:
            terms.add(day)
        elif kind == "CESSATION" and day:
            stops.add(day)
        elif kind in ("CESSATION", "TITLE_CESSATION") and not item["date"]:
            undated_stop = True
        elif kind in ("EXTENSION", "CONTINUATION"):
            old = parse_day(item.get("replaces_raw"))
            invalid |= bool(item.get("replaces_raw") and not old)
            if day:
                extensions.add((day, old, item.get("explicit_replacement", False)))
            else:
                continuation = True

    start = next(iter(starts)) if len(starts) == 1 else None
    missing_anchor = False
    for item in own:
        if item["signal_type"] == "RELATIVE_TERM":
            anchor = parse_day(item.get("start_raw")) or start
            boundary = add_term(anchor, item["count"], item["unit"]) if anchor else None
            if boundary:
                terms.add(boundary - timedelta(days=1))
            else:
                missing_anchor = True

    end = next(iter(terms)) if len(terms) == 1 else None
    conflict = (
        len(starts) > 1 or len(terms) > 1 or len(stops) > 1 or len(extensions) > 1
    )
    if len(extensions) == 1:
        new, old, replaces_original = next(iter(extensions))
        if not old and replaces_original:
            old = end
        if old and old < new and (not terms or terms == {old}):
            end = new
        elif terms != {new}:
            conflict = True
    if continuation and (terms or extensions):
        conflict = True
    if (continuation or extensions) and (stops or undated_stop):
        conflict = True
    stop = min(stops) if stops else None
    if start and (end and end < start or stop and stop < start):
        invalid = True
    if conflict:
        return "UNDETERMINED", "CONFLICTING_SIGNALS", start, None
    if invalid:
        return "UNDETERMINED", "UNRELIABLE_DATE", start, end
    if (start or publication) and reference < (start or publication):
        return "UNDETERMINED", "BEFORE_KNOWN_START_OR_PUBLICATION", start, end
    if end and any(
        item["rule_id"] == "R5" and item["target_scope"] == "UNCERTAIN"
        for item in signals
    ):
        return "UNDETERMINED", "UNRESOLVED_CONTINUATION_SCOPE", start, end
    if stop and reference >= stop:
        return "EXPIRED_EVIDENCED", "DATED_SELF_CESSATION", start, end
    if undated_stop and reference == observed and not stop:
        return "EXPIRED_EVIDENCED", "UNDATED_CESSATION_AT_OBSERVATION", start, end
    if missing_anchor:
        return "UNDETERMINED", "RELATIVE_TERM_WITHOUT_RELIABLE_ANCHOR", start, end
    if end and reference > end:
        return "EXPIRED_EVIDENCED", "KNOWN_END_PASSED", start, end
    if undated_stop and not stop:
        return "UNDETERMINED", "UNDATED_CESSATION_OUTSIDE_OBSERVATION", start, end
    if end:
        return "NOT_EXPIRED_BY_KNOWN_TERM", "WITHIN_KNOWN_TERM", start, end
    reason = (
        "TARGET_UNCLEAR"
        if any(s["target_scope"] == "UNCERTAIN" for s in signals)
        else "NO_DECISIVE_SELF_TERM"
    )
    return "UNDETERMINED", reason, start, end


def evaluate(title, body, document_type, publication, reference, observed):
    applicability, applicability_reason = classify(title, body, document_type)
    signals = list(extract(title, body, applicability))
    if applicability == "APPLICABILITY_UNCERTAIN":
        for item in signals:
            if item["target_scope"] == "SELF":
                item["target_scope"] = "UNCERTAIN"
    decision, reason, start, end = None, applicability_reason, None, None
    if applicability == "APPLICABLE":
        decision, reason, start, end = adjudicate(
            signals, reference, publication, observed
        )
    return applicability, decision, reason, start, end, signals
