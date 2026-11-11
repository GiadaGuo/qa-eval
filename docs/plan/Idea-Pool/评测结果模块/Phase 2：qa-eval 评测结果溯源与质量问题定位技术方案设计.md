
# Phase 2：qa-eval 评测结果溯源与质量问题定位技术方案设计

## 一、任务定位

本任务是 `qa-eval` 质量评测系统的 Phase 2 技术方案设计。

Phase 1 已完成代码审计，报告位于：

`docs/development/评测结果模块优化/Phase1-现状审计与根因分析.md`

请首先完整阅读 Phase 1 审计报告，再结合当前实际源码进行技术设计。

**本轮核心目标：复用现有 Measurement、Evidence、QualityIssue 等数据模型，打通指标计算结果到异常记录、证据及原文具体位置的完整溯源链路。**

目标链路：

`Quality Contract → Logical Planner → Physical Planner → Stage Runner / Calculator → Measurement + Evidence → QualityIssue → 前端展示`

用户最终应能够：

1. 在评测报告中查看某项指标的计算结果。
2. 查看该指标发现了哪些具体质量问题。
3. 查看哪些 Record 不满足对应质量要求。
4. 查看每个问题对应的 Evidence。
5. 定位到 Record 的具体字段、条款或文本 Span。
6. 在原文中查看异常位置及其判定依据。
7. 对 Pair、Group 等跨记录问题查看关联记录及各自证据。

本轮**不引入 Finding，也不新增其他中间业务模型**。

不以重构整个评测调度架构为目标。

本阶段只输出技术实现方案，不修改业务代码。

---

## 二、当前实际数据集

本次主要测试数据集为：

`data/政务数据集/policy.policy.jsonl`

不是 `jhsjk_article`。

当前数据集的一条 Record 对应一篇政策文章或政务文本。

请通过源码和数据文件核查：

- Record 实际 Schema
- Record ID
- 政策标题、正文等字段
- 正文是否存在 HTML / Plain Text 两种表示
- Dataset Version 与 Artifact 的关系
- 当前 Calculator 实际读取的字段
- Evidence 中的 Record ID、Content ID 和 Span
- 原始数据与标准化 SampleRecord 的映射
- 原文查询 API 的实际实现

不得将此前 `jhsjk_article` 的记录规模、字段结构或历史 Run ID 直接作为当前数据集的事实。

本轮以 `policy.policy.jsonl` 验证，但所有通用设计必须保持数据集无关性。

---

## 三、核心设计原则

### 3.1 复用现有数据模型

当前系统已经具有：

- Quality Contract
- Calculator Registry / Binding
- Logical Plan
- Physical Plan
- Stage Run
- CalculatorOutput
- MetricResult / Measurement
- Evidence Artifact
- RuleEvaluation
- QualityIssue
- QualityIssueIndex
- QualityReport / ScoredReport

**不新增 Finding，不新增与 Evidence 或 QualityIssue 职责重叠的业务实体。**

优先通过扩展现有 Schema、Evidence 结构、Issue 生成逻辑和查询 API 实现。

不强制新增数据库表。

如果确实需要持久化索引，应优先评估现有机制是否可扩展；只有证明现有能力不足时，才能提出新的技术索引表，并说明其不是新的业务模型。

### 3.2 Measurement、Evidence、QualityIssue 职责分离

**Measurement**

负责指标计算值，例如：

- 异常数量
- 异常记录数
- 异常比例
- 重复率
- 覆盖率

Measurement 不承担全部异常明细存储。

**Evidence**

负责保留检测依据及其原文定位信息。

可以包含：

- Record ID
- Content ID
- Field Path
- Clause Path
- Span
- 检测类型
- 检测方法
- 脱敏片段
- 相关记录
- 统计依据

Evidence 可以是 JSONL Artifact，不要求全部进入数据库。

**QualityIssue**

负责将检测证据转化为可解释、可追溯的质量问题。

复用：

- scope
- subject
- related_subjects
- locator
- evidence references
- determination
- severity
- status
- dedup_key

三者不是一一对应关系。

允许：

