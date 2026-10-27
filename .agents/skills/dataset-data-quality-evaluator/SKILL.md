---
name: dataset-data-quality-evaluator
description: >
  面向高质量数据集“数据质量”维度（0201–0208 及模态子指标）的评测开发 Skill。
  用于设计和验证 Metric Definition、Calculator、Metric Result 与计算证据；
  不负责说明文档指标、模型应用指标或共享 Contract/Report 平台层。
---

# 数据质量评测开发 Skill

## 一、目标与定位

本 Skill 用于在 qa-eval 项目中开发和验证指南“数据质量”维度的指标，包括 0201–0208 及其文本、图像、音频、视频子指标。

指标边界和共享层归属以仓库级 [ADR-0001](../../../docs/adr/0001-skill-metric-ownership-and-shared-evaluation-layers.md) 为准：

- 0101–0104 归 `dataset-document-evaluator`。
- 0201–0208 及其模态子指标归本 Skill。
- 0301–0305 归计划中的 `dataset-model-application-evaluator`。
- Quality Contract、Plan、Run、Rule Evaluation、Quality Issue 和跨维度 Quality Report 属于 qa-eval 共享平台层。

本 Skill 是指标能力的孵化与验证入口，不是独立于 qa-eval 的评测平台。qa-eval 的领域模型和 JSON Schema 是唯一 canonical contract；本 Skill 的定义、Calculator descriptor 和运行产物必须兼容这些协议。若平台协议不足，应版本化优化平台模型，不得在本 Skill 内长期维护平行协议。

当前 canonical data-flow models 的设计基线见 [qa-eval Canonical Data-flow Models v1](../../../docs/architecture/canonical-data-flow-models-v1.md)。在该提案正式落地前，不得把现有 Demo JSON 当作平台正式协议。

## 二、核心设计原则

1. **指南驱动**：所有正式评测指标必须继承指南中的指标定义、编码、权重和计算要求，不得擅自改变原始语义。
2. **定义与实现解耦**：Metric Definition 描述评测什么，Calculator Implementation 描述如何计算。
3. **计算与判定解耦**：Calculator 负责生成客观 Metric Result，Quality Contract 负责配置阈值和判定规则。
4. **平台协议优先**：复用 qa-eval 的 DatasetVersion、DatasetManifest、SampleRecord、Metric Result 等领域模型；实验格式不得反向成为正式协议。
5. **确定性计算**：数据转换和指标计算优先通过可测试实现完成，不允许大模型主观编造指标值。
6. **Agent 辅助开发**：Agent 可以辅助指标设计、Calculator 开发、异常分析和结果解释，但不能替代可验证的计算过程或平台运行状态机。
7. **全过程可追溯**：产物保留数据集版本、运行标识、指标/实现版本、参数、输入指纹和证据引用，并可映射到平台 Evaluation Run / Stage Run。
8. **真实数据优先**：正式报告必须基于真实数据或明确标识的测试数据，不允许用模拟结果冒充真实评测。
9. **覆盖范围透明**：未评测、不适用、计算失败的指标必须明确标记，不得默认记为零分或合格。
10. **渐进式开发**：每次验证最小纵向切片，不在 Skill 内实现 Registry、Planner、Runner、Contract 或 Report 的正式平台生命周期。

## 三、输入

### 3.1 数据集输入

- 数据源连接信息或固定数据快照
- 数据集 Schema
- 字段映射配置
- 数据集版本与来源信息

首个验证数据集：

- 数据源类型：MySQL
- 数据库：policy
- 数据表：jhsjk_article
- 数据内容：文章类文本数据

### 3.2 评测规范输入

- 指南中数据质量维度的指标体系
- qa-eval canonical Metric Definition
- Calculator Implementation 及参数或 descriptor
- 指标适用性与证据要求

## 四、核心工作流

### Stage 1：数据质量指标梳理与映射

目标：将指南中的指标转化为可执行的评测设计。

执行任务：

1. 提取指南中的 0201–0208 及其模态子指标。
2. 保留指标编码、名称、维度、权重和原始要求。
3. 判断指标对当前数据集的适用性。
4. 分析每个指标需要的数据、业务声明和其他证据。
5. 设计具体的计算方法、参数及输出格式。
6. 区分完整实现、部分实现和未实现的指标。

输出：

- 指南指标清单
- Metric Definition
- 指标与 Calculator 映射表
- 指标适用性及证据需求清单

验收要求：

- 数据质量维度指标无遗漏。
- 计算方法可追溯至指南要求。
- 不将代理指标直接等同于完整指南指标。

### Stage 2：数据准备与标准化

目标：将真实数据集转换为统一评测输入。

执行任务：

1. 读取数据源 Schema。
2. 固定数据快照及版本。
3. 根据字段映射生成 SampleRecord。
4. 记录数据转换失败的样本及原因。
5. 读取或生成符合 qa-eval canonical schema 的 Dataset Manifest。
6. 统计数据规模、字段分布和基础数据概况。

输出：

- Raw Snapshot
- Standardized SampleRecord
- Rejected Records
- Dataset Manifest
- Source Profile

验收要求：

- 数据规模可核对。
- 原始 NULL、空字符串等信息不被无依据地混淆。
- 输入版本、字段映射和文件指纹可追溯。
- 不使用少量 Preview 数据冒充完整数据集。

### Stage 3：指标计算

目标：依据 Metric Definition 执行具体 Calculator。

执行任务：

1. 加载指标定义和计算实现。
2. 校验计算参数与输入数据。
3. 执行确定性指标计算。
4. 记录分子、分母、指标值及必要证据。
5. 记录 Calculator 版本、参数和执行状态。
6. 对无法计算的指标保留明确原因。

输出：

