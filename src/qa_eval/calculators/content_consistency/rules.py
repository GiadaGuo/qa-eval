"""Conservative constraint extraction and compatibility checks."""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal, InvalidOperation

from qa_eval.calculators.content_consistency.text import SENTENCE
from qa_eval.calculators.policy_structure_compliance.parsers import number_value

ATTRIBUTES = {
    "补贴金额": "AMOUNT",
    "补助金额": "AMOUNT",
    "收费金额": "AMOUNT",
    "收费标准": "AMOUNT",
    "申请费": "AMOUNT",
    "费用": "AMOUNT",
    "补贴标准": "AMOUNT",
    "补助标准": "AMOUNT",
    "金额": "AMOUNT",
    "补贴比例": "RATIO",
    "补助比例": "RATIO",
    "报销比例": "RATIO",
    "比例": "RATIO",
    "费率": "RATIO",
    "占比": "RATIO",
    "申请期限": "DURATION",
    "办理期限": "DURATION",
    "公示期限": "DURATION",
    "受理期限": "DURATION",
    "有效期限": "DURATION",
    "期限": "DURATION",
    "有效期": "DURATION",
    "办理时限": "DURATION",
    "时限": "DURATION",
    "申请截止日期": "DATE",
    "截止日期": "DATE",
    "截止时间": "DATE",
    "开始日期": "DATE",
    "申请日期": "DATE",
    "施行日期": "DATE",
    "生效日期": "DATE",
    "申报日期": "DATE",
    "实施日期": "DATE",
    "最高人数": "QUANTITY",
    "名额": "QUANTITY",
    "人数": "QUANTITY",
    "数量": "QUANTITY",
    "份数": "QUANTITY",
    "次数": "QUANTITY",
}
COMPARATORS = {
    "不少于": ">=",
    "不低于": ">=",
    "至少": ">=",
    "不得少于": ">=",
    "不超过": "<=",
    "不高于": "<=",
    "至多": "<=",
    "不得超过": "<=",
    "多于": ">",
    "超过": ">",
    "高于": ">",
    "少于": "<",
    "低于": "<",
    "截至": "<=",
    "早于": "<",
    "晚于": ">",
    "不晚于": "<=",
    "不早于": ">=",
    "为": "=",
    "是": "=",
    "等于": "=",
    "：": "=",
    ":": "=",
    "=": "=",
}
UNITS = {
    "亿元": ("CNY", 100000000),
    "万元": ("CNY", 10000),
    "千元": ("CNY", 1000),
    "元": ("CNY", 1),
    "%": ("PERCENT", 1),
    "工作日": ("WORKDAY", 1),
    "自然日": ("CALENDAR_DAY", 1),
    "日": ("CALENDAR_DAY", 1),
    "天": ("CALENDAR_DAY", 1),
    "小时": ("MINUTE", 60),
    "分钟": ("MINUTE", 1),
    "个月": ("MONTH", 1),
    "年": ("MONTH", 12),
    "人": ("PERSON", 1),
    "份": ("COPY", 1),
    "次": ("OCCURRENCE", 1),
    "个": ("ITEM", 1),
    "件": ("PIECE", 1),
}
MODALS = {
    "应当": "REQUIRED",
    "应": "REQUIRED",
    "必须": "REQUIRED",
    "须": "REQUIRED",
    "不得": "FORBIDDEN",
    "禁止": "FORBIDDEN",
    "不允许": "FORBIDDEN",
    "允许": "PERMITTED",
    "可以": "PERMITTED",
    "可": "PERMITTED",
    "无需": "OPTIONAL",
    "不必": "OPTIONAL",
}
ACTION_GROUPS = (
    ("提交", "报送", "报备", "报告", "递交", "上传", "送交"),
    ("缴纳", "支付", "发放", "收取", "领取", "承担"),
    ("申请", "办理", "登记", "设立"),
    ("公开", "公布", "公示", "发布"),
    ("提供", "接受", "出示", "交付"),
    ("使用", "查阅", "保存"),
    ("参加", "进入", "享受"),
    ("开展", "实施", "组织", "建立", "制定", "执行", "进行", "组成", "恢复"),
)
VERB_GROUP = {
    verb: index for index, group in enumerate(ACTION_GROUPS) for verb in group
}