- 一个 Measurement 对应多个 QualityIssue。
- 一个 QualityIssue 关联多个 Evidence。
- 多个 Evidence 共同支持一个跨记录问题。
- 某些 Evidence 只是检测候选，不构成确认违规。
- 没有可靠记录级证据时，只生成 Dataset-level Issue。

### 3.3 Calculator 执行粒度与 Issue 定位粒度分离

Calculator 可以采用：

| 执行粒度    | 含义                 | 示例                     |
| ----------- | -------------------- | ------------------------ |
| Record      | 单条记录独立计算     | 敏感要素、编码、条款解析 |
| Pair        | 两条候选记录比较     | 标题相似、正文冲突       |
| Group       | 多条关联记录联合计算 | 重复簇、政策版本链       |
| Dataset     | 全库统计或全局索引   | 重复检测、噪声分布       |
| Aggregation | 汇总前序计算结果     | 异常率、缺陷数量         |

但 Calculator 执行粒度不能直接决定 QualityIssue 的 scope。

例如：

- Dataset Calculator 发现具体异常记录，可以生成 SAMPLE/CONTENT 级 Issue。
- Pair Calculator 可以生成关联双方 Record 的 Issue。
- Group Calculator 可以生成 GROUP Issue 并关联成员记录。
- Aggregation 可以通过上游 Evidence 引用保留异常来源。

不得简单使用：

`Calculator scope == Issue scope`

作为 Issue 生成规则。

### 3.4 Calculator 与 Stage 不一一对应

整体架构应遵循：

1. 多个 Record Calculator 可以共享 Record Stage 的数据读取与预处理。
2. Logical Planner 根据 Contract Binding 和依赖关系动态规划 Stage。
3. Dataset Calculator 不一定依赖 Record Stage。
4. Stage 可以承载一个或多个 Calculator，具体由实际依赖及执行计划决定。
5. 不同 Run 复用 Artifact 时，必须校验 Dataset Version、输入范围、Calculator 版本、参数及产物完整性。

**以上作为架构兼容性约束，本轮不要求立即重构 Planner、Stage 调度或跨 Run Artifact 缓存。**

若现有代码不满足上述原则，请记录为后续改进项，不要扩大本轮实施范围。

---

## 四、Phase 1 已确认的关键事实

Phase 1 审计发现：

1. 0202 Calculator 已输出包含 sample_id、content_id、field、span、实体类型及脱敏片段的 Evidence JSONL。
2. Evidence Artifact 已保存到 Artifact Store。
3. MetricResult 已保留 Evidence 元数据及引用。
4. 记录级信息没有在 Calculator 或 MetricResult 阶段丢失。
5. 信息断裂主要发生于 QualityIssue 生成阶段。
6. 通用阈值 Issue 只读取 Measurement 和第一个 Evidence ID。
7. 通用 Issue 默认生成 DATASET scope 和 measurement locator。
8. QualityIssue Schema 已支持 SAMPLE、CONTENT、GROUP、PAIR 等 scope。
9. QualityIssue 已支持 related_subjects 和多个 Evidence 引用。
10. 0208 已有专用的记录级 Issue 物化逻辑。
11. 前端已有 Issue 分页和 SAMPLE 原文弹窗。
12. 当前原文查询通过 JSONL 线性扫描，缺少高效 Record ID 索引。
13. Evidence 缺少统一的行级查询 API。
14. 通用阈值 FAIL 会直接生成 CONFIRMED Issue，没有充分区分检测候选和确认违规。

这些是本轮技术设计的主要依据。

不要重复设计 Calculator 已经具备的证据抽取能力。

---

## 五、核心技术设计任务

### A. 审计并规范 Evidence 定位结构

请审查：

- CalculatorOutput
- EvidenceOutput
- EvidenceArtifact
- MetricResultV1
- QualityIssueV1
- QualityIssueIndexV1
- 0202 Evidence JSONL
- 0204 Evidence
- 0206 相关实现
- 0208 Evidence / Issue 物化

重点回答：

1. 当前 Evidence 是否已经包含足够的 Record 定位信息？
2. 不同 Calculator 的 Evidence 行结构有哪些差异？
3. 哪些定位字段可以统一？
4. 哪些字段必须保留为指标专属字段？
5. 是否需要扩展现有 Evidence schema_ref 或行结构？
6. 是否需要规范现有 QualityIssue.locator？
7. 如何保持历史 Evidence 兼容？

