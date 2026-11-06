# 0204 政策结构完整性实现与验证记录

## 1. 实现身份

- Metric：`0204@2.0.0`；Calculator Binding 为
  `policy_structure_compliance@1.0.0`，覆盖规则来源 BG-02-03/04/05。
  早期错误登记 `02040305@1.0.0` 已撤销，不再是有效 Metric。
- Calculator：`policy_structure_compliance@1.0.0`。
- 结果：一个 Dataset 级 MetricResult，多 observations；样本级四 Flag 写入 Evidence Artifact。
- 原有 `0204@1.0.0`、`structural_completeness@1.0.0` 和 binding 未修改。

内部模块：

- `policy_structure_compliance/schema.py`：四状态、source location、parse result、Ground Truth 身份。
- `policy_structure_compliance/html_source_map.py`：lexical source-map 与坐标回溯验证。
- `policy_structure_compliance/parsers.py`：Legal/Outline/Generic parser、MIXED 组合、文种弱先验。
- `policy_structure_compliance/calculator.py`：Protocol、四 Flag、聚合、流式 evidence。

## 2. 第一次验收：D1–D3

### Schema

状态为 PASS/FAIL/NOT_APPLICABLE/UNAVAILABLE。每组 flag 分别输出 status-count OBJECT、evaluable
count、coverage rate，并仅在 PASS+FAIL 分母非零时输出质量 rate。MetricResult population 只表达输入
处理闭合，不冒充各 Flag 分母。

### 原文 source-map

实现基于 `HTMLParser.getpos()` 与预计算行首位置取得原始 Python `str` 坐标。普通字符映射单字符
span；Entity 解码字符映射完整 entity span；跨标签文字保留多个 raw spans。没有使用全局 `str.find`，
也不从 plain 反推 HTML。

EXACT 在 path validator 中检查：span 边界、顺序、原始 slice、Entity 解码和 normalized visible text
round-trip。malformed HTML 降级 BLOCK_ONLY，仍需范围有效；plain 为 UNAVAILABLE。坐标不是 UTF-8
字节 offset。

已覆盖 Unicode emoji 前缀、Entity、嵌套/跨标签编号、重复文本、malformed HTML。focused tests：
9 passed。

### 文种与模式

可信 `metadata.document_type`/`metadata.extra.policyType` 优先，标题推断次之；输出 source/confidence。
正文 block 候选决定 LEGAL/OUTLINE/GENERIC/MIXED，文种不强制模式。测试覆盖元数据通知、标题通知、
通知大纲、通知嵌入法条、无编号通知、无章办法、正文法条引用。

## 3. 第二次验收：D4–E5

### 四 Flag

- parsing：正文 block 存在且结构 ordinal/tree invariant 有效则 PASS；缺正文、无可见正文、超长拒绝、
  单样本异常为 UNAVAILABLE；解析器能力不足不伪装为源文档 FAIL。
- numbering：按 parent+node type scope 检查 start/gap/duplicate/reverse。`1→4` 是一个 GAP event，
  missing ordinal 为 2 个；不重复计缺陷。无编号 Generic 为 NOT_APPLICABLE。
- segmentation：只使用版本化独立 FINAL HUMAN/IMPORTED annotation；比较节点 type 和 normalized exact
  boundary，输出 TP/FP/FN；无标注 UNAVAILABLE。
- path：每个结构节点实际验证 source spans 与 visible-text round-trip；EXACT/BLOCK_ONLY 均可 PASS，
  坐标存在但验证失败为 FAIL；另按节点输出 exact_location_rate。

Evidence 每个样本一行，不含正文全文和 excerpt。显式 `evidence_sample_limit` 只限制落盘行数，不改变
任何聚合计数。

### Registry 与 E2E

新增 Metric、descriptor 和 binding；Loader digest 校验、Registry sync、Contract、Logical/Physical Plan、
Evaluation Run、MetricResult persistence 与 Evidence Artifact 均通过集成测试。E2E fixture 的一个连续
样本和一个跳号样本产生 parsing rate=1、numbering rate=0.5、defect count=1；segmentation 两条均
UNAVAILABLE，未被报告为 FAIL。

### 固定真实样本自动验证

结构化结果：`docs/development/0204-policy-structure-validation.json`。输入为 Phase B 固定分层样本和
Dataset Version `01a1a9a4-c688-7a18-a70b-927c73ab24e0` 的 normalized artifact，共 270 条。

| Flag | PASS | FAIL | N/A | UNAVAILABLE |
|---|---:|---:|---:|---:|
| parsing | 235 | 0 | 0 | 35 |
| numbering | 67 | 79 | 89 | 35 |
| segmentation | 0 | 0 | 0 | 270 |
| path | 146 | 0 | 89 | 35 |

35 条 parsing unavailable 均为 NO_VISIBLE_BODY；符合附件/图片为主层的预期。146 条 path PASS 均执行
原始 Unicode span 和 visible text round-trip，自动验证中 0 path FAIL。由于尚未人工逐条核验，不能
把 0 FAIL 声称为定位准确率 100%。

真实样本识别出 111 条通知：GENERIC 49、OUTLINE 29、MIXED 30、LEGAL 3，验证通知不会被固定到单一
模式。Phase B notification 代理层 30 条的识别数记录在 JSON 结果中；这仍不是法律文种 Ground Truth。

### 性能

270 条分层样本在开启 tracemalloc 时耗时约 4.2 秒、约 64 samples/s、Python tracked peak 约 15 MB、
evidence 约 0.4 MB。线性投影全量约 5,000 秒，但分层样本偏向复杂/长文且 tracing 有开销，只能用于
容量规划，不能作为全量 SLA。另记录了 0–10k、10k–100k、100k–300k、>300k 四长度桶的 parser
基准。按要求未启动 321,902 条 Calculator 全量运行。

默认 HTML 上限 300,000 字符基于全量 321,902 条长度：p50 3,090、p90 24,203、p95 50,176、
p99 185,089、p99.5 292,986、p99.9 710,089、max 8,285,586。默认覆盖约 99.5%，其余明确
UNAVAILABLE；`FULL_SCAN` 可显式开启。

## 4. 覆盖边界与待人工事项

- 固定样本验证是自动验证，不是人工 precision/recall；尚不能报告 parser 准确率。
- 无独立结构标注，真实样本 segmentation 全部 UNAVAILABLE；fixture 仅证明接口 PASS/FAIL 逻辑。
- parser 覆盖 block 起始的章/节/条/款与四级大纲；未实现法律语义完整性、OCR、附件下载。
- “项”当前由大纲括号层级表达，尚未区分法条项与行政大纲项的语义标签；需人工校准集后版本化。
- BLOCK_ONLY 表示块级可回溯，不等于精确字符边界。
- 在决定全量运行前，应先完成人工复核固定样本与未知合法体例清单。

