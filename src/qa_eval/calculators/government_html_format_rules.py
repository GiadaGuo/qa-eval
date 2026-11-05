"""BG-02-01 政务网页 HTML 格式规范性共享规则：DOM 构建、CSS 解析与规则配置。

设计约束（对应 docs/plan/other/政务专项评测/算子实现/BG-02-01.md）：

- 仅依赖标准库 ``html.parser`` 构建 DOM 树，不引入浏览器渲染框架；
- 本模块只提供「表示层规则」：结构事件、CSS 长度换算、DOM 路径与规则
  配置，不承担合规判定与聚合，判定语义在 Calculator 中实现；
- HTML 解析器具有自动容错能力，结构事件显式区分「确定性异常」
  （孤立结束标签、EOF 未闭合的块级容器）与「解析器修复的疑似异常」
  （结束标签隐式闭合其他要求闭合的元素）；
- CSS 只解析内联 ``style`` 与表现型属性（``font`` 标签）。外部样式表、
  ``<style>`` 块、复杂选择器、媒体查询均不在能力范围内，无法换算的
  单位返回 ``None``，由调用方统计能力受限情况，绝不伪造换算结果。

坐标系约定：所有 ``source_offset`` 均为 Python ``str`` 的 0 基 Unicode
字符索引，指向原始 HTML payload 中起始标签的 ``<`` 字符。
"""

from __future__ import annotations

import hashlib
import html.parser
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

RULESET_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 标签分类（依据 HTML 标准，服务于结构事件判定）
# ---------------------------------------------------------------------------

VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

# 结束标签可省略（或源码片段常以孤立形式出现）的标签：对这些标签的
# 隐式闭合 / 孤立结束标签不产生结构事件，避免把合法 HTML 写法判为异常。
OPTIONAL_END_TAGS = frozenset(
    {
        "html",
        "head",
        "body",
        "p",
        "li",
        "dt",
        "dd",
        "option",
        "optgroup",
        "tr",
        "td",
        "th",
        "thead",
        "tbody",
        "tfoot",
        "colgroup",
        "caption",
        "rt",
        "rp",
    }
)

# EOF 处仍未闭合的块级容器：属确定性源码事实，报告为疑似/确认异常；
# 行内元素（span/a/b 等）在抽取片段中常见截断，不在此列。
STRUCTURAL_UNCLOSED_TAGS = frozenset(
    {
        "div",
        "table",
        "ul",
        "ol",
        "dl",
        "section",
        "article",
        "aside",
        "main",
        "form",
        "blockquote",
        "details",
        "dialog",
        "fieldset",
        "nav",
        "header",
        "footer",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)

# 这些起始标签会按 HTML 规则隐式结束未闭合的 <p>（p 的结束标签可省略）
P_CLOSERS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "details",
        "dialog",
        "dir",
        "div",
        "dl",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)

# 标题/表格标题类容器：字号与字体族通常与正文不同，字体异常检测豁免
HEADING_LIKE_TAGS = frozenset(
    {"h1", "h2", "h3", "h4", "h5", "h6", "th", "caption", "figcaption", "summary"}
)

# 空白块判定时视为「有实际内容」的子元素（非纯留白）
MEDIA_CONTENT_TAGS = frozenset(
    {
        "img",
        "table",
        "hr",
        "video",
        "audio",
        "iframe",
        "svg",
        "canvas",
        "embed",
        "object",
        "input",
        "select",
        "textarea",
        "button",
        "form",
    }
)

# DOM 残留检测只考察结构容器标签；<p> 内的落款/日期不会进入该检测
RESIDUE_CONTAINER_TAGS = frozenset(
    {"nav", "footer", "aside", "div", "section", "ul", "header"}
)

_RAW_TEXT_TAGS = frozenset({"script", "style"})


class HtmlFormatParseError(RuntimeError):
    """HTML 解析器本身失败（极少见；解析器对畸形 HTML 具有容错能力）。"""


# ---------------------------------------------------------------------------
# DOM 树
# ---------------------------------------------------------------------------


class Element:
    """轻量 DOM 节点；文本以 ``str`` 形式混排在 ``children`` 中。"""

    __slots__ = ("attrs", "children", "parent", "raw_tag_text", "start_offset", "tag")

    def __init__(
        self,
        tag: str,
        attrs: dict[str, str | None],
        parent: Element | None,
        start_offset: int = -1,
        raw_tag_text: str = "",
    ) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[Element | str] = []
        self.start_offset = start_offset
        self.raw_tag_text = raw_tag_text

    def attr(self, name: str) -> str | None:
        value = self.attrs.get(name)
        if value is None or not value.strip():
            return None
        return value


