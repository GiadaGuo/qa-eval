# 0204 政策结构完整性 Calculator 技术设计

> 范围：Phase C 设计及 Phase D–E 落地基线。`policy_structure_compliance@1.0.0`
> 已按本文最小方案实现；验证结果见 `0204-policy-structure-implementation.md`。

## 0. 已冻结决策（Phase D）

- 迁移说明：早期草案曾将该能力错误登记为 `02040305@1.0.0`；该登记已撤销。
  当前正式关系为 `0204@2.0.0 → policy_structure_compliance@1.0.0`，
  BG-02-03/04/05 仅作为规则来源；既有
  `0204@1.0.0` 不变。
- 一个 Calculator、一个 MetricResult、多 observation、逐样本四 Flag evidence。
- `max_html_length_chars=300000`：来自 321,902 条全量原始 HTML 长度 p99.5=292,986；默认
  `REJECT` 且明确 UNAVAILABLE，可显式使用 `FULL_SCAN`。
- Ground Truth：task `policy.structure.segmentation@1.0.0`，schema
  `qa-eval://schemas/annotations/policy-structure/1.0.0`，仅接受 FINAL HUMAN/IMPORTED。
- Evidence 默认不含 excerpt/全文；坐标为 Python Unicode code point、0-based、end-exclusive。

## 1. 推荐架构

新增一个 `policy_structure_compliance@1.0.0`，一次流式读取 SampleRecord、一次解析 HTML，生成一个
样本内 `StructureParseResult`，依次运行四个 validator，立即写一行 evidence 并更新有界聚合器。
不拆成四个生产 Calculator，因为 HTML block、mode、层级树、编号 scope 和坐标完全共享；拆分会把
8GB normalized artifact 重读四次并重复解析。

模块建议：

- `policy_structure_compliance/calculator.py`：Protocol、参数、流式执行、聚合、evidence。
- `policy_structure_compliance/html_source_map.py`：lexical HTML source-map 与坐标验证。
- `policy_structure_compliance/parsers.py`：文种弱先验、三个 parser 与 MIXED 组合。
- `policy_structure_compliance/schema.py`：内部 dataclass/Pydantic schema。

参数（descriptor `additionalProperties: false`）：HTML/plain/title role、文种 metadata path、允许模式、
`max_html_length_chars`、超长策略（默认 `UNAVAILABLE`，禁止静默截断）、evidence cap、excerpt cap、
parser policy version、structure annotation task key/version。聚合计数不受 evidence cap 影响。

## 2. HTML 双坐标 source-map

### 2.1 算法

对 Python `str` 原始 HTML 做单次 lexical scan，而不是先 BeautifulSoup/lxml 再猜 offset：

1. tokenizer 产生 start/end tag、text、entity、comment 等 token，记录 0-based、end-exclusive Unicode
   `source_html_start/end`；同步维护 tolerant DOM stack 和稳定 `dom_path`。
2. text token 逐字符输出 decoded text。普通字符映射到 `[i,i+1)`；entity 解码后的字符映射到整个
   entity span；无输出的 markup 不进入 normalized text。
3. block 标签 (`p/div/br/li/tr/td/h1...`) 插入规范换行，并记录其触发 token span；连续空白归一化
   时用 run-length mapping segment 保存 normalized range 到一个或多个 raw span。
4. block 由 normalized range、raw span list、DOM path 组成。条款节点继承/合并 block spans。

坐标定义：

- `normalized_text_start/end`：解析器生成文本的 Python Unicode code-point index，0-based、end-exclusive。
- `source_html_start/end`：原始 `InlineTextPayload.text` 的 Python Unicode code-point index；不是 UTF-8
  字节 offset。只有连续可逆 span 才称 EXACT。
- `source_html_spans[]`：跨标签/实体/非连续内容的真实 span 列表。兼容的 start/end 为 bounding range。
- `dom_path`：tolerant parser 树路径，仅在当前原始 HTML 和 parser version 下稳定。

