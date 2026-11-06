# 0204 政策结构完整性：代码与架构审计

> 范围：BG-02-03/04/05 的 Phase A。审计日期：2026-11-06。本文不实现生产
> Calculator，也不修改 Registry、Planner、Aggregator。

## 1. 结论

现有平台可以用一个 Calculator 对每条样本只解析一次，并把四组样本状态写入同一份
Evidence，再把多项数据集级测量写入同一个 `MetricResult`。但平台**没有 canonical 的样本级
Flag 输出模型**：`CalculatorOutput` 只有数据集级 `observations`、统一 `status`、population、
coverage 与 evidence。因此“一个 Calculator 原生输出四个独立 Flag/四个 MetricResult”目前不成立；
最小实现是一个 `policy_structure_compliance` Calculator、一个 0204 MetricResult、多项 observation、
一份逐样本四状态 evidence。

若产品要求四组 Flag 分别成为可查询、可判定的 MetricResult，就需要四个 Metric Definition/Contract
item，以及共享确定性执行能力；当前 Planner 只对 `SHARED_INFERENCE` 合并计算，直接 Calculator
没有共享中间产物协议。这属于后续平台增强，不是 Phase A–C 的改动。

另一个重要冲突是：现有 `0204@1.0.0` 定义的是字段缺失率，绑定
`structural_completeness@1.0.0`；BG-02-03/04/05 是政策正文结构解析质量，不能静默覆盖既有语义。
后续应由业务确认是新增 0204 子指标，还是发布 0204 的不兼容新版本。

## 2. 已由代码确认的事实

### 2.1 SampleRecord 与 Dataset Version

- `SampleRecordV2` 位于 `src/qa_eval/contracts/datasets.py:184`，通过
  `dataset_version_id` 直接关联 Dataset Version；`sample_id` 在版本内标识样本。
- `contents[]` 是 `ContentItem`（同文件 `:119`），以 `role` 区分语义输入；`annotations[]`
  使用 `Annotation`（`:155`）；`source` 使用 `SampleSource`（`:165`），含 artifact ID、原始记录
  ID、行号、locator 和 attributes。
- 当前全量政策版本为 dataset `9ac80218-f44b-4edc-8abd-db1a39c8fdee`、version
  `01a1a9a4-c688-7a18-a70b-927c73ab24e0`。Manifest 声明 RAW_DATA 与 NORMALIZED_DATA
  均为 321,902 条，0 reject。
- 映射模板 `configs/mapping_templates/policy.policy.json:35-36` 将同一个 `$.content` 映射为
  `article.body.html` 与 `article.body.plain`；`article.title` 来自 `$.title`。`policyType` 是
  PASSTHROUGH，最终位于 `metadata.extra.policyType`，不是强类型文种字段。
- Normalizer 在 `src/qa_eval/application/normalization/jhsjk_article_normalizer.py:41-87`
  使用标准库 `HTMLParser` 生成 plain；`:406-430` 为每个 content 保留 `source_paths`，plain
  标注 `html_to_complete_plain_text@1.0.0`。该转换没有 source map，所以 HTML 与 plain **没有可靠
  offset 映射**。
- URL/附件来自 `SampleSource.attributes` 与 `metadata.attachments`。模板映射了 `policyurl`、
  `href`、`linkhref`、`attachurl` 等；原始路径保存在 mapping/attributes，而不是一个名为
  `source_paths` 的顶层字段。

### 2.2 Calculator、Registry 与结果链路

- Protocol 在 `src/qa_eval/calculators/protocol.py:42-80`：`execute(context, parameters)` 返回
  一个 `CalculatorOutput`。它可含多个 observation 和多个 EvidenceOutput，但只有一个统一 status。
- `MetricResultV1` 在 `src/qa_eval/contracts/metrics.py:93`；`:110-124` 强制 OK/PARTIAL 必须有
  observation，非 OK/PARTIAL 不得有 observation，且 observation key 唯一。