def alternatives(items):
    return "|".join(map(re.escape, sorted(items, key=len, reverse=True)))


ACTIONS = alternatives(VERB_GROUP)
VERB = re.compile(ACTIONS)
NORMATIVE = re.compile(
    rf"(?P<subject>[\u3400-\u9fffA-Za-z0-9]{{1,64}}?)(?P<modal>{alternatives(MODALS)})(?P<not>不)?(?P<action>(?:{ACTIONS})[\u3400-\u9fffA-Za-z0-9]{{0,48}})"
)
ASSERTION = re.compile(
    rf"(?P<subject>[\u3400-\u9fffA-Za-z0-9]{{1,64}}?)(?P<not>不)?(?P<action>(?:{ACTIONS})[\u3400-\u9fffA-Za-z0-9]{{0,48}})"
)
NUMERIC = re.compile(
    rf"(?P<subject>[\u3400-\u9fffA-Za-z0-9]{{0,64}}?)(?P<attribute>{alternatives(ATTRIBUTES)})(?:应当|必须)?(?P<compare>{alternatives(COMPARATORS)})?(?P<value>.{{1,64}})"
)
NUMBER = r"(?:\d{1,12}(?:\.\d{1,8})?|[零〇一二三四五六七八九十百千万两]{1,12})"
QUANTITY = re.compile(
    rf"(?P<a>{NUMBER})(?P<ua>{alternatives(UNITS)})?(?:(?:至|到|~|～|—|-)(?P<b>{NUMBER})(?P<ub>{alternatives(UNITS)}))?(?P<suffix>及以上|及以下|以上|以下|以内)?"
)
DATE_VALUE = r"\d{4}(?:年|[-/.])\d{1,2}(?:月|[-/.])\d{1,2}日?"
CLOCK = r"\d{1,2}(?::\d{2}|[时点](?:\d{1,2}分)?)"
DATE_OR_TIME = rf"(?:{DATE_VALUE}(?:[Tt ]?{CLOCK})?|{CLOCK})"
DATE = re.compile(
    rf"(?P<a>{DATE_OR_TIME})(?:(?:至|到|~|～|—)(?P<b>{DATE_OR_TIME}))?(?P<suffix>以前|之前|以后|之后|前|后)?"
)
BLOCK_CONTEXT = re.compile(
    r"另有规定|除.{0,40}外|例外|特殊情况|废止|失效|过渡|修订前|修订后|调整前|调整后"
    r"|^\s*(?:原标题|旧标准|原文|引文|摘自|引用)[：:]"
)
CONTEXT = re.compile(
    r"另有规定|除.{0,40}外|例外|特殊情况|(?:原|旧)(?:规定|标准|办法)|废止|失效|过渡|试行|曾经|此前|现行标准调整|修订前|修订后|以往|调整前|调整后|原为|改为|《|》|依据|根据|参照|按照|引用|摘自|报道称"
)
AMBIGUOUS = re.compile(
    r"分别|其他|另行|一般|原则上|左右|约|视情况|之一|或者|同时|除非|如需|必要时|每(?:人|户|次|年|月)|该|上述|前述|以下(?!$)|同上|本项"
)
CONDITIONAL = re.compile(
    r"^(?P<scope>(?:(?:若|如|在|对于|针对|仅限|限于|符合|当|凡|自|从).{1,64}?|.{1,64}?(?:的|时|前|后|期间|阶段|情况下)))[,，]\s*(?P<statement>.+)$"
)
ACTION_DURATION = re.compile(
    rf"(?P<subject>[\u3400-\u9fffA-Za-z0-9]{{1,64}}?)(?:应当|必须|应|须)(?:在|自)"
    rf"(?P<trigger>[\u3400-\u9fff]{{1,24}}?(?:后|之日起))?"
    rf"(?P<value>{NUMBER}(?:工作日|自然日|日|天|小时|分钟|个月|年))"
    rf"(?P<boundary>以内|之内|之前|之后|内|前|后)(?P<action>(?:{ACTIONS})[\u3400-\u9fffA-Za-z0-9]{{0,48}})"
)
EFFECTIVE_DATE = re.compile(
    rf"(?P<subject>本(?:办法|条例|规定|通知|细则|规则|决定))(?:自|从)(?P<value>{DATE_VALUE})(?:起)?(?:施行|实施|生效)"
)
YEAR_SCOPE = re.compile(r"在?(\d{4})年(?:度|期间)?")
TIME_SCOPE = re.compile(r"(?:年|月|日|期间|阶段|季度|年度|起|前|后)")
MARKER = re.compile(r"应当|不得|必须|禁止|允许|不少于|不超过|\d|[元%]|期限|日期")
RAW_PATTERN = re.compile(
    r"[,，](?=.{1,64}?(?:应当|必须|不得|禁止|允许|为|是|不超过|不少于))"
)