优先采用：

**现有 Evidence Artifact + 统一最小定位字段 + 指标专属扩展字段。**

不新增 Finding Schema。

建议统一的定位信息包括：

- sample_id / record_id
- content_id
- field_path
- clause_path（适用时）
- start_offset / end_offset（适用时）
- coordinate_space
- text_version
- related_sample_ids（适用时）
- group_id（适用时）

以上只是候选字段。

必须根据现有 Schema 决定最终字段名称，不允许无必要地重复定义同义字段。

特别明确：

- Unicode code point
- UTF-8 byte offset
- JavaScript UTF-16 offset
- HTML visible text
- normalized plain text
- raw HTML

之间的区别。

如果当前 Evidence 只能定位 normalized plain text，不得假装已经支持 raw HTML 精确定位。

### B. 优化 Evidence → QualityIssue 生成机制

这是本轮最核心的任务。

当前 `_rule_failure_issues()` 主要根据 Measurement 阈值失败生成通用 Dataset-level Issue。

请设计在现有 QualityResultService 基础上的扩展机制。

要求：

1. 根据真实 Evidence 识别异常对象。
2. 根据 Evidence 的 subject/locator 确定 Issue scope。
3. 支持一个 MetricResult 生成多个 QualityIssue。
4. 支持一个 QualityIssue 关联多个 Evidence。
5. 保留 Evidence Artifact 的原始引用。
6. 保留具体 Record、字段、条款、Span。
7. 支持 Pair/Group 的 related_subjects。
8. 没有可靠记录级证据时允许 Dataset-level Issue。
9. 支持确定性去重与幂等重建。
10. 不破坏已有 0208 逻辑。

请比较：

- 在 QualityResultService 中直接扩展
- 通过可注册 Issue Builder / Detector 扩展
- 通过 Evidence Schema 驱动的通用映射扩展

推荐最小侵入式方案。

允许新增轻量的 Issue Builder 接口或策略类，但**不得新增 Finding 等中间业务模型**。

#### B1. RuleEvaluation 与 Evidence 的关系

明确：

- RuleEvaluation 判断 Measurement 是否满足 Contract。
- Evidence 记录具体检测事实。
- QualityIssue 负责表达需要治理的问题。

不能简单认为：

`Rule FAIL → 所有 Evidence 都是确认违规`

也不能认为：

`Evidence 命中 → 一定生成 CONFIRMED Issue`

请设计：

- 阈值失败但没有记录级 Evidence
- 阈值失败且有多个记录级 Evidence
- 有检测候选但规则未失败
- 部分扫描下出现异常
- 检测结果待复核
- 多个 Evidence 对应同一异常

等场景的 Issue 生成规则。

尤其需要区分：

**检测事实、规则判定、业务违规认定。**

#### B2. Issue 粒度映射

至少设计以下情况：

**Record**

一个 Record 有一个或多个异常 Span。

Issue 应能够定位到具体 Record，并关联对应 Evidence。

**Pair**

两条 Record 存在冲突或高度相似。

Issue 应关联双方 Record，并保留双方的定位信息。

**Group**

多个 Record 构成重复簇或版本链异常。

Issue 应保留 Group ID、成员 Record 和关联证据。

**Dataset**

全局统计指标不满足要求。

如果能够定位具体异常 Record，应允许进一步下钻；否则保留 Dataset-level Issue。

**Aggregation**

汇总结果不满足要求。

应尽可能引用上游 Evidence，不因聚合而丢失溯源关系。

请明确：

- 每类 Issue 的 subject
- scope
- related_subjects
- locator
- evidence references
- dedup_key
- 统计口径

不要求每条 Evidence 必须生成一个 Issue。

应避免一个异常在 Dataset Issue 与 Record Issue 中重复计数。

### C. Evidence 行级查询与引用设计

当前 Evidence 以 JSONL Artifact 保存，但前端只能看到 Evidence ID。

请设计：

1. 根据 Evidence ID 获取元数据。
2. 根据 Evidence ID 查询具体证据行。
3. 根据 QualityIssue 查询关联 Evidence。
4. 根据 Record ID 查询相关 Evidence。
5. 支持 Evidence 行级分页。
6. 支持字段、类型、记录等过滤。
7. 支持 Evidence 与 Issue 之间的稳定关联。