@dataclass(frozen=True)
class StructureEvent:
    """一次源码级结构事件（由解析栈推导，非 DOM 遍历结果）。

    ``kind`` 取值：

    - ``STRAY_END_TAG``：结束标签无对应起始标签（确定性异常）；
    - ``IMPLICIT_CLOSE``：结束标签隐式闭合了其它要求闭合的元素
      （解析器修复的疑似异常），``closed_tags`` 记录被隐式闭合的标签；
    - ``UNCLOSED_AT_EOF``：块级容器在文档结束处仍未闭合。
    """

    kind: str
    tag: str
    source_offset: int
    raw_tag_text: str
    dom_path: str
    closed_tags: tuple[str, ...] = ()


def iter_elements(root: Element) -> Iterable[Element]:
    """先序迭代 DOM 树（迭代实现，避免深树的递归栈溢出）。"""

    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        for child in reversed(node.children):
            if isinstance(child, Element):
                stack.append(child)


def dom_path(element: Element | None) -> str:
    """基于真实解析树生成 ``div[2]/p[3]/span[1]`` 形式的路径。"""

    parts: list[str] = []
    node = element
    while node is not None and node.parent is not None:
        index = 0
        for sibling in node.parent.children:
            if sibling is node:
                break
            if isinstance(sibling, Element) and sibling.tag == node.tag:
                index += 1
        parts.append(f"{node.tag}[{index + 1}]")
        node = node.parent
    return "/".join(reversed(parts))


def visible_text(element: Element) -> str:
    """拼接子树内全部文本节点（含元素自身直接文本；script/style 已剔除）。"""

    parts: list[str] = []
    stack = [element]
    while stack:
        node = stack.pop()
        parts.extend(child for child in node.children if isinstance(child, str))
        for child in reversed(node.children):
            if isinstance(child, Element):
                stack.append(child)
    return "".join(parts)


def has_ancestor_tag(element: Element, tag: str) -> bool:
    node = element.parent
    while node is not None:
        if node.tag == tag:
            return True
        node = node.parent
    return False


def has_ancestor_element(element: Element, ancestor: Element) -> bool:
    node = element.parent
    while node is not None:
        if node is ancestor:
            return True
        node = node.parent
    return False


def is_blank_block(element: Element) -> bool:
    """块级元素是否为纯留白：无可见文本且无媒体/表格等实际内容。"""

    if visible_text(element).strip():
        return False
    return not any(
        isinstance(child, Element) and child.tag in MEDIA_CONTENT_TAGS
        for child in element.children
    )


