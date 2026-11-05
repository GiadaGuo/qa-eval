# BG-02-01 政务网页 HTML 格式规范性检测

`government_html_format_compliance@1.0.0` 是指标 `0201`（格式规范性）的独立
Calculator，对应 BG-02-01「政务网页 HTML 格式规范性检测」。它扫描
SampleRecord 2.0 中 `PRESENT` + `text` + `inline_text` + `format=html` 的
ContentItem（默认 `article.body.html`），对 HTML 结构、字体字号、段落排版、
连续空白块、表格结构与 DOM 级非正文残留做表示层检测。

原始 BG-02-01 中的「表格跨页断裂、页眉页脚物理版式」不覆盖：数据集保存的
是网页 HTML 正文，不含可靠物理分页信息；页眉页脚类问题在 DOM 残留规则中以
网页维度的形态检测。

## 代码路径

- 计算：`src/qa_eval/calculators/government_html_format_compliance.py`
- 共享规则（DOM 构建、CSS 解析、阈值配置）：`src/qa_eval/calculators/government_html_format_rules.py`
- 注册：`configs/registry/calculators/government-html-format-compliance-v1.0.0.json`、
  `configs/registry/bindings/0201-government-html-format-compliance.json`（priority 80）
- 测试：`tests/unit/test_government_html_format_compliance_calculator.py`、
  `tests/integration/test_government_html_format_compliance_run.py`

仅依赖标准库 `html.parser` 构建轻量 DOM 树，无新增第三方依赖，无浏览器渲染。

## 输入与适用性

选择器参数与跳过语义（内容级跳过原因计入诊断 `skipped_content_counts_by_reason`）：

| 条件 | 结果 |
| --- | --- |
| role ∈ `include_roles`（默认 `["article.body.html"]`）且 ∉ `exclude_roles` | 参与 |
| `include_content_ids` 指定时仅扫描匹配 content_id | 参与 |
| `modality != text` / `presence != PRESENT` / 非 `inline_text` / `format != html` | 跳过，不算异常 |
| HTML 文本为空白 | 跳过（`EMPTY_HTML`）；记录仅有此类内容时排除原因为 `EMPTY_HTML_ONLY` |
| 文本超过 `max_html_length_chars`（默认 2,000,000 字符） | 跳过（`HTML_TOO_LARGE`），显式计数，不静默丢弃 |
| `html.parser` 解析失败（极罕见） | 跳过（`HTML_PARSE_FAILED`），不算格式违规 |

记录级分母 `eligible_record_count` 只统计至少扫描过一个适用 HTML 内容项的
记录；无适用 HTML 时返回 `NOT_APPLICABLE`（`NO_ELIGIBLE_HTML_CONTENT`），
不伪造 100% 合规率。HTML 缺失本身不判为格式不合规（属其他质量维度）。

## 检测规则与违规码

每条违规携带 `confidence`：`CONFIRMED` 为确定性判定，`SUSPECTED` 为启发式
疑似异常（可复核后驳回）。

| 违规码 | confidence | 判定内容 |
| --- | --- | --- |
| `HTML_STRUCTURE_ANOMALY` | CONFIRMED | 孤立结束标签（如游离 `</span>`，severity ERROR）；块级容器 EOF 未闭合（WARNING） |
| `HTML_STRUCTURE_ANOMALY` | SUSPECTED | 结束标签隐式闭合了要求显式闭合的元素（解析器已修复） |
| `FONT_SIZE_OUTLIER` | CONFIRMED | 字号声明 ≤ 0（文本不可见，severity ERROR） |
| `FONT_SIZE_OUTLIER` | SUSPECTED | 字号 < `font_size_min_px`(9) 或 > `font_size_max_px`(72)（≥4 字符）；或偏离文档主流字号 ≥ `font_size_outlier_scale`(1.8x) 且 ≥10 字符 |
| `FONT_FAMILY_INCONSISTENCY` | SUSPECTED | 非主流字体族文本量 ≥ `font_family_minority_ratio`(15%) 且单块 ≥10 字符 |
| `PARAGRAPH_SPACING_ANOMALY` | SUSPECTED | 行高超出 [8px, 240px] 或 > 4 倍字号；缩进 > 8em；负边距/负缩进幅度 > 32px；垂直段间距 > 240px |
| `EXCESSIVE_EMPTY_BLOCKS` | CONFIRMED | 连续 ≥ `consecutive_empty_block_threshold`(5) 个空白 p/div（Entity 解码后判定）；或连续 ≥ `consecutive_br_threshold`(4) 个 `<br>` |
| `TABLE_STRUCTURE_ANOMALY` | CONFIRMED | tr/td/th 脱离 `<table>` 上下文（ERROR）；非法 rowspan/colspan（非整数、colspan<1 或 >1000、rowspan>65534；rowspan=0 合法）；空表格 |
| `TABLE_STRUCTURE_ANOMALY` | SUSPECTED | td/th 不在 `<tr>` 内（解析器隐式补行）；各行 colspan 合计不一致（含 rowspan 的表格跳过该检测并计入能力受限计数） |
| `HTML_LAYOUT_RESIDUE` | SUSPECTED | tag/id/class/role 签名命中特征（breadcrumb、back-to-top、site-footer、备案号、打印工具栏等）；或 nav/role=navigation 含 ≥3 链接；或 footer 链接文本占主体 |