特别注意：

**EvidenceArtifact ID 与 Evidence JSONL 中的单条证据不是同一个概念。**

必须明确如何引用具体证据行。

可以评估：

- Artifact ID + row locator
- Artifact ID + line number
- Artifact ID + byte offset
- 其他稳定行定位方式

请比较可行性、幂等性和存储成本。

不预设必须新增 Evidence 数据库表。

优先复用 Artifact Store 和现有查询基础设施。

如果需要技术索引，应明确：

- 索引内容
- 构建时机
- 查询方式
- 一致性
- 清理机制
- 与 QualityIssueIndex 的关系

不得把 Evidence 全量复制到 QualityIssue 中。

#### C1. Evidence 完整性

当前 0202 的 `evidence_limit=100` 会截断实际保存的证据行，但不截断 Measurement 统计。

请设计如何保证：

- 统计值与 Evidence 总量关系明确。
- 完整异常证据可追溯。
- 报告预览可以限制数量。
- 大量证据不导致 Report JSON 膨胀。

如果仍需设置持久化上限，必须明确告知证据不完整，不能声称所有异常均可追溯。

### D. Record 原文定位

当前数据集：

`data/政务数据集/policy.policy.jsonl`

一条 Record 对应一篇政策文章。

目标：

**QualityIssue → Evidence → Record → 字段 / 条款 / Span → 原文位置**

请设计：

#### D1. Record 定位

- Record ID 与 SampleRecord ID 的关系
- Dataset Version 与 Record 的关系
- Evidence 中的 Record ID 如何定位标准化记录
- 原始 JSONL 与标准化 Artifact 如何关联
- 跨 Dataset Version 如何避免定位错误

#### D2. 原文查询性能

当前 `find_jsonl_record()` 为线性扫描。

请比较：

- JSONL byte-offset 索引
- 分片 JSONL
- 现有数据库索引扩展
- 其他轻量方案

优先采用最小侵入式方案。

需要考虑：

- Local Artifact Store
- S3 / 对象存储
- 文件压缩
- Range Read
- 数据集版本
- 索引失效
- Artifact 删除

不要求本轮重构全部数据集存储。

但必须保证新方案不会让大量 Issue 的原文查询成为明显性能瓶颈。

#### D3. 原文精确位置

针对不同证据：

- Record：定位政策文章。
- Field：定位具体字段。
- Clause：定位条款。
- Span：高亮文本范围。
- Pair：分别定位双方文本。
- Group：查看关联记录及对应位置。

明确：

- 文本坐标系
- 字段选择
- Span 边界
- 文本版本一致性
- 脱敏处理
- 定位失败的降级方案

如果没有可靠 Span，允许只定位到字段或条款，不得虚构精确位置。

### E. 0202 安全规范性落地案例

以：

`0202@2.0.0`

Calculator：

`bg_06_01_sensitive_entity_detection@1.0.0`

为首个落地案例。

保留现有敏感实体检测算法。

本轮重点设计：

1. 读取现有 Evidence JSONL。
2. 解析具体敏感实体命中。
3. 关联对应 Record。
4. 生成 SAMPLE/CONTENT 级 QualityIssue。
5. 保留实体类型、字段及 Span。
6. 关联具体 Evidence 行。
7. 提供中文问题说明。
8. 支持前端原文高亮。
9. 区分待复核与确认违规。
10. 兼容现有 Measurement 和报告。

例如：

MetricResult：

`sensitive_entity_hit_count = 3`

Evidence：

- Record A：正文手机号
- Record B：正文身份证号
- Record B：另一处手机号

QualityIssue 可以根据实际业务规则生成：

- Record A 的疑似敏感信息问题。
- Record B 的疑似敏感信息问题。

Record B 可以关联两条 Evidence。

**以上只是设计示例，不要求强制一个 Record 对应一个 Issue。**

请根据现有 QualityIssue scope、dedup_key 和业务规则确定合理粒度。

同时明确：

如果检测到手机号但尚未经过业务审核，默认应如何表达候选问题，而不是直接标记为确认违规。