- Registry 从 `configs/registry/{metrics,calculators,bindings}` 同步，代码为
  `MetricRegistryService.sync()`（`src/qa_eval/application/services/metric_registry_service.py:48`）。
- Logical Plan 的 output binding 固定到一个 metric + measurement（
  `src/qa_eval/contracts/execution.py:41`）。Planner 对每个 Contract item 创建一个 METRIC operation
  和一个 output binding（`logical_planner_service.py:235-282`）。
- 直接执行 `_calculate()`（`evaluation_run_service.py:971` 附近）只调用一次 Calculator，随后构造
  一个以 stage.metric 为身份的 MetricResult。Evidence 在 `_store_evidence()`（`:1020`）提交为
  ArtifactRef。
- 共享执行只存在于 `SHARED_INFERENCE` 路径；它为每个 output binding 调用各自 `score_shared()`，
  并隔离兄弟 metric 的异常。没有确定性 parser 的共享 artifact/output contract。
- `ApplicabilityService.evaluate()`（`applicability_service.py:84`）处理数据集级
  APPLICABLE/NOT_APPLICABLE/NEEDS_INPUT/UNAVAILABLE；这不是每条样本四状态模型。

### 2.3 已有可复用能力

- `government_html_format_rules.py` 已有标准库 HTML tree builder、`dom_path()`、visible text、
  Python Unicode 字符 source offset；文件头明确该 offset 不是字节 offset。
- `government_html_format_compliance.py:180-203` 的 evidence 已使用 `coordinate_space=source_html`、
  `dom_path`、`source_offset` 与 offset semantics，可复用命名和隐私策略。
- `_PlainTextHTMLParser` 可复用“哪些标签形成换行”的经验，但不能复用为精确 source-map。
- 未发现可直接复用的中文数字、政策条款层级、作用域编号连续性或结构 Ground Truth validator。
- 全量政策版本的 normalization annotations 由显式 ANNOTATION mapping 才生成；当前模板没有结构
  标注。另一个 14,960 条 canonical 快照也实测 0 条带 annotation。因此不能计算真实切分正确率。

## 3. Phase A 问题逐项回答

1. **一个 Calculator 能输出四个独立 Flag 吗？** 可以在 Calculator 内部生成四状态并写逐样本
   evidence；不能把它们作为四个 canonical sample Flag 或四个 MetricResult 原生返回。
2. **数据集级如何分别聚合？** Calculator 在一次流式遍历中维护每组
   PASS/FAIL/NOT_APPLICABLE/UNAVAILABLE 计数；各比率只以 PASS+FAIL 为分母，缺陷数单独求和。
3. **需要调整 MetricResult Schema 吗？** 最小方案不需要；新增 measurements 和 evidence schema
   即可。若要求样本 Flag 可直接 API 查询或四个结果有独立 status，才需要版本化扩展。
4. **需要调整 Registry/Binding/Planner 吗？** 最小方案仅新增/版本化 Metric、descriptor、binding，
   Planner 无逻辑改动。不能复用现有 0204@1.0.0 语义。
5. **能否保证同一样本只解析一次 HTML？** 单 Calculator 内能；用局部
   `StructureParseResult` 同时驱动四组判定即可。
6. **需要持久化解析结果吗？** MVP 不需要。它只在单次样本处理内存活；Evidence 只落有界诊断与
   四状态。只有多个独立 Calculator/下游任务复用时，才将其升级为版本化 derived artifact。
7. **最小改造是什么？** 新 Calculator + rules/parser 模块、0204 新语义版本或子指标、descriptor、
   binding、逐样本 evidence schema、测试与开发说明；不改共享 contracts/services。

## 4. 未解决/需确认

- 0204 现有“字段填充完整性”与“政策结构完整性”的指标身份如何处理。
- 四组状态是否必须成为 API 一等对象；若必须，平台 contract 需另立 ADR。
- 文种来源采用 `metadata.extra.policyType`、标题推断还是新增 canonical `document_type`。
- 是否建设人工结构标注集；没有它，`segmentation_correct` 永远只能 UNAVAILABLE。