`coordinate_status`：

- EXACT：节点 normalized range 的每段均有 source spans，且 round-trip 验证成功；跨标签时仍保留
  spans，不把 bounding range 伪称连续文本。
- BLOCK_ONLY：能定位包含 block/DOM，但节点边界因 malformed HTML 或归一化无法精确反解。
- UNAVAILABLE：plain 降级、超长拒绝、无 HTML 或 parser 失败。

复杂度为 O(n) 时间；source map 使用 run-length segments，内存 O(s+b)，s 为映射段数、b 为 block 数，
不使用每字符对象。单样本完成四判定后释放。

### 2.2 降级与验证

- HTML 缺失但 plain 存在：可解析 normalized 层级；所有 source HTML 坐标 UNAVAILABLE。
- malformed HTML：容错构树；可定位 block 则 BLOCK_ONLY，同时输出 diagnostic。
- 重复文本：从 token span 直接映射，不使用 `str.find`，因此无歧义。
- 超长 HTML：默认整条 parser flag UNAVAILABLE，其他可独立判定的 flag 继续；可选显式 FULL_SCAN。
- 路径 validator 实际检查范围、span 单调性、DOM path 可解析、raw slice/entity round-trip。

## 3. `StructureParseResult`

```json
{
  "parser": {"key": "policy-structure-parser", "version": "1.0.0", "policy_fingerprint": "sha256:..."},
  "document_type": {"value": "通知", "source": "METADATA", "confidence": 0.8},
  "mode": "MIXED",
  "mode_candidates": [{"mode": "LEGAL_HIERARCHY", "score": 0.8, "reasons": ["article_sequence"]}],
  "nodes": [{
    "node_id": "n12", "node_type": "ARTICLE", "parent_id": "n3", "depth": 2,
    "ordinal": 3, "raw_number": "第三条", "numbering_scope": "n3:ARTICLE",
    "title_range": null, "content_range": {"normalized_text_start": 120, "normalized_text_end": 188,
      "source_html_start": 452, "source_html_end": 731, "source_html_spans": [[452,480],[510,731]],
      "dom_path": "/body[1]/div[2]", "coordinate_status": "EXACT"},
    "children": []
  }],
  "unparsed_candidates": [{"kind": "NUMBERING", "range": {}, "reason": "AMBIGUOUS_REFERENCE"}],
  "diagnostics": [{"code": "MIXED_MODE_SELECTED", "severity": "INFO"}]
}
```

四组 Flag 必需字段：parsing 需要 mode/nodes/unparsed/diagnostics；numbering 需要 node type、parent、ordinal、
scope；segmentation 需要 nodes/ranges/parser version 与独立 annotation；path 需要全部 coordinate 字段。

## 4. 模式选择和解析

- LEGAL_HIERARCHY：章/节/条/款/项，允许“无章有条”。同层 ordinal 在 parent scope 内验证。
- OUTLINE_HIERARCHY：一、→（一）→1.→（1），结合 block 开头、相邻序列和缩进/样式，排除日期、
  金额、文号、附件编号和正文引用。
- GENERIC_DOCUMENT：以可靠 block/heading 形成层级；无编号是合法输入。
- MIXED：不是第四个独立 parser；在一个文档内把高置信 legal subtree 与 outline subtree 按 block
  范围组合。文种只作弱先验，内容证据优先。

解析成功不等于“至少一个节点”：必须消费所有高置信结构候选、树无环、边界单调不重叠、父子层级
合法、无 ERROR diagnostic，并达到版本化 coverage 阈值。parser 不支持但可能合法的结构是
UNAVAILABLE，不是原文 FAIL。

## 5. 四组 Flag 口径

