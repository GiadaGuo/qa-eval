# ADR-0003：说明文档质量评测采用平台内置流水线与独立报告类型

- 状态：Accepted
- 日期：2026-10-28
- 决策范围：qa-eval 平台说明文档评测链路（DocumentVersion / DocumentReportTask / DocumentQualityReport）与 `dataset-document-evaluator` Skill 的标准归属
- 前置决策：[ADR-0001](0001-skill-metric-ownership-and-shared-evaluation-layers.md)、[ADR-0002](0002-adopt-canonical-data-flow-models-v1.md)
- 架构评估：[说明文档质量评测报告生成 — 架构评估与开发计划](../development/document-quality-report-generation-plan.md)

## 背景

《新增数据集说明文档版本管理与质量评测能力》要求：上传说明文档版本 → 一键生成质量评测报告（4 指标、20 检查项、逐项证据、缺失清单、建议与总分）。该链路与 ADR-0001 三处规则相抵：

1. ADR-0001 §2 禁止建立与 qa-eval 并行的 Dataset、Metric Result、Contract 或 Report canonical schema。
2. ADR-0001 §3 要求正式 Quality Report 必须由平台基于持久化的 Metric Result、Rule Evaluation 和 Quality Issue 生成（共享 Contract/Plan/Run 链路）。
3. ADR-0001 §5 要求 `dataset-document-evaluator` 的私有输出后续必须映射或迁移到平台 Metric Result。

同时，说明文档维度的输入（Markdown 文档）、判定（LLM 语义判断）、执行（分钟级多次 LLM 调用）与现有数据质量评测链路（样本记录、阈值规则、确定性 Calculator）不同构；架构评估结论为方案 C′（混合）：复用 Skill 的 rubric 与确定性校验逻辑，迁移为平台内置流水线，不内嵌 agent 运行时、不直接调用 Skill。

## 决策

### 1. 说明文档评测 MVP 采用平台内置专用流水线，不经 Contract/Plan

评测由平台组件 `DocumentEvaluationService` 编排：解析 → 逐指标 LLM 语义判定（每指标一次结构化调用）→ 程序校验（证据行号、摘录逐字命中、状态枚举、算术复算）→ 程序汇总 → 报告生成。用户零配置即可使用：**通过平台内置默认实现满足 canonical 意图，而非绕过 canonical 模型**。不内嵌 agent 运行时，不直接调用 Skill。

### 2. 结果语义对齐 MetricResultV1

DocumentQualityReport 的指标段（`metric key/version`、`status`、`observations`、证据 artifact）与 `MetricResultV1` 同构，仅持久化位置不同（报告文档内 + ArtifactStore，而非 `metric_results` 表）。指标编码沿用 `0101–0104`（ADR-0001 所有权表已认定其归属说明文档维度），`subject` 语义为 `{scope: "DOCUMENT_VERSION", id}`。`dataset-document-evaluator` Skill 的私有输出以本契约（`DocumentQualityReportV1`）为映射目标。

### 3. DocumentQualityReport 是报告家族中的独立 report kind

本决策不视为对「禁止并行 Report schema」的违反，理由与约束：

- 独立 report kind 是**平台拥有的 canonical 契约**（Pydantic 契约 + JSON Schema），不是 Skill 私有协议；无私有状态枚举、无私有标识格式。
- 输入与判定语义与数据质量报告不同构，强行统一即需求所警告的「为统一制造复杂度」；报告生成纪律与共享层一致——只从已持久化的判定组装，不在报告层重算语义。
- 跨维度综合评测（《数据集维度综合评测》）落地时，将 `metric_results.subject` 泛化为 `subject_type + subject_id`，文档指标结果机械迁入共享表。该迁移明确列为综合评测需求的前置项。

### 4. 评测标准的 canonical 副本位于平台

rubric 从 Skill 迁出，冻结为 `configs/document-evaluation/rubric/1.0.0.json`；报告记录 `evaluation_standard.version` 与内容 sha256，与 `evaluator_version` 分开。`dataset-document-evaluator` Skill 转为该标准的**上游来源**（验证与反馈），不再独立演进评分口径。修订评分口径必须升级标准版本另存新文件，不得原地改写冻结文件。

## 对 ADR-0001 的修订范围（窄幅）

- §3「正式 Quality Report 必须由平台基于持久化的 Metric Result、Rule Evaluation 和 Quality Issue 生成」：说明文档维度 MVP 例外——报告由专用流水线基于持久化的逐项判定与程序复算生成；「不在报告层重算语义」的纪律不变。
- §5「`dataset-document-evaluator` 当前私有输出格式后续也必须映射或迁移到平台 Metric Result」：映射目标明确为 `DocumentQualityReportV1`（MetricResultV1 兼容语义）。
- 其余条款不变，尤其 §2「Skill 不得建立并行 canonical schema」对本维度依然成立（见决策 3 的理由与约束）。

## 后果

正面影响：

- 用户零配置一键获得可追溯的文档质量报告；平台避免引入 agent 运行时依赖。
- 评分口径单一来源（冻结 rubric），Skill 与平台不再双标准。
- 与综合评测保有明确合并路径（subject 泛化 + 机械迁移）。

代价：

- 短期存在两条报告链路（数据质量 + 说明文档），需在文档与 UI 中明示口径差异（总分等权平均为平台口径，非指南规定）。
- 综合评测落地时必须执行 `metric_results.subject` 泛化迁移，成本记入该需求。

## 后续工作

1. P1：DocumentVersion 版本管理（实体、ORM、迁移、Service、API、前端 Tab）。
2. P2：评测流水线（LLM 客户端、解析/校验器移植、任务模型与独立执行器、报告契约与 JSON Schema）。
3. P3：一键生成交互、报告页、历史任务列表。
4. 综合评测前置（backlog）：`metric_results.subject` 泛化与文档指标迁移、evidence-assisted 模式。