class _TreeBuilder(html.parser.HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        starts = [0]
        for index, char in enumerate(text):
            if char == "\n":
                starts.append(index + 1)
        self._line_starts = starts
        self.root = Element("#document", {}, None)
        self._stack: list[Element] = [self.root]
        self.events: list[StructureEvent] = []
        self._text = text

    # -- 位置 ------------------------------------------------------------

    def _absolute_offset(self) -> int:
        line, column = self.getpos()
        if 1 <= line <= len(self._line_starts):
            return min(len(self._text), self._line_starts[line - 1] + column)
        return -1

    # -- 起始标签 --------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open_element(tag, dict(attrs), self.get_starttag_text() or f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # HTML5 语义：`<div/>` 等价于未闭合的 `<div>`；void 标签不入栈。
        if tag in VOID_TAGS:
            return
        self._open_element(tag, dict(attrs), self.get_starttag_text() or f"<{tag}>")

    def _open_element(self, tag: str, attrs: dict[str, str | None], raw: str) -> None:
        top = self._stack[-1]
        # HTML 规范允许的隐式闭合：起始标签结束未闭合的 <p> / 同级表格单元
        # 等。这些闭合是合法写法，不产生结构事件。
        if tag in P_CLOSERS and top.tag == "p":
            self._stack.pop()
            top = self._stack[-1]
        if tag in {"li", "dt", "dd", "option"}:
            family = {"li"} if tag == "li" else {"dt", "dd"}
            while top.tag in family:
                self._stack.pop()
                top = self._stack[-1]
        elif tag == "tr":
            while top.tag in {"tr", "td", "th"}:
                self._stack.pop()
                top = self._stack[-1]
        elif tag in {"td", "th"}:
            while top.tag in {"td", "th"}:
                self._stack.pop()
                top = self._stack[-1]
        elif tag in {"thead", "tbody", "tfoot"}:
            while top.tag in {"tr", "td", "th", "thead", "tbody", "tfoot"}:
                self._stack.pop()
                top = self._stack[-1]
        element = Element(tag, attrs, top, self._absolute_offset(), raw)
        top.children.append(element)
        if tag not in VOID_TAGS:
            self._stack.append(element)

    # -- 结束标签 --------------------------------------------------------

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            # `</br>` 等对 void 标签的结束标签：浏览器按容错处理，忽略。
            return
        depth = None
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                depth = index
                break
        offset = self._absolute_offset()
        raw = f"</{tag}>"
        if depth is None:
            if tag not in OPTIONAL_END_TAGS:
                self.events.append(
                    StructureEvent(
                        kind="STRAY_END_TAG",
                        tag=tag,
                        source_offset=offset,
                        raw_tag_text=raw,
                        dom_path=dom_path(self._stack[-1]),
                    )
                )
            return
        implicitly_closed: list[str] = []
        while len(self._stack) - 1 >= depth:
            closed = self._stack.pop()
            if closed.tag not in OPTIONAL_END_TAGS and len(self._stack) - 1 >= depth:
                implicitly_closed.append(closed.tag)
        if implicitly_closed:
            self.events.append(
                StructureEvent(
                    kind="IMPLICIT_CLOSE",
                    tag=tag,
                    source_offset=offset,
                    raw_tag_text=raw,
                    dom_path=dom_path(self._stack[-1]),
                    closed_tags=tuple(sorted(implicitly_closed)),
                )
            )

    # -- 文本 / 忽略项 ---------------------------------------------------

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if any(element.tag in _RAW_TEXT_TAGS for element in self._stack):
            return
        top = self._stack[-1]
        if top.children and isinstance(top.children[-1], str):
            top.children[-1] += data
        else:
            top.children.append(data)

    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return

    # -- 收尾 ------------------------------------------------------------

    def finalize(self) -> list[StructureEvent]:
        self.close()
        remaining = [
            element
            for element in self._stack[1:]
            if element.tag in STRUCTURAL_UNCLOSED_TAGS
        ]
        for element in remaining:
            self.events.append(
                StructureEvent(
                    kind="UNCLOSED_AT_EOF",
                    tag=element.tag,
                    source_offset=element.start_offset,
                    raw_tag_text=element.raw_tag_text,
                    dom_path=dom_path(element),
                )
            )
        return self.events


def build_dom(text: str) -> tuple[Element, list[StructureEvent]]:
    """解析 HTML 片段，返回 (DOM 根节点, 结构事件列表)。"""

    builder = _TreeBuilder(text)
    try:
        builder.feed(text)
        events = builder.finalize()
    except Exception as exc:  # html.parser 极少抛错；不把异常吞成误报
        raise HtmlFormatParseError(str(exc)) from exc
    return builder.root, events


# ---------------------------------------------------------------------------
# CSS 解析（仅内联 style 与表现型属性）
# ---------------------------------------------------------------------------

_ABSOLUTE_LENGTH_UNITS = {
    "px": 1.0,
    "pt": 4.0 / 3.0,
    "pc": 16.0,
    "in": 96.0,
    "cm": 96.0 / 2.54,
    "mm": 96.0 / 25.4,
    "q": 96.0 / 101.6,
}

_LENGTH_RE = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*(px|pt|pc|in|cm|mm|q|em|rem|%|ex|ch|vw|vh|vmin|vmax)?\s*$",
    re.IGNORECASE,
)

# CSS 关键字字号的常见浏览器取值（96dpi 下的近似值）
_FONT_SIZE_KEYWORDS = {
    "xx-small": 9.0,
    "x-small": 10.0,
    "small": 13.0,
    "medium": 16.0,
    "large": 18.0,
    "x-large": 24.0,
    "xx-large": 32.0,
    "smaller": None,
    "larger": None,
}

# 旧式 <font size="N"> 的传统映射（pt → px 按 96dpi 换算）
_FONT_TAG_SIZES = {
    "1": 8 * 96 / 72,
    "2": 10 * 96 / 72,
    "3": 12 * 96 / 72,
    "4": 14 * 96 / 72,
    "5": 18 * 96 / 72,
    "6": 24 * 96 / 72,
    "7": 36 * 96 / 72,
}