@dataclass(frozen=True)
class Fact:
    subject: str
    action: str
    attribute: str
    kind: str
    interval: tuple[Decimal, bool, Decimal, bool] | None
    unit: str | None
    modality: str
    negation: bool
    condition: tuple[str, ...]
    time_scope: tuple[str, ...]
    excluded_context: str | None
    excerpt: str
    location: dict
    table_context: tuple[dict, ...] = ()

    @property
    def key(self):
        return self.subject, self.action, self.attribute

    @property
    def signature(self):
        return (
            self.key,
            self.unit,
            self.interval,
            self.modality,
            self.condition,
            self.time_scope,
            self.excluded_context,
        )

    def evidence(self):
        value = self.interval
        return {
            "subject": self.subject,
            "action": self.action,
            "attribute": self.attribute,
            "value": (
                [str(value[0]), value[1], str(value[2]), value[3]] if value else None
            ),
            "unit": self.unit,
            "modality": self.modality,
            "negation": self.negation,
            "condition": list(self.condition),
            "time_scope": list(self.time_scope),
            "excerpt": self.excerpt[:240],
            "source_span": self.location,
            "table_context": list(self.table_context),
        }


def _decimal(raw):
    if raw[0].isascii() and raw[0].isdigit():
        return Decimal(raw)
    if all(c in "零〇一二三四五六七八九两" for c in raw):
        return Decimal("".join(str(number_value(c)) for c in raw))
    return Decimal(number_value(raw))


def _date(raw):
    date_match = re.match(DATE_VALUE, raw)
    if date_match:
        ordinal = date(*map(int, re.findall(r"\d+", date_match.group()))).toordinal()
        rest = raw[date_match.end() :].lstrip(" Tt")
        if not rest:
            return "DATE_ORDINAL", Decimal(ordinal)
    else:
        ordinal = 0
        rest = raw
    hour, *minutes = map(int, re.findall(r"\d+", rest))
    wall_time = time(hour, minutes[0] if minutes else 0)
    unit = "DATE_MINUTE" if ordinal else "MINUTE_OF_DAY"
    return unit, Decimal(ordinal * 1440 + wall_time.hour * 60 + wall_time.minute)