- Calculator Implementation
- Metric Result
- Calculation Evidence
- Execution Metadata

验收要求：

- 每个指标的计算过程可解释。
- 相同输入与参数产生一致结果。
- 计算失败不得伪装成质量不合格。

### Stage 4：平台兼容性与局部闭环验证

目标：验证 Metric Result 能被 qa-eval 共享 Contract/Run/Rule/Report 层消费，而不在本 Skill 内定义这些共享模型。

执行任务：

1. 使用平台 schema 校验 Metric Definition、Calculator descriptor、Metric Result 和证据索引。
2. 核对结果能通过 `dataset_version_id`、`run_id`、指标版本和 Calculator 版本关联。
3. 在共享平台服务可用时，将结果交给 Contract/Rule Evaluation 层验证。
4. 平台服务尚未实现时，只生成明确标识为本地验证的 execution metadata，不复制正式生命周期。

输出：

- Schema Validation Result
- Platform Compatibility Result
- Local Execution Metadata（仅在平台执行服务尚未可用时）

验收要求：

- Skill 产物不存在与平台同名但语义不同的字段。
- 计算失败保持为 Metric Result 的错误状态，不转换成质量 FAIL。
- 本地验证产物不得冒充正式 Evaluation Run、Rule Evaluation 或 Quality Report。

## 五、数据流转约定

本 Skill 负责的数据流：

Raw Dataset
→ qa-eval DatasetVersion / DatasetManifest / SampleRecord
→ 数据质量 Metric Definition + Calculator Implementation
→ qa-eval Metric Result + Calculation Evidence
→ 交付 qa-eval 共享平台层

平台后续流转为 `Quality Contract → Plan → Evaluation Run / Stage Run → Rule Evaluation → Quality Issue / Quality Report`，不由本 Skill 独立定义或持久化。

要求：

1. 每个阶段保留结构化输入和输出。
2. 产物之间通过平台定义的数据集版本、运行标识、指标及 Calculator 版本关联。
3. Metric Definition、Calculator Implementation 和 Metric Result 必须保持独立；平台 Quality Contract 只作为外部输入或后续消费者。
4. Skill 产物必须兼容 qa-eval canonical schema；“尽可能兼容”不作为验收标准。
5. 高基数样本特征和证据使用文件产物，由 Metric Result 通过 URI/指纹引用，不强行内嵌或逐条写入数据库。

## 六、Skill 执行规则

当用户要求执行数据集质量评测时：

1. 首先确认数据集、评测范围及已有产物。
2. 检查当前阶段的输入是否齐全。
3. 优先复用已有产物，避免不必要的重复计算。
4. 按 Stage 1 至 Stage 4 的依赖顺序执行。
5. 每完成一个阶段，检查产物和验收条件。
6. 遇到缺失的指标口径、阈值或证据时，不得自行编造。
7. 在需要访问真实数据库、修改源数据或执行高成本操作前，先获得用户确认。
8. 输出执行摘要、产物路径、schema 校验结果、成功与失败情况，以及下一步建议。
9. 发现平台模型缺失或冲突时，记录协议差异并提出平台级版本化修改；不得用 Skill 私有 schema 静默绕过。
10. MVP 阶段可接收 Skill 定义的 `ContractPublishRequest` JSON，由 publisher 补齐平台 ID、fingerprint、状态和发布时间；不得让用户直接伪造已发布 Contract 的平台管理字段。

## 七、安全与质量约束

- 不擅自连接生产数据库。
- 不修改原始数据。
- 不读取、打印或泄露数据库凭据。
- 不在日志或报告中无必要地暴露敏感原始文本。
- 不伪造数据、证据、指标值或执行结果。
- 不以模拟数据的测试结果冒充真实评测结果。
- 不将 NOT_APPLICABLE、NOT_EVALUATED、ERROR 混为一类。
- 不对不完整的数据质量指标覆盖范围生成误导性综合得分。
- 不代表说明文档或模型应用维度给出结论。
- 不在本 Skill 内发布正式 Quality Contract、Rule Evaluation、Quality Issue 或跨维度 Quality Report。
- 发生阻塞时，明确说明原因及所需补充输入。

## 八、当前开发状态

当前 Skill 已完成首个数据质量指标到 MetricResult 的 canonical 本地计算链路，并具备从用户 QualityContract 生成 Rule、Issue、IssueIndex 和 QualityReport 的单指标执行器；真实报告仍需用户 Contract，正式平台 Run 集成仍依赖共享 Registry、Planner 和 Runner。

已具备：

- qa-eval 项目现有 Python 环境
- MySQL Schema / Preview 基础能力
- FieldMappingConfig
- MySQLRecordAdapter
- SampleRecord 基础协议
- DatasetVersion / DatasetManifest 部分领域模型
- 全量固定数据快照及其指纹
- 02080103 Calculator v2.0.0、Metric Result 草案和计算证据
- 02080103 对 SampleRecord 2.0、MetricResult 1.0 和 EvidenceArtifact 的本地闭环
- 02080103 指标专属 evidence schema 与全量验证器
- Calculator 与快照脚本测试

尚未具备：

- 数据质量维度完整可执行指标映射
- MetricDefinition、CalculatorDescriptor、NormalizationRun 的 executable contract/schema
- 02080103 对正式 EvaluationRun / StageRun 的 referential integrity
- 共享平台 Contract、Planner、Runner、Rule Evaluation、Issue 和 Report 能力

当前优先任务：

**补齐共享 Registry/Run contracts，再将已经通过 LOCAL_VALIDATION 的 02080103 接入正式平台生命周期。**

未经用户明确要求，不在本 Skill 内开发正式系统的 Registry、Planner、Runner、Contract/Report 生命周期或前端页面。