def parse_style_declarations(style: str | None) -> dict[str, str]:
    """把内联 ``style`` 拆成 ``{属性名: 值}``（属性名小写）。"""

    if not style:
        return {}
    declarations: dict[str, str] = {}
    for chunk in style.split(";"):
        name, separator, value = chunk.partition(":")
        if separator and name.strip() and value.strip():
            declarations[name.strip().lower()] = value.strip()
    return declarations


def length_to_px(
    value: str | None, *, reference_px: float | None, root_px: float | None = None
) -> float | None:
    """CSS 长度换算为像素；无法可靠换算的单位返回 ``None``。

    ``em`` / ``%`` 依赖 ``reference_px``（元素自身字号）；``rem`` 依赖
    ``root_px``；``ex``/``ch``/视口单位没有可靠换算基准，一律 ``None``。
    不同单位的数值绝不直接比较。
    """

    if value is None:
        return None
    match = _LENGTH_RE.match(value)
    if match is None:
        return None
    number = float(match.group(1))
    unit = (match.group(2) or "px").lower()
    if unit in _ABSOLUTE_LENGTH_UNITS:
        return number * _ABSOLUTE_LENGTH_UNITS[unit]
    if unit in {"em", "%"}:
        return None if reference_px is None else number * reference_px
    if unit == "rem":
        return None if root_px is None else number * root_px
    return None


def parse_font_size(
    value: str | None,
    *,
    parent_px: float | None,
    root_px: float | None = None,
) -> float | None:
    """解析 ``font-size`` 声明值；关键字使用近似映射，未知值返回 ``None``。"""

    if value is None:
        return None
    stripped = value.strip().lower()
    if stripped in _FONT_SIZE_KEYWORDS:
        # smaller/larger 等相对关键字映射为 None，保持能力受限语义
        return _FONT_SIZE_KEYWORDS[stripped]
    return length_to_px(value, reference_px=parent_px, root_px=root_px)


def parse_font_tag_size(value: str | None) -> float | None:
    """``<font size="N">`` 绝对取值映射；``+1``/``-1`` 相对取值不换算。"""

    if value is None:
        return None
    return _FONT_TAG_SIZES.get(value.strip())


def parse_line_height(value: str | None, *, font_px: float | None) -> float | None:
    """解析 ``line-height``：无单位数值按字号倍数换算，其余走长度换算。"""

    if value is None:
        return None
    stripped = value.strip()
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", stripped):
        return None if font_px is None else float(stripped) * font_px
    return length_to_px(stripped, reference_px=font_px)


def parse_font_family(value: str | None) -> tuple[str, ...]:
    """字体族声明拆成小写规范化 token 列表（保留备选族信息）。"""

    if value is None:
        return ()
    tokens = (item.strip().strip("'\"").lower() for item in value.split(","))
    return tuple(item for item in tokens if item)