def _constraint(raw, kind, comparator):
    raw = raw.strip()
    if kind == "DATE":
        match = DATE.fullmatch(raw)
        if not match:
            return None
        first, second, suffix = match.group("a", "b", "suffix")
        unit, a = _date(first)
        b = None
        if second:
            other_unit, b = _date(second)
            if unit != other_unit:
                return None
        if suffix:
            if second or (
                comparator != "="
                and comparator != ("<" if suffix.endswith("前") else ">")
            ):
                return None
            comparator = "<" if suffix.endswith("前") else ">"
    else:
        match = QUANTITY.fullmatch(raw)
        if not match:
            return None
        first, ua, second, ub, suffix = match.groups()
        if suffix:
            if second or comparator != "=":
                return None
            comparator = ">=" if suffix.endswith("以上") else "<="
        if not (ua or ub):
            return None
        unit, factor = UNITS[ua or ub]
        a = _decimal(first) * factor
        b = None
        if second:
            second_unit, second_factor = UNITS[ub or ua]
            if unit != second_unit:
                return None
            b = _decimal(second) * second_factor
        if (
            unit
            not in {
                "AMOUNT": {"CNY"},
                "RATIO": {"PERCENT"},
                "DURATION": {"WORKDAY", "CALENDAR_DAY", "MINUTE", "MONTH"},
                "QUANTITY": {"PERSON", "COPY", "OCCURRENCE", "ITEM", "PIECE"},
            }[kind]
        ):
            return None
        if kind == "RATIO" and (a > 100 or (b is not None and b > 100)):
            return None
    if b is not None:
        return (unit, (a, True, b, True)) if comparator == "=" and a <= b else None
    interval = {
        "=": (a, True, a, True),
        ">=": (a, True, Decimal("Infinity"), False),
        ">": (a, False, Decimal("Infinity"), False),
        "<=": (Decimal("-Infinity"), False, a, True),
        "<": (Decimal("-Infinity"), False, a, False),
    }[comparator]
    return unit, interval


def _segments(text):
    if CONDITIONAL.fullmatch(text):
        return [(0, text)]
    parts = list(RAW_PATTERN.finditer(text))
    start = 0
    segments = []
    for delimiter in parts:
        segments.append((start, text[start : delimiter.start()]))
        start = delimiter.end()
    return [*segments, (start, text[start:])]


def _fact(text, passage, start, *, synthesized=None, context=(), guarded=False):
    raw = unicodedata.normalize("NFKC", text).strip()
    excluded = (
        "REFERENCE_EXCEPTION_OR_TRANSITION" if guarded or CONTEXT.search(raw) else None
    )
    scope = passage.scope
    if match := CONDITIONAL.fullmatch(raw):
        scope = (*scope, match["scope"])
        raw = match["statement"]
    temporal = tuple(item for item in scope if TIME_SCOPE.search(item))
    conditions = tuple(item for item in scope if item not in temporal)
    raw = re.sub(rf"({DATE_VALUE})\s+(?={CLOCK})", r"\1T", raw)
    statement = synthesized or re.sub(r"\s+", "", raw)
    if AMBIGUOUS.search(statement):
        return None
    location = passage.location | {
        "normalized_start": passage.location["normalized_block_start"] + start,
        "normalized_end": passage.location["normalized_block_start"]
        + start
        + len(text),
    }
    evidence = (conditions, temporal, excluded, text.strip(), location, context)
    if match := NUMERIC.fullmatch(statement):
        subject, attribute, comparator, value = match.groups()
        if not subject:
            return None
        try:
            constraint = _constraint(
                value, ATTRIBUTES[attribute], COMPARATORS.get(comparator, "=")
            )
        except (ValueError, InvalidOperation):
            return None
        if constraint:
            unit, interval = constraint
            return Fact(
                subject,
                attribute,
                attribute,
                ATTRIBUTES[attribute],
                interval,
                unit,
                "CONSTRAINT",
                False,
                *evidence,
            )
    if match := EFFECTIVE_DATE.fullmatch(statement):
        try:
            unit, interval = _constraint(match["value"], "DATE", "=")
        except ValueError:
            return None
        return Fact(
            match["subject"],
            "施行日期",
            "施行日期",
            "DATE",
            interval,
            unit,
            "CONSTRAINT",
            False,
            *evidence,
        )
    if match := ACTION_DURATION.fullmatch(statement):
        subject, trigger, value, boundary, action = match.groups()
        if trigger and re.search(r"(?:该|上述|前述)", trigger):
            return None
        comparator = (
            ">" if boundary.endswith("后") else "<" if boundary.endswith("前") else "<="
        )
        unit, interval = _constraint(value, "DURATION", comparator)
        timing = (*temporal, trigger) if trigger else temporal
        return Fact(
            subject,
            action,
            "行为期限",
            "DURATION",
            interval,
            unit,
            "CONSTRAINT",
            False,
            conditions,
            timing,
            excluded,
            text.strip(),
            location,
            context,
        )
    if match := NORMATIVE.fullmatch(statement):
        subject, modal, negative, action = match.groups()
        if negative:
            # Obligatory non-performance is a prohibition; other double negatives are ambiguous.
            if MODALS[modal] != "REQUIRED":
                return None
            modal = "不得"
        if re.search(
            r"应当|必须|不得|禁止|允许|不予|不再|未|并且|或者", action
        ) or re.search(r"应当|必须|不得|禁止|可以|允许", subject):
            return None
        modality = MODALS[modal]
        return Fact(
            subject,
            action,
            "DEONTIC",
            "DEONTIC",
            None,
            None,
            modality,
            modality == "FORBIDDEN",
            *evidence,
        )
    if match := ASSERTION.fullmatch(statement):
        subject, negative, action = match.groups()
        # Require a recognizable explicit participant in unmodalized statements.
        if subject.endswith(("企业", "申请人", "单位", "居民", "个人", "机构", "部门")):
            return Fact(
                subject,
                action,
                "DEONTIC",
                "DEONTIC",
                None,
                None,
                "NEGATIVE" if negative else "AFFIRMATIVE",
                bool(negative),
                *evidence,
            )
    return None