结构判定基于解析栈的 HTML 规则实现：合法的结束标签省略（`<p>a<p>b`、
`<tr>` 连续省略 `</tr>` 等）与 void 标签不产生事件；`<div/>` 按 HTML5 语义
视为未闭合的起始标签。嵌套表格、合并单元格、正常 `&nbsp;`、单个空段落、
落款前留白、`text-align:justify`、落款右对齐、正文引用链接、标题/落款与
正文字号不同均不误报；父级 14px / 子级 21px（1.5x）的合法样式覆盖不触发
相对离群。

## 启发式与确定性规则的区别

- 确定性（CONFIRMED）：结构事实与取值校验 —— 标签闭合状态、表格层级、
  rowspan/colspan 数值域、空白块的连续数量。这些是从源码可直接确定的。
- 疑似（SUSPECTED）：需要「正常基线」经验的判断 —— 字号偏离主流、字体族
  混用、行高/缩进极端值、DOM 残留识别、解析器隐式修复。阈值是通用网页
  经验值，**不表述为违反国家标准或政务排版规范**（当前无正式标准可依），
  结果表述为「疑似异常，建议人工复核」。
- 主合规率默认 `CONFIRMED_ONLY`：疑似异常只进 `suspected_anomaly_record_count`
  等诊断统计，不把低置信告警直接计为不合规；可通过参数 `compliance_basis:
  CONFIRMED_AND_SUSPECTED` 切换口径。

## CSS 样式处理能力与局限

- 只解析内联 `style` 与表现型属性（`<font size/face>`）；实现基本继承与
  子元素覆盖，不实现完整 CSS Cascade，不声称得到浏览器最终渲染样式。
- 单位换算：px/pt/pc/in/cm/mm/q 绝对换算；em/% 依赖元素字号；rem 依赖根
  元素声明；无单位行高按字号倍数。ex/ch/视口单位等无法可靠换算的值计入
  `css_capability.unresolved_css_length_count` 后跳过，绝不跨单位比较数值。
- 外部样式表、`<style>` 块、媒体查询不在能力内：带 class/id 的元素计入
  `stylesheet_dependent_element_count`；`font:` 简写、相对父级未知的 em
  字号计入相应受限计数。无字号声明的文本不参与字号基线与离群判定
  （`undeclared_font_size_char_count`）。
- 字号/字体族基线：按「已声明的非标题文本」字符量占比 ≥
  `font_size_baseline_dominance`(0.5) 确定主流值；不足 30 个已声明字符时
  不做相对判定。h1-h6/th/caption/figcaption/summary 豁免。
- `<font size="+1">` 相对取值、`smaller/larger` 关键字不换算（返回未知）。

## 统计口径

- 观测值（仅指标 0201 已声明测量）：`compliant_record_ratio`（默认
  = 1 - 确认违规记录数 / eligible）、`compliant_record_count`、
  `non_compliant_record_count`、`evaluable_record_count`。
- 诊断（`government_html_format_statistics`）：`eligible_record_count`、
  `scanned_content_count`、`affected_record_count`（任一违规，记录去重）、
  `confirmed_violation_record_count`、`suspected_anomaly_record_count`
  （两者可与 confirmed 重叠，同一记录两类都命中时各计一次）、
  `violation_count`、`violation_counts_by_code`、`skipped_content_count(_by_reason)`、
  `css_capability`（能力受限计数）、`rule_version`、`rule_config_fingerprint`、
  `compliance_basis`、`counting_semantics`。
- population 闭合：input = evaluated + excluded（NO_ELIGIBLE_HTML_CONTENT /
  EMPTY_HTML_ONLY / HTML_CONTENT_NOT_SCANNABLE）。
- 同一记录多条异常只计一次 affected；同一内容多个 DOM 节点分别出证据；
  Calculator 不计算质量评分，评分由 Quality Contract 规则负责。

## Evidence Artifact

`government-html-format-violations.jsonl`（kind
`GOVERNMENT_HTML_FORMAT_VIOLATIONS`，schema
`qa-eval://schemas/evidence/bg-02-01-html-format/1.0.0`）。每行：