def families_consistent(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    """两份字体族声明是否视为一致：token 集合相交或首选族相同。"""

    if not left or not right:
        return True
    return bool(set(left) & set(right)) or left[0] == right[0]


# ---------------------------------------------------------------------------
# 规则配置
# ---------------------------------------------------------------------------

DEFAULT_LAYOUT_RESIDUE_PATTERNS: tuple[str, ...] = (
    r"breadcrumb|crumb|面包屑",
    r"back[-_]?to[-_]?top|return[-_]?top|go[-_]?top|scroll[-_]?top|返回顶部|回到顶部",
    r"(site|page|web|www|website)[-_]?footer|footer[-_]?(info|bottom|copyright|link|bar|nav)|页脚|网站底部|网站尾部",
    r"print[-_]?(tool|bar|btn|button|friendly)|打印(本页|页面|文章|正文|工具)|友好打印",
    r"(main|top|primary|global|header|site)[-_]?nav(igation)?|navbar|网站导航|导航栏|主导航|栏目导航",
    r"all\s*rights\s*reserved|版权所有|icp备\s*\d|备案编号|备案号",
    r"(share|weixin|weibo|wechat)[-_]?(tool|bar|box)|分享到|扫一扫|二维码",
)


@dataclass(frozen=True)
class HtmlFormatRuleConfig:
    """BG-02-01 规则参数（由 Calculator 参数解析而来，均有默认值）。"""

    font_size_min_px: float = 9.0
    font_size_max_px: float = 72.0
    font_size_outlier_scale: float = 1.8
    font_size_baseline_dominance: float = 0.5
    font_family_minority_ratio: float = 0.15
    line_height_min_px: float = 8.0
    line_height_max_px: float = 240.0
    line_height_font_ratio_max: float = 4.0
    text_indent_max_em: float = 8.0
    text_indent_max_px: float = 120.0
    negative_margin_tolerance_px: float = 32.0
    paragraph_margin_max_px: float = 240.0
    consecutive_empty_block_threshold: int = 5
    consecutive_br_threshold: int = 4
    max_rowspan: int = 65534
    max_colspan: int = 1000
    layout_residue_patterns: tuple[str, ...] = DEFAULT_LAYOUT_RESIDUE_PATTERNS
    # 统计口径（不参与指纹，避免诊断字段扰动规则指纹）
    compliance_basis: str = field(default="CONFIRMED_ONLY", compare=False)

    def fingerprint(self) -> str:
        payload = {
            key: (list(value) if isinstance(value, tuple) else value)
            for key, value in (
                (key, getattr(self, key))
                for key in (
                    "font_size_min_px",
                    "font_size_outlier_scale",
                    "font_size_max_px",
                    "font_size_baseline_dominance",
                    "font_family_minority_ratio",
                    "line_height_min_px",
                    "line_height_max_px",
                    "line_height_font_ratio_max",
                    "text_indent_max_em",
                    "text_indent_max_px",
                    "negative_margin_tolerance_px",
                    "paragraph_margin_max_px",
                    "consecutive_empty_block_threshold",
                    "consecutive_br_threshold",
                    "max_rowspan",
                    "max_colspan",
                    "layout_residue_patterns",
                )
            )
        }
        canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


VIOLATION_CATALOG: tuple[dict[str, str], ...] = (
    {
        "violation_code": "HTML_STRUCTURE_ANOMALY",
        "rule_version": RULESET_VERSION,
        "confidence": "CONFIRMED|SUSPECTED",
        "description": (
            "孤立结束标签（确认）、EOF 未闭合块级容器（确认）、"
            "结束标签隐式闭合其它元素（疑似）"
        ),
    },
    {
        "violation_code": "FONT_SIZE_OUTLIER",
        "rule_version": RULESET_VERSION,
        "confidence": "SUSPECTED",
        "description": (
            "正文文本块字号明显偏离文档主流字号，或绝对值极小/极大；标题类容器豁免"
        ),
    },
    {
        "violation_code": "FONT_FAMILY_INCONSISTENCY",
        "rule_version": RULESET_VERSION,
        "confidence": "SUSPECTED",
        "description": "同类正文中明显异常的字体族混用（按文本量占比判定）",
    },
    {
        "violation_code": "PARAGRAPH_SPACING_ANOMALY",
        "rule_version": RULESET_VERSION,
        "confidence": "SUSPECTED",
        "description": "极端行高、异常缩进、不合理负边距与极端段间距",
    },
    {
        "violation_code": "EXCESSIVE_EMPTY_BLOCKS",
        "rule_version": RULESET_VERSION,
        "confidence": "CONFIRMED",
        "description": "连续空段落 / 连续 <br> / 仅空白的连续块超过阈值",
    },
    {
        "violation_code": "TABLE_STRUCTURE_ANOMALY",
        "rule_version": RULESET_VERSION,
        "confidence": "CONFIRMED|SUSPECTED",
        "description": (
            "表格元素脱离表格上下文、非法 rowspan/colspan、无有效单元格、"
            "行列跨度冲突（含 rowspan 的复杂表跳过冲突检测）"
        ),
    },
    {
        "violation_code": "HTML_LAYOUT_RESIDUE",
        "rule_version": RULESET_VERSION,
        "confidence": "SUSPECTED",
        "description": "导航栏、面包屑、打印工具栏、网站页脚等 DOM 结构残留",
    },
)


__all__ = [
    "DEFAULT_LAYOUT_RESIDUE_PATTERNS",
    "HEADING_LIKE_TAGS",
    "RESIDUE_CONTAINER_TAGS",
    "RULESET_VERSION",
    "VIOLATION_CATALOG",
    "Element",
    "HtmlFormatParseError",
    "HtmlFormatRuleConfig",
    "StructureEvent",
    "build_dom",
    "dom_path",
    "families_consistent",
    "has_ancestor_element",
    "has_ancestor_tag",
    "is_blank_block",
    "iter_elements",
    "parse_font_family",
    "parse_font_size",
    "parse_font_tag_size",
    "parse_line_height",
    "parse_style_declarations",
    "visible_text",
]
