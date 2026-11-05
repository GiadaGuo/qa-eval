"""BG-02-01 政务网页 HTML 格式规范性检测 Calculator。

对 SampleRecord 2.0 中 ``article.body.html`` 等 HTML 正文做表示层格式检测：

- 六类规则：结构异常、字号离群、字体族混用、段落排版异常、连续空白块、
  表格结构异常、DOM 级非正文残留（详见 ``VIOLATION_CATALOG``）；
- 每条违规标注 ``confidence``：``CONFIRMED`` 为确定性判定（孤立结束标签、
  非法 rowspan/colspan 等），``SUSPECTED`` 为启发式疑似异常（字号偏离、
  残留识别等）。默认合规口径 ``CONFIRMED_ONLY`` 只把确定性异常计入
  ``compliant_record_ratio``，疑似异常单独统计，可用 ``compliance_basis``
  参数改为 ``CONFIRMED_AND_SUSPECTED``；
- 只解析内联 ``style`` 与表现型属性，不模拟浏览器最终渲染样式；外部
  CSS、``<style>`` 块、媒体查询等能力受限情况在诊断中计数，不伪造结论；
- 不覆盖 PDF/DOCX/OFD 物理分页（HTML 源码不含可靠分页信息）。

计算与 Quality Contract 阈值、规则判定、评分完全解耦；本 Calculator 只
产出指标 0201 已声明的测量值。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from qa_eval.calculators.government_html_format_rules import (
    HEADING_LIKE_TAGS,
    RESIDUE_CONTAINER_TAGS,
    RULESET_VERSION,
    Element,
    HtmlFormatParseError,
    HtmlFormatRuleConfig,
    StructureEvent,
    build_dom,
    dom_path,
    families_consistent,
    has_ancestor_element,
    has_ancestor_tag,
    is_blank_block,
    iter_elements,
    length_to_px,
    parse_font_family,
    parse_font_size,
    parse_font_tag_size,
    parse_line_height,
    parse_style_declarations,
    visible_text,
)
from qa_eval.calculators.protocol import (
    CalculatorContext,
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.contracts.base import JsonObject
from qa_eval.contracts.datasets import ContentItem, InlineTextPayload, SampleRecordV2

DEFAULT_HTML_ROLE = "article.body.html"
CHECK_KEYS = (
    "html_structure",
    "font",
    "paragraph_spacing",
    "empty_blocks",
    "table",
    "layout_residue",
)
COMPLIANCE_BASES = ("CONFIRMED_ONLY", "CONFIRMED_AND_SUSPECTED")
MAX_HTML_LENGTH_DEFAULT = 2_000_000
EVIDENCE_LIMIT_DEFAULT = 20
EVIDENCE_TEXT_LIMIT = 120
# 文档字号/字体族基线最少需要的已声明字符量；不足时不做相对离群判定
MIN_BASELINE_CHARS = 30
# 绝对字号离群 / 相对字号离群与字体族混用所需的最少正文字符量
MIN_ABSOLUTE_OUTLIER_CHARS = 4
MIN_RELATIVE_OUTLIER_CHARS = 10
MIN_FAMILY_OUTLIER_CHARS = 10

_CSS_KEYWORD_TOKENS = frozenset(
    {"auto", "inherit", "initial", "unset", "normal", "none"}
)
_MARGIN_PROPERTIES = (
    "margin",
    "margin-top",
    "margin-bottom",
    "margin-left",
    "margin-right",
)


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def _optional_string_set(parameters: JsonObject, key: str) -> frozenset[str] | None:
    value = parameters.get(key)
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise CalculatorValidationError(
            f"{key} must be a non-empty list of non-empty strings"
        )
    if len(value) != len(set(value)):
        raise CalculatorValidationError(f"{key} must contain unique strings")
    return frozenset(value)


def _number(
    parameters: JsonObject,
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float:
    value = parameters.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalculatorValidationError(f"{key} must be a number")
    if value < minimum:
        raise CalculatorValidationError(f"{key} must be at least {minimum}")
    if exclusive_minimum is not None and value <= exclusive_minimum:
        raise CalculatorValidationError(
            f"{key} must be greater than {exclusive_minimum}"
        )
    if maximum is not None and value > maximum:
        raise CalculatorValidationError(f"{key} must be at most {maximum}")
    return float(value)


def _integer(parameters: JsonObject, key: str, default: int, minimum: int) -> int:
    value = parameters.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CalculatorValidationError(f"{key} must be an integer")
    if value < minimum:
        raise CalculatorValidationError(f"{key} must be at least {minimum}")
    return value


def _enabled_checks(parameters: JsonObject) -> frozenset[str]:
    value = parameters.get("enabled_checks")
    if value is None:
        return frozenset(CHECK_KEYS)
    if not isinstance(value, list) or not value:
        raise CalculatorValidationError(
            "enabled_checks must be a non-empty list of check keys"
        )
    unknown = sorted({item for item in value if item not in CHECK_KEYS})
    if unknown:
        raise CalculatorValidationError(
            "enabled_checks references unknown check keys",
            unknown_checks=unknown,
            known_checks=list(CHECK_KEYS),
        )
    return frozenset(value)


# ---------------------------------------------------------------------------
# 内部违规构造
# ---------------------------------------------------------------------------


def _violation(
    code: str,
    *,
    severity: str,
    confidence: str,
    message: str,
    observed_value: str,
    expected_value: str,
    evidence_text: str,
    dom_path_value: str = "",
    source_offset: int | None = None,
    node: Element | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "violation_code": code,
        "severity": severity,
        "confidence": confidence,
        "message": message,
        "observed_value": str(observed_value)[:EVIDENCE_TEXT_LIMIT],
        "expected_value": str(expected_value)[:EVIDENCE_TEXT_LIMIT],
        "evidence_text": evidence_text[:EVIDENCE_TEXT_LIMIT],
        "dom_path": dom_path_value if node is None else dom_path(node),
        "rule_version": RULESET_VERSION,
        "coordinate_space": "source_html",
    }
    offset = (
        source_offset
        if source_offset is not None
        else (node.start_offset if node is not None else None)
    )
    if offset is not None and offset >= 0:
        row["source_offset"] = offset
        row["offset_semantics"] = (
            "zero-based Python Unicode character index of the start tag '<' "
            "in the raw HTML payload"
        )
    return row


def _nonws_chars(text: str) -> int:
    return sum(1 for char in text if not char.isspace())


# ---------------------------------------------------------------------------
# 有效样式解析（内联 style + 表现型属性，含基本继承）
# ---------------------------------------------------------------------------


class _ElementStyle:
    __slots__ = ("family", "family_owner", "size", "size_owner")

    def __init__(
        self,
        size: float | None,
        size_owner: Element | None,
        family: tuple[str, ...] | None,
        family_owner: Element | None,
    ) -> None:
        self.size = size
        self.size_owner = size_owner
        self.family = family
        self.family_owner = family_owner


def _root_font_size(root: Element) -> float | None:
    for node in iter_elements(root):
        if node.tag not in {"html", "body"}:
            continue
        declared = parse_style_declarations(node.attr("style")).get("font-size")
        resolved = parse_font_size(declared, parent_px=None, root_px=None)
        if resolved is not None:
            return resolved
    return None


def _resolve_styles(
    root: Element, css_stats: dict[str, int]
) -> dict[Element, _ElementStyle]:
    """计算每个元素的有效字号/字体族（基本 CSS 继承 + 子元素覆盖）。

    ``em``/``%`` 相对未知父字号时沿用继承值（近似），并计入能力受限
    计数；``rem`` 依赖根元素声明，未声明时不换算。
    """

    root_px = _root_font_size(root)
    styles: dict[Element, _ElementStyle] = {}
    stack: list[
        tuple[
            Element,
            float | None,
            Element | None,
            tuple[str, ...] | None,
            Element | None,
        ]
    ] = [(root, None, None, None, None)]
    while stack:
        node, size, size_owner, family, family_owner = stack.pop()
        if node is not root:
            declarations = parse_style_declarations(node.attr("style"))
            if declarations:
                if "font-size" in declarations:
                    resolved = parse_font_size(
                        declarations["font-size"], parent_px=size, root_px=root_px
                    )
                    if resolved is None:
                        css_stats["unresolved_font_size_declaration_count"] += 1
                    else:
                        size, size_owner = resolved, node
                elif "font" in declarations:
                    # font 简写（如 `font: 14px/1.5 仿宋`）不做完整解析
                    css_stats["font_shorthand_declaration_count"] += 1
                tokens = parse_font_family(declarations.get("font-family"))
                if tokens:
                    family, family_owner = tokens, node
            if node.tag == "font":
                tag_size = parse_font_tag_size(node.attr("size"))
                if tag_size is not None:
                    size, size_owner = tag_size, node
                face = parse_font_family(node.attr("face"))
                if face:
                    family, family_owner = face, node
            if node.attr("class") is not None or node.attr("id") is not None:
                css_stats["stylesheet_dependent_element_count"] += 1
        # 记录在应用自身声明之后：元素的有效样式含本元素的内联声明
        styles[node] = _ElementStyle(size, size_owner, family, family_owner)
        for child in reversed(node.children):
            if isinstance(child, Element):
                stack.append((child, size, size_owner, family, family_owner))
    return styles


def _heading_exempt(node: Element) -> bool:
    while node.parent is not None:
        if node.tag in HEADING_LIKE_TAGS:
            return True
        node = node.parent
    return False


# ---------------------------------------------------------------------------
# 规则实现
# ---------------------------------------------------------------------------


def _check_structure(events: list[StructureEvent]) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    for event in events:
        if event.kind == "STRAY_END_TAG":
            violations.append(
                _violation(
                    "HTML_STRUCTURE_ANOMALY",
                    severity="ERROR",
                    confidence="CONFIRMED",
                    dom_path_value=event.dom_path,
                    observed_value=event.raw_tag_text,
                    expected_value="end tag with a matching open element",
                    message=f"孤立结束标签 {event.raw_tag_text} 没有对应的起始标签",
                    evidence_text=event.raw_tag_text,
                    source_offset=event.source_offset,
                )
            )
        elif event.kind == "IMPLICIT_CLOSE":
            closed = "、".join(f"<{tag}>" for tag in event.closed_tags)
            violations.append(
                _violation(
                    "HTML_STRUCTURE_ANOMALY",
                    severity="WARNING",
                    confidence="SUSPECTED",
                    dom_path_value=event.dom_path,
                    observed_value=f"{event.raw_tag_text} implicitly closed {closed}",
                    expected_value="explicit end tags for non-optional elements",
                    message=(
                        f"{event.raw_tag_text} 隐式闭合了要求显式闭合的元素 "
                        f"{closed}（解析器已按容错规则修复）"
                    ),
                    evidence_text=event.raw_tag_text,
                    source_offset=event.source_offset,
                )
            )
        else:  # UNCLOSED_AT_EOF
            violations.append(
                _violation(
                    "HTML_STRUCTURE_ANOMALY",
                    severity="WARNING",
                    confidence="CONFIRMED",
                    dom_path_value=event.dom_path,
                    observed_value=f"<{event.tag}> left open at end of document",
                    expected_value="block containers closed before end of document",
                    message=f"块级容器 <{event.tag}> 在文档结束处仍未闭合",
                    evidence_text=event.raw_tag_text,
                    source_offset=event.source_offset,
                )
            )
    return violations


def _check_fonts(
    root: Element,
    styles: dict[Element, _ElementStyle],
    config: HtmlFormatRuleConfig,
    css_stats: dict[str, int],
) -> list[dict[str, Any]]:
    """字号离群与字体族混用（启发式疑似异常，标题类容器豁免）。

    基线取「已声明字号的非标题文本」中按字符量占比不低于
    ``font_size_baseline_dominance`` 的主流字号；父级 14px / 子级 21px
    这类合法覆盖（1.5x）与短文本（落款、日期）不会触发相对离群。
    """

    element_chars: dict[Element, int] = {}
    for node in iter_elements(root):
        if node is root:
            continue
        chars = sum(
            _nonws_chars(chunk) for chunk in node.children if isinstance(chunk, str)
        )
        if chars:
            element_chars[node] = chars

    size_volume: dict[float, int] = {}
    known_size_chars = 0
    undeclared_chars = 0
    for node, chars in element_chars.items():
        style = styles[node]
        if style.size is None:
            undeclared_chars += chars
        elif not _heading_exempt(node):
            key = round(style.size, 2)
            size_volume[key] = size_volume.get(key, 0) + chars
            known_size_chars += chars
    css_stats["known_font_size_char_count"] += known_size_chars
    css_stats["undeclared_font_size_char_count"] += undeclared_chars

    baseline: float | None = None
    if known_size_chars >= MIN_BASELINE_CHARS and size_volume:
        dominant_size, dominant_chars = max(
            size_volume.items(), key=lambda item: (item[1], item[0])
        )
        if dominant_chars / known_size_chars >= config.font_size_baseline_dominance:
            baseline = dominant_size

    violations: list[dict[str, Any]] = []
    size_groups: dict[Element, dict[str, Any]] = {}
    for node in iter_elements(root):
        chars = element_chars.get(node)
        if not chars or _heading_exempt(node):
            continue
        style = styles[node]
        if style.size is None:
            continue
        owner = style.size_owner or node
        group = size_groups.setdefault(owner, {"chars": 0, "size": style.size})
        group["chars"] += chars
    for owner in sorted(size_groups, key=dom_path):
        group = size_groups[owner]
        size = group["size"]
        chars = group["chars"]
        if size <= 0:
            violations.append(
                _violation(
                    "FONT_SIZE_OUTLIER",
                    severity="ERROR",
                    confidence="CONFIRMED",
                    node=owner,
                    observed_value=f"{size:g}px",
                    expected_value="font-size greater than 0",
                    message=f"字号声明为 {size:g}px，文本将不可见",
                    evidence_text=owner.raw_tag_text or f"<{owner.tag}>",
                )
            )
            continue
        if chars >= MIN_ABSOLUTE_OUTLIER_CHARS and (
            size < config.font_size_min_px or size > config.font_size_max_px
        ):
            violations.append(
                _violation(
                    "FONT_SIZE_OUTLIER",
                    severity="WARNING",
                    confidence="SUSPECTED",
                    node=owner,
                    observed_value=f"{size:g}px",
                    expected_value=(
                        f"at least {config.font_size_min_px:g}px and at most "
                        f"{config.font_size_max_px:g}px"
                    ),
                    message="局部正文字号绝对值明显异常（极小或极大），可能影响正文阅读",
                    evidence_text=owner.raw_tag_text or f"<{owner.tag}>",
                )
            )
            continue
        if (
            baseline is not None
            and chars >= MIN_RELATIVE_OUTLIER_CHARS
            and (
                size >= baseline * config.font_size_outlier_scale
                or size <= baseline / config.font_size_outlier_scale
            )
        ):
            violations.append(
                _violation(
                    "FONT_SIZE_OUTLIER",
                    severity="WARNING",
                    confidence="SUSPECTED",
                    node=owner,
                    observed_value=f"{size:g}px",
                    expected_value=f"document body baseline {baseline:g}px",
                    message="局部正文字号明显偏离文档主体字号",
                    evidence_text=owner.raw_tag_text or f"<{owner.tag}>",
                )
            )

    family_groups: dict[Element, dict[str, Any]] = {}
    known_family_chars = 0
    for node in iter_elements(root):
        chars = element_chars.get(node)
        if not chars or _heading_exempt(node):
            continue
        style = styles[node]
        if not style.family:
            continue
        owner = style.family_owner or node
        group = family_groups.setdefault(owner, {"chars": 0, "family": style.family})
        group["chars"] += chars
        known_family_chars += chars
    css_stats["known_font_family_char_count"] += known_family_chars
    if len(family_groups) >= 2 and known_family_chars >= MIN_BASELINE_CHARS:
        dominant_owner, dominant_group = max(
            family_groups.items(),
            key=lambda item: (item[1]["chars"], dom_path(item[0])),
        )
        if (
            dominant_group["chars"] / known_family_chars
            >= config.font_size_baseline_dominance
        ):
            baseline_family = dominant_group["family"]
            deviating = {
                owner: group
                for owner, group in family_groups.items()
                if owner is not dominant_owner
                and not families_consistent(group["family"], baseline_family)
            }
            deviating_chars = sum(group["chars"] for group in deviating.values())
            if deviating_chars >= max(
                MIN_FAMILY_OUTLIER_CHARS,
                config.font_family_minority_ratio * known_family_chars,
            ):
                for owner in sorted(deviating, key=dom_path):
                    group = deviating[owner]
                    if group["chars"] < MIN_FAMILY_OUTLIER_CHARS:
                        continue
                    violations.append(
                        _violation(
                            "FONT_FAMILY_INCONSISTENCY",
                            severity="WARNING",
                            confidence="SUSPECTED",
                            node=owner,
                            observed_value="/".join(group["family"][:3]),
                            expected_value=f"document dominant family {baseline_family[0]}",
                            message="同类正文中存在明显偏离主流字体族的文本块",
                            evidence_text=owner.raw_tag_text or f"<{owner.tag}>",
                        )
                    )
    return violations


def _parse_margin_values(
    declaration: str, *, font_px: float | None, css_stats: dict[str, int]
) -> list[float] | None:
    values: list[float] = []
    for token in declaration.split():
        if token.lower() in _CSS_KEYWORD_TOKENS:
            continue
        resolved = length_to_px(token, reference_px=font_px)
        if resolved is None:
            css_stats["unresolved_css_length_count"] += 1
            return None
        values.append(resolved)
    return values


def _check_spacing(
    root: Element,
    styles: dict[Element, _ElementStyle],
    config: HtmlFormatRuleConfig,
    css_stats: dict[str, int],
) -> list[dict[str, Any]]:
    """极端行高、异常缩进、负边距与极端段间距（只报明显极端值）。

    正常 ``text-align: justify``、落款右对齐与常见首行缩进不会触发；
    无法换算的单位（vw/ex 等）计入能力受限计数后跳过，不做跨单位比较。
    """

    violations: list[dict[str, Any]] = []
    for node in iter_elements(root):
        if node is root:
            continue
        declarations = parse_style_declarations(node.attr("style"))
        if not declarations:
            continue
        font_px = styles[node].size
        hit: tuple[str, str, str] | None = None

        line_height = declarations.get("line-height")
        if line_height is not None and line_height.lower() not in _CSS_KEYWORD_TOKENS:
            resolved = parse_line_height(line_height, font_px=font_px)
            if resolved is None:
                css_stats["unresolved_css_length_count"] += 1
            elif (
                resolved < config.line_height_min_px
                or resolved > config.line_height_max_px
            ):
                hit = (
                    f"line-height:{line_height}≈{resolved:g}px",
                    f"between {config.line_height_min_px:g}px and {config.line_height_max_px:g}px",
                    "行高明显异常，可能影响正文阅读",
                )
            elif (
                font_px is not None
                and font_px > 0
                and resolved / font_px > config.line_height_font_ratio_max
            ):
                hit = (
                    f"line-height:{line_height}≈{resolved:g}px at font-size {font_px:g}px",
                    f"at most {config.line_height_font_ratio_max:g}x font-size",
                    "行高相对字号明显过大",
                )

        indent = declarations.get("text-indent")
        if hit is None and indent is not None:
            resolved = length_to_px(indent, reference_px=font_px)
            if resolved is None:
                css_stats["unresolved_css_length_count"] += 1
            elif resolved < 0 and abs(resolved) > config.negative_margin_tolerance_px:
                hit = (
                    f"text-indent:{indent}≈{resolved:g}px",
                    f"negative magnitude at most {config.negative_margin_tolerance_px:g}px",
                    "段落缩进为明显异常的负值",
                )
            elif resolved > 0 and (
                (font_px is not None and resolved > config.text_indent_max_em * font_px)
                or (font_px is None and resolved > config.text_indent_max_px)
            ):
                hit = (
                    f"text-indent:{indent}≈{resolved:g}px",
                    f"at most {config.text_indent_max_em:g}em of font-size",
                    "段落缩进明显异常过大",
                )

        if hit is None:
            for prop in _MARGIN_PROPERTIES:
                declaration = declarations.get(prop)
                if declaration is None:
                    continue
                values = _parse_margin_values(
                    declaration, font_px=font_px, css_stats=css_stats
                )
                if values is None:
                    continue
                negative = min(values, default=0.0)
                if negative < 0 and abs(negative) > config.negative_margin_tolerance_px:
                    hit = (
                        f"{prop}:{declaration}≈{negative:g}px",
                        f"negative magnitude at most {config.negative_margin_tolerance_px:g}px",
                        "存在明显不合理的负边距",
                    )
                    break
                if prop in {"margin", "margin-top", "margin-bottom"}:
                    extreme = max(values, default=0.0)
                    if extreme > config.paragraph_margin_max_px:
                        hit = (
                            f"{prop}:{declaration}≈{extreme:g}px",
                            f"vertical margin at most {config.paragraph_margin_max_px:g}px",
                            "段前段后间距极端异常",
                        )
                        break

        if hit is not None:
            observed, expected, message = hit
            violations.append(
                _violation(
                    "PARAGRAPH_SPACING_ANOMALY",
                    severity="WARNING",
                    confidence="SUSPECTED",
                    node=node,
                    observed_value=observed,
                    expected_value=expected,
                    message=message,
                    evidence_text=node.raw_tag_text or f"<{node.tag}>",
                )
            )
    return violations


def _consecutive_run(
    children: list[Element | str], start: int, matches
) -> tuple[int, int]:
    """从 ``start`` 起统计满足条件的连续元素数；允许其间穿插纯空白文本。

    返回 (连续命中数量, 扫描结束位置)；结束位置指向首个打破连续性的子节点。
    """

    run = 0
    index = start
    while index < len(children):
        child = children[index]
        if isinstance(child, str):
            if not child.strip():
                index += 1
                continue
            break
        if isinstance(child, Element) and matches(child):
            run += 1
            index += 1
            continue
        break
    return run, index


def _check_empty_blocks(
    root: Element, config: HtmlFormatRuleConfig
) -> list[dict[str, Any]]:
    """连续空段落 / 连续 <br> / 仅空白字符的连续块（阈值可配置）。

    单个空段落、落款前少量留白与正常 ``&nbsp;`` 不触发；判定基于
    Entity 解码后的可见内容（``&nbsp;`` 解码为不可见的窄空白）。
    """

    violations: list[dict[str, Any]] = []
    for parent in iter_elements(root):
        children = parent.children
        index = 0
        while index < len(children):
            child = children[index]
            if (
                isinstance(child, Element)
                and child.tag in {"p", "div"}
                and is_blank_block(child)
            ):
                run, end = _consecutive_run(
                    children,
                    index,
                    lambda node: node.tag in {"p", "div"} and is_blank_block(node),
                )
                if run >= config.consecutive_empty_block_threshold:
                    violations.append(
                        _violation(
                            "EXCESSIVE_EMPTY_BLOCKS",
                            severity="WARNING",
                            confidence="CONFIRMED",
                            node=child,
                            observed_value=f"{run} consecutive blank blocks",
                            expected_value=(
                                f"fewer than {config.consecutive_empty_block_threshold} "
                                "consecutive blank blocks"
                            ),
                            message=f"存在 {run} 个连续空白块，明显影响正文阅读",
                            evidence_text=child.raw_tag_text or f"<{child.tag}>",
                        )
                    )
                index = max(end, index + 1)
            else:
                index += 1

        index = 0
        while index < len(children):
            child = children[index]
            if isinstance(child, Element) and child.tag == "br":
                run, end = _consecutive_run(
                    children, index, lambda node: node.tag == "br"
                )
                if run >= config.consecutive_br_threshold:
                    violations.append(
                        _violation(
                            "EXCESSIVE_EMPTY_BLOCKS",
                            severity="WARNING",
                            confidence="CONFIRMED",
                            node=child,
                            observed_value=f"{run} consecutive <br>",
                            expected_value=(
                                f"fewer than {config.consecutive_br_threshold} "
                                "consecutive <br>"
                            ),
                            message=f"存在 {run} 个连续 <br>，明显影响正文阅读",
                            evidence_text=child.raw_tag_text or "<br>",
                        )
                    )
                index = max(end, index + 1)
            else:
                index += 1
    return violations


def _check_table_spans(
    node: Element, config: HtmlFormatRuleConfig
) -> tuple[str, str, str] | None:
    colspan = node.attr("colspan")
    if colspan is not None:
        colspan = colspan.strip()
        if not colspan.isdigit() or not 1 <= int(colspan) <= config.max_colspan:
            return (
                "colspan",
                f'colspan="{colspan}"',
                f"integer between 1 and {config.max_colspan}",
            )
    rowspan = node.attr("rowspan")
    if rowspan is not None:
        rowspan = rowspan.strip()
        if not rowspan.isdigit() or int(rowspan) > config.max_rowspan:
            return (
                "rowspan",
                f'rowspan="{rowspan}"',
                f"integer between 0 and {config.max_rowspan}",
            )
    return None


def _check_tables(
    root: Element, config: HtmlFormatRuleConfig, css_stats: dict[str, int]
) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    tables = [node for node in iter_elements(root) if node.tag == "table"]

    for node in iter_elements(root):
        if node.tag in {"tr", "td", "th"}:
            if not has_ancestor_tag(node, "table"):
                violations.append(
                    _violation(
                        "TABLE_STRUCTURE_ANOMALY",
                        severity="ERROR",
                        confidence="CONFIRMED",
                        node=node,
                        observed_value=f"<{node.tag}> outside <table>",
                        expected_value="table elements inside a <table> context",
                        message=f"表格元素 <{node.tag}> 脱离了 <table> 上下文",
                        evidence_text=node.raw_tag_text or f"<{node.tag}>",
                    )
                )
            elif (
                node.tag == "tr"
                and node.parent is not None
                and node.parent.tag
                not in {
                    "table",
                    "thead",
                    "tbody",
                    "tfoot",
                    "template",
                }
            ):
                violations.append(
                    _violation(
                        "TABLE_STRUCTURE_ANOMALY",
                        severity="WARNING",
                        confidence="SUSPECTED",
                        node=node,
                        observed_value=f"<tr> under <{node.parent.tag}>",
                        expected_value="<tr> inside table/thead/tbody/tfoot",
                        message="<tr> 未位于表格分区内，依赖解析器容错修复",
                        evidence_text=node.raw_tag_text or "<tr>",
                    )
                )
            elif (
                node.tag in {"td", "th"}
                and node.parent is not None
                and node.parent.tag != "tr"
            ):
                violations.append(
                    _violation(
                        "TABLE_STRUCTURE_ANOMALY",
                        severity="WARNING",
                        confidence="SUSPECTED",
                        node=node,
                        observed_value=f"<{node.tag}> under <{node.parent.tag}>",
                        expected_value="<td>/<th> inside <tr>",
                        message=f"<{node.tag}> 不在 <tr> 内，依赖解析器隐式补全行",
                        evidence_text=node.raw_tag_text or f"<{node.tag}>",
                    )
                )
        if node.tag in {"td", "th"}:
            invalid = _check_table_spans(node, config)
            if invalid is not None:
                prop, observed, expected = invalid
                violations.append(
                    _violation(
                        "TABLE_STRUCTURE_ANOMALY",
                        severity="ERROR",
                        confidence="CONFIRMED",
                        node=node,
                        observed_value=observed,
                        expected_value=expected,
                        message=f"表格单元格的 {prop} 取值非法",
                        evidence_text=node.raw_tag_text or f"<{node.tag}>",
                    )
                )

    for table in tables:
        cells = [
            node
            for node in iter_elements(table)
            if node is not table and node.tag in {"td", "th"}
        ]
        if not cells:
            violations.append(
                _violation(
                    "TABLE_STRUCTURE_ANOMALY",
                    severity="WARNING",
                    confidence="CONFIRMED",
                    node=table,
                    observed_value="table without any cell",
                    expected_value="at least one row with cells",
                    message="表格缺少有效行列，为空表格",
                    evidence_text=table.raw_tag_text or "<table>",
                )
            )
            continue
        rows = [
            node
            for node in table.children
            if isinstance(node, Element) and node.tag == "tr"
        ]
        for section in table.children:
            if isinstance(section, Element) and section.tag in {
                "thead",
                "tbody",
                "tfoot",
            }:
                rows.extend(
                    child
                    for child in section.children
                    if isinstance(child, Element) and child.tag == "tr"
                )
        if len(rows) < 2:
            continue
        if any(node.attr("rowspan") for node in cells):
            # 含 rowspan 的复杂表格不做行跨度一致性判定（能力受限）
            css_stats["tables_with_rowspan_skipped"] += 1
            continue
        widths = set()
        for row in rows:
            total = 0
            for cell in row.children:
                if not (isinstance(cell, Element) and cell.tag in {"td", "th"}):
                    continue
                span = (cell.attr("colspan") or "1").strip()
                total += int(span) if span.isdigit() else 1
            widths.add(total)
        if len(widths) > 1:
            violations.append(
                _violation(
                    "TABLE_STRUCTURE_ANOMALY",
                    severity="WARNING",
                    confidence="SUSPECTED",
                    node=table,
                    observed_value=f"row cell-span totals {sorted(widths)}",
                    expected_value="consistent row cell-span totals",
                    message="各行单元格跨度合计不一致，存在可识别的行列结构冲突",
                    evidence_text=table.raw_tag_text or "<table>",
                )
            )
    return violations


def _residue_signature(node: Element) -> str:
    parts = [node.tag]
    if identifier := node.attr("id"):
        parts.append(f"#{identifier.lower()}")
    for cls in (node.attr("class") or "").lower().split():
        parts.append(f".{cls}")
    if role := node.attr("role"):
        parts.append(f"role={role.lower()}")
    if label := node.attr("aria-label"):
        parts.append(f"aria={label.lower()}")
    return " ".join(parts)


def _check_residue(
    root: Element, patterns: tuple[re.Pattern[str], ...]
) -> list[dict[str, Any]]:
    """DOM 结构层面的非正文残留（导航、面包屑、打印工具栏、网站页脚等）。

    不能仅凭 ``footer``/``nav`` 单标签判异常：无特征命中时，``nav`` 需
    含链接列表，``footer`` 需链接文本占主体才判定。正文中的合法落款、
    发布日期（通常位于 <p>）与引用链接不进入本检测。
    """

    violations: list[dict[str, Any]] = []
    reported: list[Element] = []
    for node in iter_elements(root):
        if node.tag not in RESIDUE_CONTAINER_TAGS:
            continue
        if any(has_ancestor_element(node, item) for item in reported):
            continue
        signature = _residue_signature(node)
        matched = next(
            (pattern.pattern for pattern in patterns if pattern.search(signature)),
            None,
        )
        anchors = sum(
            1
            for descendant in iter_elements(node)
            if descendant is not node and descendant.tag == "a"
        )
        if matched is not None:
            observed = f"{node.tag} matching /{matched}/"
        else:
            role = (node.attr("role") or "").lower()
            aria = (node.attr("aria-label") or "").lower()
            if node.tag == "nav" or role == "navigation" or "导航" in aria:
                if anchors < 3:
                    continue
                observed = f"nav container with {anchors} links"
            elif node.tag == "footer":
                text_chars = _nonws_chars(visible_text(node))
                link_chars = sum(
                    _nonws_chars(visible_text(descendant))
                    for descendant in iter_elements(node)
                    if descendant is not node and descendant.tag == "a"
                )
                if anchors < 3 or not text_chars or link_chars / text_chars < 0.5:
                    continue
                observed = f"footer container dominated by {anchors} links"
            else:
                continue
        reported.append(node)
        violations.append(
            _violation(
                "HTML_LAYOUT_RESIDUE",
                severity="WARNING",
                confidence="SUSPECTED",
                node=node,
                observed_value=observed,
                expected_value="no page-furniture containers in article body HTML",
                message="检测到疑似网页模板/导航/页脚等非正文 DOM 残留",
                evidence_text=signature[:EVIDENCE_TEXT_LIMIT],
            )
        )
    return violations


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------


class GovernmentHtmlFormatComplianceCalculator:
    """BG-02-01：政务网页 HTML 格式规范性检测（指标 0201 的独立 Calculator）。"""

    calculator_key = "government_html_format_compliance"
    version = "1.0.0"

    def source_paths(self) -> tuple[Path, ...]:
        root = Path(__file__).resolve().parent
        return (Path(__file__), root / "government_html_format_rules.py")

    def validate(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> list[JsonObject]:
        if context.input_schema_ref != "2.0":
            raise CalculatorValidationError(
                "government_html_format_compliance requires SampleRecord 2.0 input",
                input_schema_ref=context.input_schema_ref,
            )
        if not context.input_path.is_file():
            raise CalculatorValidationError(
                "normalized input artifact is unavailable",
                input_path=str(context.input_path),
            )
        _optional_string_set(parameters, "include_roles")
        _optional_string_set(parameters, "exclude_roles")
        _optional_string_set(parameters, "include_content_ids")
        _enabled_checks(parameters)
        basis = parameters.get("compliance_basis", COMPLIANCE_BASES[0])
        if basis not in COMPLIANCE_BASES:
            raise CalculatorValidationError(
                "compliance_basis must be CONFIRMED_ONLY or CONFIRMED_AND_SUSPECTED"
            )
        config = self._rule_config(parameters)
        for pattern in config.layout_residue_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise CalculatorValidationError(
                    "layout_residue_patterns contains an invalid regular expression",
                    pattern=pattern,
                    error=str(exc),
                ) from exc
        _integer(parameters, "max_html_length_chars", MAX_HTML_LENGTH_DEFAULT, 1000)
        _integer(parameters, "max_evidence_per_record", EVIDENCE_LIMIT_DEFAULT, 1)
        return []

    def execute(
        self, context: CalculatorContext, parameters: JsonObject
    ) -> CalculatorOutput:
        diagnostics = self.validate(context, parameters)
        include_roles = _optional_string_set(parameters, "include_roles") or frozenset(
            {DEFAULT_HTML_ROLE}
        )
        exclude_roles = _optional_string_set(parameters, "exclude_roles") or frozenset()
        include_ids = _optional_string_set(parameters, "include_content_ids")
        checks = _enabled_checks(parameters)
        basis = str(parameters.get("compliance_basis", COMPLIANCE_BASES[0]))
        config = self._rule_config(parameters)
        residue_patterns = tuple(
            re.compile(pattern) for pattern in config.layout_residue_patterns
        )
        max_chars = _integer(
            parameters, "max_html_length_chars", MAX_HTML_LENGTH_DEFAULT, 1000
        )
        evidence_limit = _integer(
            parameters, "max_evidence_per_record", EVIDENCE_LIMIT_DEFAULT, 1
        )

        input_count = eligible_records = scanned_contents = 0
        affected_records = confirmed_records = suspected_records = 0
        violation_count = 0
        code_counts: Counter[str] = Counter()
        skipped: Counter[str] = Counter()
        record_exclusions: Counter[str] = Counter()
        css_stats: dict[str, int] = {
            "unresolved_font_size_declaration_count": 0,
            "font_shorthand_declaration_count": 0,
            "stylesheet_dependent_element_count": 0,
            "unresolved_css_length_count": 0,
            "known_font_size_char_count": 0,
            "undeclared_font_size_char_count": 0,
            "known_font_family_char_count": 0,
            "tables_with_rowspan_skipped": 0,
        }

        context.output_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = context.output_dir / "government-html-format-violations.jsonl"
        evidence_rows = 0

        with (
            context.input_path.open(encoding="utf-8") as input_file,
            evidence_path.open("w", encoding="utf-8") as evidence_file,
        ):
            for line_number, raw_line in enumerate(input_file, 1):
                if not raw_line.strip():
                    continue
                input_count += 1
                try:
                    sample = SampleRecordV2.model_validate_json(raw_line)
                except ValidationError as exc:
                    raise CalculatorValidationError(
                        "normalized input contains an invalid SampleRecord",
                        line_number=line_number,
                    ) from exc

                record_eligible = False
                record_confirmed = False
                record_suspected = False
                record_skips: set[str] = set()
                emitted_for_record = 0

                for item in sample.contents:
                    reason = self._skip_reason(
                        item, include_roles, exclude_roles, include_ids
                    )
                    if reason is not None:
                        skipped[reason] += 1
                        record_skips.add(reason)
                        continue
                    assert isinstance(item.payload, InlineTextPayload)
                    text = item.payload.text
                    if not text.strip():
                        skipped["EMPTY_HTML"] += 1
                        record_skips.add("EMPTY_HTML")
                        continue
                    if len(text) > max_chars:
                        # 资源保护：超长 HTML 跳过并显式计数，不静默丢弃
                        skipped["HTML_TOO_LARGE"] += 1
                        record_skips.add("HTML_TOO_LARGE")
                        continue
                    try:
                        root, events = build_dom(text)
                    except HtmlFormatParseError:
                        skipped["HTML_PARSE_FAILED"] += 1
                        record_skips.add("HTML_PARSE_FAILED")
                        continue
                    record_eligible = True
                    scanned_contents += 1

                    violations: list[dict[str, Any]] = []
                    if "html_structure" in checks:
                        violations.extend(_check_structure(events))
                    if "table" in checks:
                        violations.extend(_check_tables(root, config, css_stats))
                    if "empty_blocks" in checks:
                        violations.extend(_check_empty_blocks(root, config))
                    if "font" in checks or "paragraph_spacing" in checks:
                        styles = _resolve_styles(root, css_stats)
                        if "font" in checks:
                            violations.extend(
                                _check_fonts(root, styles, config, css_stats)
                            )
                        if "paragraph_spacing" in checks:
                            violations.extend(
                                _check_spacing(root, styles, config, css_stats)
                            )
                    if "layout_residue" in checks:
                        violations.extend(_check_residue(root, residue_patterns))

                    if not violations:
                        continue
                    if any(row["confidence"] == "CONFIRMED" for row in violations):
                        record_confirmed = True
                    if any(row["confidence"] == "SUSPECTED" for row in violations):
                        record_suspected = True
                    violation_count += len(violations)
                    code_counts.update(row["violation_code"] for row in violations)
                    for violation in violations:
                        if emitted_for_record >= evidence_limit:
                            break
                        row = {
                            "sample_id": sample.sample_id,
                            "content_id": item.content_id,
                            "role": item.role,
                            "source_paths": item.attributes.get("source_paths", []),
                            **violation,
                        }
                        evidence_file.write(
                            json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n"
                        )
                        evidence_rows += 1
                        emitted_for_record += 1

                if record_eligible:
                    eligible_records += 1
                else:
                    if record_skips & {"HTML_PARSE_FAILED", "HTML_TOO_LARGE"}:
                        record_exclusions["HTML_CONTENT_NOT_SCANNABLE"] += 1
                    elif "EMPTY_HTML" in record_skips:
                        record_exclusions["EMPTY_HTML_ONLY"] += 1
                    else:
                        record_exclusions["NO_ELIGIBLE_HTML_CONTENT"] += 1
                if record_confirmed or record_suspected:
                    affected_records += 1
                if record_confirmed:
                    confirmed_records += 1
                if record_suspected:
                    suspected_records += 1

        non_compliant = (
            affected_records
            if basis == "CONFIRMED_AND_SUSPECTED"
            else confirmed_records
        )
        compliant = eligible_records - non_compliant
        observations: list[JsonObject] = [
            {
                "key": "compliant_record_count",
                "value": compliant,
                "value_type": "NUMBER",
                "unit": "record",
            },
            {
                "key": "non_compliant_record_count",
                "value": non_compliant,
                "value_type": "NUMBER",
                "unit": "record",
            },
            {
                "key": "evaluable_record_count",
                "value": eligible_records,
                "value_type": "NUMBER",
                "unit": "record",
            },
        ]
        status = "OK"
        status_detail: JsonObject = {}
        if eligible_records:
            observations.insert(
                0,
                {
                    "key": "compliant_record_ratio",
                    "value": compliant / eligible_records,
                    "value_type": "NUMBER",
                    "unit": "ratio",
                    "numerator": compliant,
                    "denominator": eligible_records,
                },
            )
        else:
            status = "NOT_APPLICABLE"
            observations = []
            status_detail = {
                "reason_code": "NO_ELIGIBLE_HTML_CONTENT",
                "message": (
                    "No PRESENT inline-text HTML ContentItem matched the "
                    "configured selectors"
                ),
            }

        return CalculatorOutput(
            status=status,
            status_detail=status_detail,
            observations=observations,
            population={
                "input_count": input_count,
                "evaluated_count": eligible_records,
                "excluded_count": input_count - eligible_records,
                "exclusions": (
                    [
                        {"reason": reason, "count": count}
                        for reason, count in sorted(record_exclusions.items())
                    ]
                    if record_exclusions
                    else []
                ),
            },
            semantic_coverage={
                "level": "PARTIAL",
                "covered": [
                    "HTML 结构异常（孤立/未闭合/隐式闭合标签）",
                    "内联样式字号离群与字体族混用（基本 CSS 继承）",
                    "极端行高、缩进、负边距与段间距",
                    "连续空段落/连续 <br> 空白块",
                    "表格层级、跨度取值与行列结构冲突",
                    "导航/面包屑/页脚等 DOM 结构残留",
                ],
                "excluded": [
                    "PDF/DOCX/OFD 物理版式与跨页断裂（HTML 源码不含分页信息）",
                    "浏览器最终渲染样式与外部 CSS 计算样式",
                    "WAI-ARIA 无障碍合规、语义化标签规范",
                    "Unicode 乱码与字符合法性（由 BG-02-02 覆盖）",
                    "文本噪声与显式广告标记（由 text_purity 覆盖）",
                ],
                "limitations": [
                    "字号/字体族基线依赖内联 style 声明，外部 CSS 可能使实际渲染偏离声明",
                    "启发式异常（SUSPECTED）默认不计入合规率，仅单独统计",
                    "启发式阈值是通用网页经验值，不表述为政务排版标准",
                ],
            },
            evidence=[
                EvidenceOutput(
                    path=evidence_path,
                    kind="GOVERNMENT_HTML_FORMAT_VIOLATIONS",
                    schema_ref=(
                        "qa-eval://schemas/evidence/bg-02-01-html-format/1.0.0"
                    ),
                    record_count=evidence_rows,
                    redaction_policy="matched-excerpts-only",
                    attributes={
                        "rule_version": RULESET_VERSION,
                        "rule_config_fingerprint": config.fingerprint(),
                        "compliance_basis": basis,
                    },
                )
            ],
            diagnostics=[
                *diagnostics,
                {
                    "key": "government_html_format_statistics",
                    "eligible_record_count": eligible_records,
                    "scanned_content_count": scanned_contents,
                    "affected_record_count": affected_records,
                    "confirmed_violation_record_count": confirmed_records,
                    "suspected_anomaly_record_count": suspected_records,
                    "violation_count": violation_count,
                    "violation_counts_by_code": dict(sorted(code_counts.items())),
                    "skipped_content_count": sum(skipped.values()),
                    "skipped_content_counts_by_reason": dict(sorted(skipped.items())),
                    "emitted_evidence_count": evidence_rows,
                    "semantic_coverage": (
                        "PARTIAL；排除项与能力受限说明见 result.semantic_coverage"
                    ),
                    "rule_version": RULESET_VERSION,
                    "rule_config_fingerprint": config.fingerprint(),
                    "compliance_basis": basis,
                    "enabled_checks": [key for key in CHECK_KEYS if key in checks],
                    "css_capability": dict(sorted(css_stats.items())),
                    "counting_semantics": {
                        "record": "至少一个适用 HTML ContentItem 才进入分母；多条异常只计一次 affected",
                        "content": "每个适用 HTML 内容项独立扫描，证据按内容项输出",
                        "violation": "每个检测到的异常独立计数；证据行数受 max_evidence_per_record 限制但聚合计数不设上限",
                        "compliance": (
                            f"compliant_record_ratio 分子按 {basis} 口径："
                            "仅确定性(CONFIRMED)违规计入 non_compliant_record_count"
                            if basis == "CONFIRMED_ONLY"
                            else "确定性(CONFIRMED)与疑似(SUSPECTED)违规均计入 non_compliant_record_count"
                        ),
                        "suspected_overlap": (
                            "suspected_anomaly_record_count 与 confirmed_violation_record_count "
                            "可重叠（同一记录既有确认又有疑似异常时两边各计一次）"
                        ),
                    },
                },
            ],
        )

    @staticmethod
    def _rule_config(parameters: JsonObject) -> HtmlFormatRuleConfig:
        basis = str(parameters.get("compliance_basis", COMPLIANCE_BASES[0]))
        patterns = _optional_string_set(parameters, "layout_residue_patterns")
        font_min = _number(parameters, "font_size_min_px", 9.0, minimum=1.0)
        font_max = _number(parameters, "font_size_max_px", 72.0, minimum=10.0)
        lh_min = _number(parameters, "line_height_min_px", 8.0, minimum=1.0)
        lh_max = _number(parameters, "line_height_max_px", 240.0, minimum=10.0)
        if font_min >= font_max:
            raise CalculatorValidationError(
                "font_size_min_px must be below font_size_max_px"
            )
        if lh_min >= lh_max:
            raise CalculatorValidationError(
                "line_height_min_px must be below line_height_max_px"
            )
        return HtmlFormatRuleConfig(
            font_size_min_px=font_min,
            font_size_max_px=font_max,
            font_size_outlier_scale=_number(
                parameters,
                "font_size_outlier_scale",
                1.8,
                minimum=1.0,
                exclusive_minimum=1.0,
                maximum=100.0,
            ),
            font_size_baseline_dominance=_number(
                parameters,
                "font_size_baseline_dominance",
                0.5,
                minimum=0.01,
                maximum=0.999,
            ),
            font_family_minority_ratio=_number(
                parameters,
                "font_family_minority_ratio",
                0.15,
                minimum=0.0,
                maximum=1.0,
            ),
            line_height_min_px=lh_min,
            line_height_max_px=lh_max,
            line_height_font_ratio_max=_number(
                parameters,
                "line_height_font_ratio_max",
                4.0,
                minimum=1.0,
                exclusive_minimum=1.0,
                maximum=100.0,
            ),
            text_indent_max_em=_number(
                parameters, "text_indent_max_em", 8.0, minimum=1.0
            ),
            text_indent_max_px=_number(
                parameters, "text_indent_max_px", 120.0, minimum=10.0
            ),
            negative_margin_tolerance_px=_number(
                parameters, "negative_margin_tolerance_px", 32.0, minimum=0.0
            ),
            paragraph_margin_max_px=_number(
                parameters, "paragraph_margin_max_px", 240.0, minimum=10.0
            ),
            consecutive_empty_block_threshold=_integer(
                parameters, "consecutive_empty_block_threshold", 5, 2
            ),
            consecutive_br_threshold=_integer(
                parameters, "consecutive_br_threshold", 4, 2
            ),
            layout_residue_patterns=tuple(sorted(patterns))
            if patterns
            else HtmlFormatRuleConfig().layout_residue_patterns,
            compliance_basis=basis,
        )

    @staticmethod
    def _skip_reason(
        item: ContentItem,
        include_roles: frozenset[str],
        exclude_roles: frozenset[str],
        include_ids: frozenset[str] | None,
    ) -> str | None:
        if item.modality != "text":
            return "NON_TEXT_MODALITY"
        if include_roles is not None and item.role not in include_roles:
            return "ROLE_NOT_INCLUDED"
        if item.role in exclude_roles:
            return "ROLE_EXCLUDED"
        if include_ids is not None and item.content_id not in include_ids:
            return "CONTENT_ID_NOT_INCLUDED"
        if item.presence != "PRESENT":
            return f"PRESENCE_{item.presence}"
        if not isinstance(item.payload, InlineTextPayload):
            return "NOT_INLINE_TEXT"
        if item.payload.format != "html":
            return "NOT_HTML_FORMAT"
        return None


__all__ = ["GovernmentHtmlFormatComplianceCalculator"]