| Flag | 适用 | PASS | FAIL | NOT_APPLICABLE | UNAVAILABLE | 数据集聚合 |
|---|---|---|---|---|---|---|
| `parsing_success` | 有 HTML/plain 正文且为支持的文本政策 | parse invariants 全满足，候选覆盖达标 | 输入可解析但树冲突、未消费高置信候选或边界不合法 | 非文本/明确非政策正文 | 正文缺失、附件未解析、超长拒绝、模式/能力不足、runtime parser error | PASS/(PASS+FAIL) |
| `numbering_valid` | 解析树存在至少一个可验证编号 scope | 每个 scope 连续且无重复/倒序 | 至少一个 scope 有 gap、duplicate、reverse/invalid ordinal | GENERIC 或合法无编号 | parsing 无法提供可靠 scope | PASS/(PASS+FAIL)；`numbering_defect_count` 求和 |
| `segmentation_correct` | 存在 FINAL、独立 HUMAN/IMPORTED 结构标注，task/version 匹配 | 预测边界/类型/父子路径按预定 exact 或容差规则全部匹配 | 标注有效且至少一项不匹配 | 标注明确声明该样本不适用 | 无标注、草稿/拒绝、标注 schema/version 不支持 | PASS/(PASS+FAIL) |
| `path_locatable` | 已产生至少一个需定位节点 | 所有目标节点 coordinate 校验通过且满足所需精度 | 坐标越界、错序、round-trip/DOM 验证失败 | 合法无节点 GENERIC | 仅 plain、source-map 不可用或 parsing 不可判 | PASS/(PASS+FAIL) |

编号缺陷按 `(sample_id, scope, level, transition)` 去重。一次 `1→4` 是一个 GAP 事件，
`missing_ordinals=[2,3]` 可作细节，但 `numbering_defect_count` 只加 1，避免“跳号+两个缺号”三计。
重复和倒序是不同事件类型；同一 raw transition 只取一个最高优先级 code。

每组 evidence 含状态、reason code、必要 node/scope/ordinal、坐标和有界 excerpt；不落全文。默认
`contains_raw_content=false`，若启用 excerpt 则 descriptor/evidence 必须声明 true 和截断/脱敏策略。

## 6. 聚合、状态与 observation

每个 flag 维护四状态计数；比率分母严格为 PASS+FAIL，N/A 与 unavailable 分开报告。建议 0204 新定义
声明：四个 status-count OBJECT、三个 ratio、`numbering_defect_count`、各自 evaluable count。没有可判定
分母时不生成该 ratio，整体可有其他 observation；若所有 flag 都不可计算，CalculatorOutput=MISSING 或
NOT_APPLICABLE（按原因），不得伪造 0/1。

由于 MetricResult 只有一个总体 population，不能用它同时表达四个不同分母；population 只表达“成功
读取/处理的样本数”，每个 ratio 自带 numerator/denominator，四状态闭合计数作为 observations/diagnostic。

## 7. 附件、性能和版本

- 不联网下载。PDF/OCR/外链解析未来作为 Dataset Version 的版本化 derived content/artifact；Calculator
  只消费已声明 role，并记录 artifact/checksum/parser identity。
- JSONL 单遍；evidence 边算边写；只保留聚合 counter 与当前样本。禁止 `read_text().splitlines()`。
- 单样本异常写 UNAVAILABLE diagnostic，不中止兄弟 flags；损坏 SampleRecord 属输入验证 ERROR，是否
  允许隔离需与平台规范一致。
- Calculator version、code digest、parameters fingerprint、parser policy fingerprint、input fingerprint
  全部入运行/evidence metadata。
- MVP 不持久化完整 parse tree。若未来拆 metric，共享 parse artifact 必须先设计 canonical derived
  artifact 与 Planner dependency，不能用临时文件路径暗连。

## 8. 语义覆盖边界

本设计能测规则可判定的结构解析、编号序列、标注一致性和坐标有效性；不能仅凭规则证明法律语义
完整、内容真实或人工切分正确。附件未解析、无标注、未知合法体例均显式 UNAVAILABLE。

