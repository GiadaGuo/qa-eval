"""Deterministic explicit-interference detection rules for text purity (02080106).

规则设计约束（对应指标规范【R】正则识别广告、乱码、特殊符号）：

- 每条规则有稳定 ``rule_id``、独立 ``rule_version``、可配置参数与命中证据；
- 规则只识别「显式干扰内容」，刻意避免将正常网址、英文术语、数字编号、
  代码片段等仅因形式特殊而判为污染；
- 所有判断确定性、可复现：同一规则版本 + 同一参数 + 同一文本 ⇒ 同一命中。

融合接口（为后续 Shared LLM Stage 预留，本模块不实现 LLM 调用）：

- :class:`PurityRuleHit` 携带 ``detection_source="RULE"``、``rule_id``、
  ``rule_version`` 与逐条命中证据，未来 Hybrid 计算器可按 ``sample_id``
  对 RULE 命中与语义（SEMANTIC）命中做并集融合，双方互不覆盖；
- 证据片段仅保留命中区间内容（截断上限 :data:`EXCERPT_LIMIT`），
  不外泄整段原文。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable

RULESET_VERSION = "1.0.0"
EXCERPT_LIMIT = 60
MAX_MATCH_SAMPLES = 5

# ---------------------------------------------------------------------------
# 乱码 / 异常字符
# ---------------------------------------------------------------------------

_REPLACEMENT_CHAR = "�"
# UTF-8 被按 Latin-1 误解码的典型 mojibake 片段，以及 Windows 未初始化内存
# 填充字（锟斤拷/烫烫烫/屯屯屯 是 GBK 环境下的经典乱码签名）
_MOJIBAKE_PATTERN = re.compile(
    r"[\u00c0-\u00ff][\u0080-\u00bf]{1,3}|锟斤拷|烫烫烫|屯屯屯"
)


def _is_garbled_char(char: str) -> bool:
    """未定义码位(Cn)、私用区(Co)、格式字符(Cf，含各类零宽字符)、
    控制字符(Cc，保留 \\t\\n\\r\\f\\v 等空白)与替换符 U+FFFD。"""

    if char == _REPLACEMENT_CHAR:
        return True
    category = unicodedata.category(char)
    if category in {"Cn", "Co", "Cf"}:
        return True
    return category == "Cc" and char not in "\t\n\r\f\v"


# ---------------------------------------------------------------------------
# 特殊符号堆积
# ---------------------------------------------------------------------------

# 常用于省略号/连字符/破折号的字符：允许在正文中成串出现，不计入堆积判定
_RUN_EXEMPT_CHARS = frozenset(".…=*-­–—－﹣·・_")


def _is_pileup_symbol_char(char: str) -> bool:
    """非汉字、非字母数字、非空白、且不属于省略/连接类符号的可见字符。"""

    if char.isspace() or char.isalnum() or char in _RUN_EXEMPT_CHARS:
        return False
    return not unicodedata.category(char).startswith("L")


def _is_decorative_symbol(char: str) -> bool:
    """非 ASCII 装饰性符号（So/Sm/Sk/Sc）：★→±￥ 等。

    密度统计刻意排除 ASCII 符号（``< = >`` 等常见于代码与 HTML 标签，
    由重复串规则与 HTML 残留规则负责），避免技术文本误报。
    """

    if ord(char) <= 0x7F:
        return False
    return unicodedata.category(char) in {"So", "Sm", "Sk", "Sc"}


# ---------------------------------------------------------------------------
# 页码 / 页眉页脚 / 网页家具残留
# ---------------------------------------------------------------------------

_CN_NUM = "0-9〇零一二三四五六七八九十百千万"
# 页码类残留必须独占一行（页眉/页脚/OCR 残迹的形态）；出现在句中的
# 「《文选》第79页」属于文献引用，不判定为污染。
DEFAULT_LAYOUT_RESIDUE_PATTERNS: tuple[str, ...] = (
    rf"^\s*(?:第\s*[{_CN_NUM}]+\s*页(?:\s*[,，]?\s*共\s*[{_CN_NUM}]+\s*页)?"
    rf"|共\s*[{_CN_NUM}]+\s*页)\s*$",
    r"(?<!\d)[-–—·]\s*\d{1,4}\s*[-–—·](?!\d)",
    r"[Pp]age\s+\d{1,4}(?:\s+of\s+\d{1,4})?",
    r"^\s*[-–—·.\s]*\d{1,4}[-–—·.\s]*$",
    r"版权所有.{0,40}?(?:保留所有权利|All\s+Rights\s+Reserved)",
    r"返回(?:顶部|列表|首页)|上一[页张篇]|下一[页张篇]",
)

# ---------------------------------------------------------------------------
# 显式广告 / 推广标记（高精度行动导向模式，非词面出现即命中）
# ---------------------------------------------------------------------------

DEFAULT_AD_MARKER_PATTERNS: tuple[str, ...] = (
    r"(?:加|添加)\s*微信[:：]?\s*[A-Za-z0-9_-]{4,}",
    r"微信(?:号|公众号)[:：]?\s*[A-Za-z0-9_-]{4,}",
    r"(?:扫码|扫描(?:上方)?二维码|长按识别(?:二维码)?)(?:关注|购买|领取|进群)",
    r"(?:点击|戳)(?:此处|这里|链接|下方).{0,12}(?:购买|下载|领取|报名|查看)",
    r"限时(?:特价|优惠|抢购|秒杀)",
    r"(?:优惠券|代金券|红包)(?:免费)?(?:领取|等您|等你|速抢)",
    r"(?:咨询|订购|报名|客服)(?:热线|电话)\s*[:：]?\s*[0-9\-]{5,}",
    r"广告合作[:：]|软文推广|商业推广(?:链接|信息)",
    r"(?:关注|订阅).{0,8}(?:公众号|官方帐号|官方账号|视频号|抖音号)",
    r"(?:免费)?领取.{0,6}(?:资料|课程|试用)",
)


def _compile(patterns: tuple[str, ...], flags: int = 0) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, flags) for pattern in patterns)


def _excerpt(text: str, start: int, end: int) -> str:
    return text[start:end][:EXCERPT_LIMIT]


@dataclass(frozen=True)
class RuleMatch:
    """一次规则命中的定位证据。"""

    start: int
    end: int
    excerpt: str


@dataclass(frozen=True)
class PurityRuleHit:
    """一条规则在单条记录上的命中结果。

    ``count`` 是该规则在此记录内的命中次数；同一记录命中多条规则时
    产生多个 :class:`PurityRuleHit`，聚合层负责按记录去重。
    """

    rule_id: str
    rule_version: str
    detection_source: str = "RULE"
    count: int = 0
    matches: list[RuleMatch] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_evidence_json(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "detection_source": self.detection_source,
            "count": self.count,
            "matches": [
                {"start": match.start, "end": match.end, "excerpt": match.excerpt}
                for match in self.matches
            ],
            "attributes": self.attributes,
        }


class PurityRule:
    """具备稳定 ID / 版本 / 参数 / 证据的确定性检测规则。"""

    def __init__(
        self,
        rule_id: str,
        rule_version: str,
        description: str,
        detect: Callable[[str], PurityRuleHit | None],
    ):
        self.rule_id = rule_id
        self.rule_version = rule_version
        self.description = description
        self._detect = detect

    def detect(self, text: str) -> PurityRuleHit | None:
        return self._detect(text)


@dataclass(frozen=True)
class PurityRuleConfig:
    """规则参数（由 Calculator 参数解析而来，均可配置）。"""

    garbled_char_ratio_threshold: float = 0.01
    symbol_run_length: int = 6
    symbol_density_threshold: float = 0.05
    min_symbol_count: int = 10
    html_tag_count_threshold: int = 3
    ad_marker_patterns: tuple[str, ...] = DEFAULT_AD_MARKER_PATTERNS
    layout_residue_patterns: tuple[str, ...] = DEFAULT_LAYOUT_RESIDUE_PATTERNS


def build_rules(config: PurityRuleConfig) -> list[PurityRule]:
    """按配置组装规则集；规则顺序稳定，保证证据顺序可复现。"""

    return [
        _garbled_char_rule(config),
        _symbol_pileup_rule(config),
        _html_residue_rule(config),
        _layout_residue_rule(config),
        _ad_marker_rule(config),
    ]


def rule_catalog() -> list[dict[str, str]]:
    return [
        {
            "rule_id": rule.rule_id,
            "rule_version": rule.rule_version,
            "description": rule.description,
        }
        for rule in build_rules(PurityRuleConfig())
    ]


# ---------------------------------------------------------------------------
# 规则实现
# ---------------------------------------------------------------------------


def _garbled_char_rule(config: PurityRuleConfig) -> PurityRule:
    threshold = config.garbled_char_ratio_threshold

    def detect(text: str) -> PurityRuleHit | None:
        positions = [
            index for index, char in enumerate(text) if _is_garbled_char(char)
        ]
        mojibake = list(_MOJIBAKE_PATTERN.finditer(text))
        total = sum(1 for char in text if not char.isspace())
        garbled = len(positions)
        ratio = garbled / total if total else 0.0
        if ratio < threshold and not mojibake:
            return None
        samples = [
            RuleMatch(position, position + 1, text[position])
            for position in positions[:MAX_MATCH_SAMPLES]
        ]
        samples.extend(
            RuleMatch(
                match.start(),
                match.end(),
                _excerpt(text, match.start(), match.end()),
            )
            for match in mojibake[: MAX_MATCH_SAMPLES - len(samples)]
        )
        return PurityRuleHit(
            rule_id="GARBLED_UNPRINTABLE_CHARS",
            rule_version="1.0.0",
            count=garbled + len(mojibake),
            matches=samples[:MAX_MATCH_SAMPLES],
            attributes={
                "garbled_char_count": garbled,
                "mojibake_sequence_count": len(mojibake),
                "garbled_char_ratio": round(ratio, 6),
                "garbled_char_ratio_threshold": threshold,
            },
        )

    return PurityRule(
        "GARBLED_UNPRINTABLE_CHARS",
        "1.0.0",
        "乱码与异常字符：未定义码位/私用区/控制字符/零宽字符/替换符/mojibake 序列",
        detect,
    )


def _symbol_pileup_rule(config: PurityRuleConfig) -> PurityRule:
    run_length = config.symbol_run_length
    density_threshold = config.symbol_density_threshold
    min_symbol_count = config.min_symbol_count
    run_pattern = re.compile(rf"(.)\1{{{run_length - 1},}}")

    def detect(text: str) -> PurityRuleHit | None:
        runs = [
            match
            for match in run_pattern.finditer(text)
            if _is_pileup_symbol_char(match.group(0)[0])
        ]
        total = sum(1 for char in text if not char.isspace())
        decorative = sum(1 for char in text if _is_decorative_symbol(char))
        density = decorative / total if total else 0.0
        density_hit = decorative >= min_symbol_count and density >= density_threshold
        if not runs and not density_hit:
            return None
        samples = [
            RuleMatch(
                match.start(),
                match.end(),
                _excerpt(text, match.start(), match.end()),
            )
            for match in runs[:MAX_MATCH_SAMPLES]
        ]
        return PurityRuleHit(
            rule_id="REPETITIVE_SYMBOL_PILEUP",
            rule_version="1.0.0",
            count=len(runs) + (1 if density_hit else 0),
            matches=samples,
            attributes={
                "symbol_run_count": len(runs),
                "max_symbol_run_length": max(
                    (len(match.group(0)) for match in runs), default=0
                ),
                "decorative_symbol_count": decorative,
                "decorative_symbol_density": round(density, 6),
                "symbol_run_length": run_length,
                "symbol_density_threshold": density_threshold,
            },
        )

    return PurityRule(
        "REPETITIVE_SYMBOL_PILEUP",
        "1.0.0",
        "特殊符号堆积：同一符号连续重复 ≥N 次，或装饰符号密度异常偏高",
        detect,
    )


def _html_residue_rule(config: PurityRuleConfig) -> PurityRule:
    threshold = config.html_tag_count_threshold
    tag_pattern = re.compile(r"</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?>")
    entity_pattern = re.compile(
        r"&(?:nbsp|amp|lt|gt|quot|apos|#\d{1,6}|#x[0-9a-fA-F]{1,6});"
    )

    def detect(text: str) -> PurityRuleHit | None:
        tags = list(tag_pattern.finditer(text))
        entities = list(entity_pattern.finditer(text))
        if len(tags) + len(entities) < threshold:
            return None
        tag_names: dict[str, int] = {}
        for match in tags:
            name = re.match(r"</?([A-Za-z][A-Za-z0-9]*)", match.group(0))
            key = f"<{name.group(1).lower()}>" if name else match.group(0)
            tag_names[key] = tag_names.get(key, 0) + 1
        samples = [
            RuleMatch(
                match.start(),
                match.end(),
                _excerpt(text, match.start(), match.end()),
            )
            for match in (tags + entities)[:MAX_MATCH_SAMPLES]
        ]
        return PurityRuleHit(
            rule_id="HTML_MARKUP_RESIDUE",
            rule_version="1.0.0",
            count=len(tags) + len(entities),
            matches=samples,
            attributes={
                "html_tag_count": len(tags),
                "html_entity_count": len(entities),
                "distinct_tag_counts": dict(
                    sorted(tag_names.items(), key=lambda item: -item[1])[:5]
                ),
                "html_tag_count_threshold": threshold,
            },
        )

    return PurityRule(
        "HTML_MARKUP_RESIDUE",
        "1.0.0",
        "HTML/XML 标签与实体残留：抓取清洗未去除的标记语言碎片",
        detect,
    )


def _layout_residue_rule(config: PurityRuleConfig) -> PurityRule:
    compiled = _compile(config.layout_residue_patterns, re.MULTILINE)

    def detect(text: str) -> PurityRuleHit | None:
        hits = [
            (pattern, match)
            for pattern in compiled
            for match in pattern.finditer(text)
        ]
        if not hits:
            return None
        samples = [
            RuleMatch(
                match.start(),
                match.end(),
                _excerpt(text, match.start(), match.end()),
            )
            for _, match in hits[:MAX_MATCH_SAMPLES]
        ]
        return PurityRuleHit(
            rule_id="LAYOUT_PAGE_RESIDUE",
            rule_version="1.0.0",
            count=len(hits),
            matches=samples,
            attributes={
                "matched_patterns": [pattern.pattern for pattern, _ in hits][
                    :MAX_MATCH_SAMPLES
                ],
            },
        )

    return PurityRule(
        "LAYOUT_PAGE_RESIDUE",
        "1.0.0",
        "页码、页眉页脚与网页导航等排版残留",
        detect,
    )


def _ad_marker_rule(config: PurityRuleConfig) -> PurityRule:
    compiled = _compile(config.ad_marker_patterns)

    def detect(text: str) -> PurityRuleHit | None:
        hits = [
            (pattern, match)
            for pattern in compiled
            for match in pattern.finditer(text)
        ]
        if not hits:
            return None
        samples = [
            RuleMatch(
                match.start(),
                match.end(),
                _excerpt(text, match.start(), match.end()),
            )
            for _, match in hits[:MAX_MATCH_SAMPLES]
        ]
        return PurityRuleHit(
            rule_id="EXPLICIT_ADVERTISING_MARKER",
            rule_version="1.0.0",
            count=len(hits),
            matches=samples,
            attributes={
                "matched_patterns": [pattern.pattern for pattern, _ in hits][
                    :MAX_MATCH_SAMPLES
                ],
            },
        )

    return PurityRule(
        "EXPLICIT_ADVERTISING_MARKER",
        "1.0.0",
        "显式广告/推广标记：行动导向的高精度推广模式",
        detect,
    )


def detect_all(text: str, rules: list[PurityRule]) -> list[PurityRuleHit]:
    """对单条文本执行全部规则，返回全部命中（可能为多条规则的并集）。"""

    return [hit for hit in (rule.detect(text) for rule in rules) if hit is not None]


__all__ = [
    "DEFAULT_AD_MARKER_PATTERNS",
    "DEFAULT_LAYOUT_RESIDUE_PATTERNS",
    "EXCERPT_LIMIT",
    "PurityRule",
    "PurityRuleConfig",
    "PurityRuleHit",
    "RULESET_VERSION",
    "RuleMatch",
    "build_rules",
    "detect_all",
    "rule_catalog",
]