def extract(passages):
    facts = []
    supported = set()
    unresolved = 0
    units = 0
    rows = defaultdict(dict)
    for passage in passages:
        if passage.table:
            table, row, col = passage.table
            rows[(table, row)].setdefault(col, []).append(passage)
    headers = {}
    for (table, row), columns in rows.items():
        if len(columns) >= 2 and all(
            len(cells) == 1 and cells[0].header for cells in columns.values()
        ):
            headers.setdefault(table, []).append((row, columns))
    for passage in passages:
        if passage.header:
            continue
        clause_guard = bool(
            BLOCK_CONTEXT.search(passage.text)
            or any(CONTEXT.search(scope) for scope in passage.scope)
        )
        for sentence in SENTENCE.finditer(passage.text):
            sentence_guard = clause_guard or bool(CONTEXT.search(sentence.group()))
            unresolved_prefix = False
            for relative, text in _segments(sentence.group()):
                if not text.strip():
                    continue
                units += 1
                if fact := _fact(
                    text,
                    passage,
                    sentence.start() + relative,
                    guarded=sentence_guard or unresolved_prefix,
                ):
                    facts.append(fact)
                    supported.add(passage.location["block_id"])
                else:
                    unresolved_prefix = True
                    unresolved += bool(MARKER.search(text))
        if not passage.table:
            continue
        table, row, col = passage.table
        columns = rows[(table, row)]
        header_rows = headers.get(table, [])
        if col == 1 or len(header_rows) != 1 or header_rows[0][0] >= row:
            continue
        header = header_rows[0][1]
        # Extra columns can encode year, beneficiary tier, or other conditions.
        if set(header) != {1, 2} or set(columns) != {1, 2}:
            continue
        if any(
            cell.location["table_has_spans"]
            for cells in columns.values()
            for cell in cells
        ):
            continue
        if not (
            {1, col} <= header.keys()
            and 1 in columns
            and len(columns[1]) == len(columns[col]) == 1
        ):
            continue
        label, subject = header[col][0], columns[1][0]
        if header[1][0].text.strip() not in {
            "对象",
            "适用对象",
            "项目",
            "事项",
            "名称",
        }:
            continue
        if label.text.strip() not in ATTRIBUTES or not re.fullmatch(
            r"[\u3400-\u9fffA-Za-z0-9]{1,48}", subject.text.strip()
        ):
            continue
        table_fact = _fact(
            passage.text,
            passage,
            0,
            synthesized=subject.text.strip()
            + label.text.strip()
            + "为"
            + unicodedata.normalize("NFKC", passage.text).strip(),
            context=(subject.location, label.location),
            guarded=bool(CONTEXT.search(subject.text)),
        )
        if table_fact:
            facts.append(table_fact)
            supported.add(passage.location["block_id"])
            unresolved -= bool(MARKER.search(passage.text))
    return facts, supported, units, max(0, unresolved)