```json
{
  "sample_id": "policy-001",
  "content_id": "body-html",
  "role": "article.body.html",
  "source_paths": ["$.content"],
  "violation_code": "FONT_SIZE_OUTLIER",
  "severity": "WARNING",
  "confidence": "SUSPECTED",
  "dom_path": "div[1]/p[3]/span[1]",
  "observed_value": "8px",
  "expected_value": "document body baseline 16px",
  "message": "局部正文字号明显偏离文档主体字号",
  "evidence_text": "<span style=\"font-size:8px\">",
  "source_offset": 128,
  "offset_semantics": "zero-based Python Unicode character index of the start tag '<' in the raw HTML payload",
  "coordinate_space": "source_html",
  "rule_version": "1.0.0"
}
```

- `dom_path` 由真实解析树生成（同名兄弟 1-based 下标）。
- `source_offset` 来自 `html.parser.getpos()`，指向起始标签 `<` 的源码字符
  位置；仅对标签锚定的证据输出，不伪造文本偏移。
- `evidence_text` 截断 120 字符（起始标签原文或元素签名），不落完整正文；
  redaction policy `matched-excerpts-only`，`contains_raw_content=false`。
- `max_evidence_per_record`（默认 20）只限制证据行数，聚合计数不设上限。

## 性能策略

流式逐行读取 JSONL，逐内容项构建并丢弃 DOM；正则与规则配置在执行前编译
一次；聚合仅用低基数计数器，内存不随记录数增长。实测 10.9 万字符/记录 ×
2 万条（约 110 MB）耗时 4.3s、峰值 RSS 约 43 MB。超长 HTML 由
`max_html_length_chars` 保护并显式计入跳过统计，不静默跳过。

## 与其他 Calculator 的边界

- `format_compliance`（0201 通用）：继续负责 JSONL 合法性、SampleRecord
  schema、版本身份、sample_id 唯一性；本算子不做这些检查。
- `sample_text_encoding_compliance`（BG-02-02）：负责字符合法性/乱码；本
  算子不复用其文本规则。
- `text_purity`（02080106）：文本噪声/HTML 碎片残留正则在**文本层**；本算子
  的 `HTML_LAYOUT_RESIDUE` 在 **DOM 结构层**（tag/id/class/role 签名），
  规则独立，未复制其正则。
- 同一指标 0201 允许多 Calculator 并存，Quality Contract 显式选择
  `calculator_key`；平台不平均多个 Calculator 的合规率。

## 注册与调用

Registry sync 自动装载描述符与绑定（priority 80，低于 format_compliance/
sample_text_encoding 的 100/90）。通过 Quality Contract 选择：

```json
{
  "metric_key": "0201",
  "metric_version": "1.0.0",
  "measurement": "compliant_record_ratio",
  "calculator_key": "government_html_format_compliance",
  "calculator_version": "1.0.0",
  "parameters": {"include_roles": ["article.body.html"]}
}
```

参数（JSON Schema 与运行时校验一致，`additionalProperties: false`）含六类
检测开关（`enabled_checks`）、全部阈值（见上文规则表）与资源保护参数；默
认值见描述符 `parameter_schema`。规则指纹 = 有效阈值配置的 sha256，随诊断
与证据属性输出，结果可复现（同一规则版本 + 同一参数 + 同一输入 ⇒ 同一结果）。

## 测试结果

- `uv run pytest tests/unit/test_government_html_format_compliance_calculator.py`
  —— 34 通过：9 个正常不误报场景、8 类异常场景（含 record 去重）、边界
  （缺失/空 HTML/纯文本/解析失败/超长/多内容项/Entity 与单位/能力受限/
  DOM 路径/证据上限/口径切换/选择器/参数校验/流式 200 条）。
- `uv run pytest tests/integration/test_government_html_format_compliance_run.py`
  —— 2 通过：注册可加载（既有 0201 计算器不受影响）与
  DatasetVersion → Calculator → MetricResult → Evidence Artifact 全链路。
- 回归：`tests/unit`（290 项）与相关集成测试全部通过。
  `tests/integration/test_evaluation_run_service.py::test_formal_run_executes_...`
  在本次改动前（main HEAD）即已失败（`issue_total == 4 != 3`），与本算子无关。

## 当前不支持的检测能力

- PDF/DOCX/OFD 物理版式、跨页断裂、页眉页脚物理残留。
- 浏览器最终渲染样式、外部 CSS/媒体查询的计算样式、字体实际可用性。
- 完整 CSS Cascade/选择器特异性、`<style>` 块规则。
- WAI-ARIA 无障碍合规与语义化标签规范检查。
- 动态内容（JS 渲染后的 DOM）、图片型正文。
- 表格跨页断裂（HTML 源码不含分页信息）；含 rowspan 的复杂表不做行跨度
  一致性判定（显式计入能力受限）。