### F. QualityReport 与前端展示

保持现有 Report 汇总结构。

不要把大量 Evidence 原文直接写入 Report JSON。

#### F1. Report

至少提供：

- 指标 Measurement
- RuleEvaluation
- Issue 数量
- 受影响 Record 数量
- Evidence 引用
- 数据扫描范围
- 结果限制说明

应区分：

- Issue 数量
- Evidence 命中数量
- 受影响 Record 数量

三者不能混用。

#### F2. 前端

主要页面：

`apps/web/src/pages/EvaluationResultsPage.tsx`

请复用已有：

- 指标卡片
- Issue 列表
- 分页
- 指标筛选
- 样本原文弹窗

新增或扩展：

1. 点击指标查看具体 QualityIssue。
2. 点击 Issue 查看关联 Evidence。
3. 点击 Evidence 查看对应 Record。
4. 自动定位字段或条款。
5. 在原文中高亮异常 Span。
6. 支持一个 Issue 多条 Evidence。
7. 支持 Pair / Group 关联记录切换。
8. 显示检测方法、问题说明及复核状态。
9. 证据缺失时给出明确提示。
10. 对敏感信息进行安全脱敏。

技术 ID、Artifact URI 等应放在技术详情区域，不应代替业务问题说明。

### G. 扫描状态与评分的边界处理

Phase 1 还发现：

- `max_records=20` 默认只检测前 20 条。
- 部分扫描仍标记 `OK`。
- RuleEvaluator 可能对部分扫描结果进行全局阈值判断。
- 0202 v2 与运营 Scoring Profile 不匹配。

这些问题确实影响结果可信度，但**本轮不以重构统一扫描调度和评分体系为目标**。

请设计最小必要的正确性保护：

1. 报告明确区分扫描记录数与总体记录数。
2. 部分扫描不得被展示为全量通过。
3. RuleEvaluation 不应无条件将样本统计解释为总体质量结论。
4. 局部发现可以作为局部证据保留。
5. 评分配置不匹配时应明确标注原因。
6. 不得为 0202 v2 伪造正式评分。

如需要大规模修改 Planner、Executor、Calculator 批处理或 Scoring Profile，请单独列入后续优化计划。

不要把这些改造作为本轮结果溯源功能上线的强制前置条件，除非存在无法安全绕过的正确性阻塞。

---

## 六、Schema 与 API 设计要求

优先扩展现有 Schema。

对每项修改说明：

- 现有字段
- 新增或扩展字段
- 修改原因
- 数据类型
- 是否必填
- 版本兼容性
- 历史数据处理

重点检查：

- EvidenceArtifact
- Evidence JSONL 行结构
- QualityIssueV1
- Issue Locator
- related_subjects
- IssueIndex
- QualityReport

不要为了统一命名而无必要地修改既有字段。

API 至少覆盖：

- 按 Run 获取 QualityIssue
- 按 Issue 获取关联 Evidence
- 按 Evidence 获取具体证据行
- 按 Record ID 获取标准化记录
- 按 Evidence 定位原文位置

明确：

- HTTP Method
- URL
- 请求参数
- 响应结构
- 分页方式
- 权限控制
- 脱敏策略
- 错误码
- 对应 Handler

提供一组完整的端到端 JSON 示例。

示例必须标记为模拟数据，不得伪装为真实运行结果。

---

## 七、实施范围与阶段

请将技术方案拆分为以下阶段，并根据依赖关系调整。

### Phase 2.1：Evidence 与 Issue 关联机制

- 审计现有 Evidence 行结构。
- 规范最小定位字段。
- 设计 Evidence → QualityIssue 转换。
- 设计 Issue Builder 扩展机制。
- 保持 0208 兼容。

### Phase 2.2：0202 记录级问题物化

- 解析 0202 Evidence。
- 生成记录级 Issue。
- 关联 Evidence 行。
- 保留 Record、字段和 Span。
- 区分候选与确认违规。
- 提供中文问题说明。

### Phase 2.3：Evidence 查询与原文定位

- Evidence 行级查询 API。
- Evidence 查询索引。
- Record ID 查询优化。
- 字段、条款、Span 定位。
- 权限和脱敏。

### Phase 2.4：报告与前端展示