def _overlap(a, b, discrete=False):
    low, high = max(a[0], b[0]), min(a[2], b[2])
    include_low = all(interval[1] for interval in (a, b) if interval[0] == low)
    include_high = all(interval[3] for interval in (a, b) if interval[2] == high)
    if discrete:
        low += int(not include_low)
        high -= int(not include_high)
        return low <= high
    return low < high or (low == high and include_low and include_high)


def compare(a: Fact, b: Fact):
    if a.excluded_context or b.excluded_context:
        return "UNKNOWN", "REFERENCE_EXCEPTION_OR_TRANSITION", None
    if a.subject != b.subject:
        return "UNKNOWN", "OBJECT_OR_ACTION_NOT_ALIGNED", None
    if (
        a.key == b.key
        and a.condition == b.condition
        and len(a.time_scope) == len(b.time_scope) == 1
    ):
        year_a, year_b = (
            YEAR_SCOPE.fullmatch(scope[0]) for scope in (a.time_scope, b.time_scope)
        )
        if year_a and year_b and year_a[1] != year_b[1]:
            return "NON_CONTRADICTION", "DISJOINT_YEAR_SCOPES", None
    if (a.condition, a.time_scope) != (b.condition, b.time_scope):
        return "UNKNOWN", "APPLICABILITY_NOT_ALIGNED", None
    if a.attribute != b.attribute:
        if a.kind != b.kind:
            return "NON_CONTRADICTION", "DISTINCT_CONSTRAINT_KINDS", None
        return "UNKNOWN", "ATTRIBUTE_NOT_ALIGNED", None
    if a.action != b.action:
        if (
            a.kind == "DEONTIC"
            and VERB_GROUP[VERB.match(a.action)[0]]
            != VERB_GROUP[VERB.match(b.action)[0]]
        ):
            return "NON_CONTRADICTION", "DISTINCT_ACTION_KINDS", None
        return "UNKNOWN", "OBJECT_OR_ACTION_NOT_ALIGNED", None
    if a.unit != b.unit:
        return "UNKNOWN", "UNITS_NOT_COMPARABLE", None
    if a.interval is not None and b.interval is not None:
        if _overlap(a.interval, b.interval, a.kind == "DATE"):
            return "NON_CONTRADICTION", "COMPATIBLE_INTERVALS", None
        severity = {
            "AMOUNT": "severe",
            "DURATION": "severe",
            "DATE": "severe",
            "RATIO": "general",
            "QUANTITY": "minor",
        }
        return "CONTRADICTION", "DISJOINT_INTERVALS", severity[a.kind]
    modalities = {a.modality, b.modality}
    if modalities == {"AFFIRMATIVE", "NEGATIVE"}:
        return "CONTRADICTION", "OPPOSITE_ASSERTIONS", "general"
    if modalities & {"AFFIRMATIVE", "NEGATIVE"} and len(modalities) > 1:
        return "UNKNOWN", "ASSERTION_AND_OBLIGATION", None
    if modalities == {"REQUIRED", "FORBIDDEN"}:
        return "CONTRADICTION", "REQUIRED_AND_FORBIDDEN", "severe"
    if modalities in ({"PERMITTED", "FORBIDDEN"}, {"REQUIRED", "OPTIONAL"}):
        return "CONTRADICTION", "INCOMPATIBLE_MODALITIES", "general"
    return "NON_CONTRADICTION", "COMPATIBLE_MODALITIES", None