- 指标到 Issue 的下钻。
- Issue 到 Evidence 的下钻。
- Evidence 到 Record 原文的下钻。
- 原文高亮。
- Pair / Group 关联记录展示。
- 问题统计口径。
- 历史报告兼容。

### Phase 2.5：端到端测试与验收

- 0202 完整链路测试。
- Record / Pair / Group / Dataset 定位测试。
- Evidence 完整性测试。
- Issue 去重测试。
- 原文查询性能测试。
- 历史兼容测试。
- 部分扫描正确性测试。

对于每个阶段，请给出：

1. 开发目标。
2. 依赖条件。
3. 具体修改文件。
4. 需要扩展的 Schema。
5. API 变更。
6. 实现步骤。
7. 测试用例。
8. 验收标准。
9. 风险和回滚方式。

本轮优先完成 Record 场景的真实端到端闭环。

Pair / Group / Dataset 场景应完成通用协议和关键测试设计；如涉及大量额外工作，可以作为后续增量实施，不要求本轮一次性完成所有专属前端交互。

---

## 八、必须回答的架构问题

最终技术方案必须回答：

1. 现有 Evidence JSONL 是否足以承担记录级异常证据？
2. 是否可以完全不新增 Finding？
3. Evidence Artifact 与单条 Evidence 如何稳定关联？
4. 一个 Measurement 对应多个 Issue 应如何实现？
5. Issue scope 应如何根据 Evidence 确定？
6. 如何复用现有 QualityIssue 的 locator 和 related_subjects？
7. 如何避免在 QualityResultService 中堆积指标专属逻辑？
8. 如何保证 Rule FAIL 不直接等于确认违规？
9. 如何实现 Issue → Evidence → Record → Span？
10. 如何避免每次原文查询都线性扫描 JSONL？
11. 如何保持历史 MetricResult、Issue、Report 兼容？
12. 如何保证本轮不演变成 Planner、Stage 和数据存储体系的全面重构？

每个问题必须提供：

- 推荐方案
- 依据
- 影响文件
- 主要风险
- 备选方案（必要时）

---

## 九、输出文档

请在以下目录生成：

`docs/development/评测结果模块优化/`

输出三份文档：

### 1. Phase2-结果溯源技术架构设计.md

包括：

- 当前问题与改造目标
- 现有模型复用清单
- 目标数据流
- Evidence → QualityIssue 机制
- Record / Pair / Group / Dataset 定位方案
- Mermaid 架构图
- 关键技术决策
- 与现有架构的兼容关系

### 2. Phase2-Evidence与Issue-Schema及API设计.md

包括：

- 现有 Schema 审计
- 字段级扩展方案
- Evidence 行引用方式
- Issue Locator 规范
- Issue Builder 接口
- API 定义
- JSON 示例
- 索引与存储设计
- 原文定位机制
- 安全与脱敏

### 3. Phase2-分阶段实施与验收计划.md

包括：

- 阶段划分
- 文件级修改清单
- 实施顺序
- 依赖关系
- 测试用例
- 性能验证
- 验收标准
- 风险与回滚
- 后续非本轮优化事项

三份文档必须使用一致的数据模型、术语和接口设计。

---

## 十、工作边界

本阶段允许：

- 阅读源码
- 阅读 Phase 1 报告
- 阅读 Registry / Binding
- 阅读已有测试
- 只读检查政策 JSONL
- 静态分析
- 新增技术设计文档

禁止：

- 修改业务代码
- 修改数据库
- 修改 Registry / Binding
- 执行迁移
- 运行大规模评测
- 修改前端
- 调用付费模型
- 删除或覆盖 Artifact
- 新增 Finding 或其他中间业务模型

如发现现有架构确实存在无法安全绕过的阻塞，应在方案中明确说明，不得擅自扩大改造范围。

**本阶段仅交付技术实现方案，不开始代码开发。**

完成后输出：

1. 三份设计文档路径。
2. 最关键的架构决策。
3. 现有模型中需要扩展的字段。
4. 推荐实施顺序。
5. 尚未解决的技术风险。
6. 是否存在阻塞本轮开发的问题。

现在请基于 Phase 1 审计报告及当前源码，开始 Phase 2 技术方案设计。
