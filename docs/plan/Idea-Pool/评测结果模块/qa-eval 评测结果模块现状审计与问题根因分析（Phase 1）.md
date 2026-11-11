
# 任务：qa-eval 评测结果模块现状审计与问题根因分析（Phase 1）

## 一、任务目标

对当前 `qa-eval` 项目的评测结果模块进行完整的代码审计，重点以 `0202@2.0.0 · 安全规范性` 指标为案例，梳理从数据读取、Calculator 执行、MetricResult 生成，到 RuleEvaluation、QualityIssue、Evidence、QualityReport、ScoredReport 及前端展示的完整链路。

**本阶段仅进行现状审计和根因分析，不修改业务代码、不执行数据库迁移、不进行架构重构，也不输出详细技术实现方案。**

本阶段的目标是回答：

1. 当前系统已经具备哪些能力？
2. 哪些能力存在但没有正确连接？
3. 哪些能力完全缺失？
4. 已发现的问题分别产生于哪个环节？
5. 哪些模块可以直接复用？
6. 哪些地方存在需要在第二阶段解决的架构约束？

请以真实代码为依据，不要仅根据需求描述推断。

## 二、审计背景

### 1. 系统架构

当前系统采用：

Dataset Version + Dataset Profile + Quality Contract
→ Logical Evaluation Plan
→ Physical Evaluation Plan
→ Evaluation Run + Stage Runs
→ Metric Result
→ Rule Evaluation
→ Quality Issue
→ Quality Report / Scored Report

需要审计以上各阶段的实际实现，以及它们之间的数据传递和持久化方式。

### 2. 本次重点案例

指标：

`0202@2.0.0 · 安全规范性`

Calculator：

`bg_06_01_sensitive_entity_detection@1.0.0`

Calculator 路径：

`src/qa_eval/calculators/sensitive_entity_detection`

数据集：

`jhsjk_article`

Dataset Version ID：

`01a1a9a4-c688-7a18-a70b-927c73ab24e0`

Run ID：

`01a1c334-10e8-7d92-91bc-5bc01d7c4123`

### 3. 已知运行结果

本次评测结果显示：

- 数据集总记录数：321,902
- 实际评测记录数：20
- 排除记录数：321,882
- 排除原因：`SAMPLED_OUT_MAX_RECORDS`
- `sensitive_entity_hit_count = 1`
- `affected_record_count = 1`
- `sensitive_entity_type_counts = {"MOBILE_PHONE_CN": 1}`
- `scan_coverage_ratio = 1.0`
- `population.evaluated_count = 20`
- `population.input_count = 321902`
- MetricResult 状态：OK
- RuleEvaluation 状态：FAIL
- QualityIssue 类型：`THRESHOLD_VIOLATED`
- QualityIssue Scope：DATASET
- 正式 Score：null
- 运营估计 Score：null

目前 QualityIssue 的主要信息为：

`该指标的已观测值违反发布契约中的阈值规则。`

Evidence Locator 仅包含：

`{"measurement":"sensitive_entity_hit_count"}`

而运营评分配置期待：

`safe_record_ratio`

与当前 Calculator 的主测量 `sensitive_entity_hit_count` 不一致。

以上是已观察到的现象，请通过代码审计验证根因，不要直接将现象当作根因。

---

## 三、审计任务

### A. 0202 数据读取与扫描策略审计

重点回答：

1. `max_records=20` 的实际来源是什么？
2. 这个限制是 Calculator 自身设置，还是 Binding、Contract、Planner、Executor 或数据读取层传入？
3. 当前是否支持全量扫描？
4. 是否已经存在流式读取或分批处理机制？
5. 是否存在统一的抽样策略接口？
6. 当前抽样是前 N 条、随机抽样还是其他方式？
7. `SAMPLED_OUT_MAX_RECORDS` 在哪个模块生成？
8. `scan_coverage_ratio=1.0` 的计算公式是什么？
9. 为什么该值与真实数据扫描比例不一致？
10. 是否有区分数据集总体、适用记录、实际扫描记录及失败记录的统计结构？
11. 目前 Calculator 对大规模文本执行时有哪些内存、性能或模型调用限制？
12. UIE 模型和确定性规则是否采用同一批次执行方式？
13. 当前是否支持断点续跑、失败重试和进度统计？

需要追踪实际调用链，并标注关键文件、函数及代码位置。

### B. Calculator 输出与 MetricResult 审计

检查：

`src/qa_eval/calculators/sensitive_entity_detection`

以及对应的 Calculator Registry、Binding、Measurement 定义和执行器。

重点回答：

1. Calculator 的输入 Schema 是什么？
2. Calculator 的输出 Schema 是什么？
3. 实际输出了哪些 Measurement、Diagnostics 和 Observations？
4. 是否产生记录级命中信息？
5. 如果有，命中结果包含哪些字段？
6. 是否包含 record_id、field_path、entity_type、matched_text、offset、confidence 等？
7. 命中结果如何传递给 MetricResult？
8. 是否存在结果截断或只保留统计值的逻辑？
9. Calculator 是否直接生成 Evidence？
10. Calculator 是否能够声明多个 Measurement？
11. `0202@2.0.0` 与 Calculator Binding 是否完全一致？
12. MetricResult 是否支持部分扫描、语义覆盖和检测限制的表达？
13. 当前哪些结构可以直接复用？

请区分：

- Calculator 实际计算出来的数据
- Calculator 返回的数据
- Executor 接收的数据
- MetricResult 最终持久化的数据

不能只检查最终报告。

### C. Evidence、Finding 与原文追溯能力审计

这是本次审计的重点。

请完整检查：

- Evidence Schema
- Evidence 持久化
- QualityIssue Schema
- QualityIssueIndex
- MetricResult 与 Evidence 的关联
- RuleEvaluation 与 Evidence 的关联
- 原始记录定位能力
- 数据集记录读取 API

重点回答：

1. 当前是否存在独立的记录级 Finding 或类似结构？
2. Evidence 是数据库记录、Artifact 文件还是嵌套 JSON？
3. Evidence 的完整 Schema 是什么？
4. 当前 Evidence Locator 支持哪些定位类型？
5. 为什么 0202 最终只剩下 measurement Locator？
6. 记录级命中信息究竟在哪一层丢失？
7. QualityIssue 能否关联多个 Evidence？
8. 多个记录级异常是否只能聚合成一个 Dataset-level Issue？
9. 当前是否支持通过 Evidence ID 查询完整证据？
10. 是否支持通过 record_id 查询原始记录？
11. 是否支持字段级、文本片段级定位？
12. 是否已经有原文 offset、DOM path 或 span 定位能力？
13. 当前原始数据与标准化数据之间是否保留稳定映射？
14. 如果 Calculator 检测的是 `plain_text_content`，是否可以可靠定位回原始 `content`？
15. 是否有 Evidence 分页、过滤和索引能力？
16. 大量记录级证据是否会造成数据库或报告 JSON 膨胀？
17. 当前是否有敏感信息脱敏、权限控制和访问审计机制？

请明确区分：

- 已经实现且可直接使用
- 已经实现但没有接入 0202
- 仅存在 Schema 或接口但尚未实现
- 完全缺失

### D. 数据集存储与原文查询审计

当前项目历史上采用过：

- `raw_snapshot.jsonl`
- `sample_records.jsonl`
- `dataset_manifest.json`
- `dataset_versions.artifact_refs`
- `dataset_profiles`

其中标准化记录可能主要保存在 Artifact Store 中，而非数据库逐条存储。

请核查当前真实实现，不要假设历史架构仍然完全一致。

重点回答：

1. 原始数据实际存储在哪里？
2. 标准化记录实际存储在哪里？
3. 是否存在记录级数据库表？
4. 是否存在 record_id 索引？
5. 是否存在 JSONL 字节偏移索引？
6. 当前数据集记录预览如何实现？
7. 当前分页查询是否需要从头扫描 JSONL？
8. 是否可以通过 record_id 高效获取原文？
9. 数据集记录 ID 是否跨运行稳定？
10. 标准化记录是否保留原始记录 ID？
11. 数据集版本更新是否会影响证据定位？
12. 当前是否存在记录缓存或其他查询加速机制？
13. 原文定位能力的主要性能瓶颈是什么？

重点评估：

**如果将来 Evidence 只保存 record_id，当前系统能否在不全文件扫描的情况下快速获取对应原文？**

请给出代码证据。

### E. RuleEvaluation 与 QualityIssue 生成链路审计

重点回答：

1. RuleEvaluation 如何读取 MetricResult？
2. 当前阈值规则如何定义？
3. `THRESHOLD_VIOLATED` 在哪里生成？
4. QualityIssue 的 message 如何生成？
5. 是否存在指标专属问题解释模板？
6. 是否存在结构化问题详情？
7. QualityIssue 的 subject 和 related_subjects 如何确定？
8. 为什么当前只生成 Dataset-level Issue？
9. 是否支持 Record-level Issue？
10. Evidence ID 是如何关联到 Issue 的？
11. 是否支持区分检测命中、疑似风险、确认违规和人工复核？
12. `determination=CONFIRMED` 的实际判定依据是什么？
13. 当前修复建议如何生成？
14. 是否已经存在可复用的推荐生成机制？
15. 是否存在问题去重、合并、状态流转和复核机制？

特别检查：

当前系统是否把“敏感实体命中”直接当作“确认安全违规”。

如果存在这种情况，请定位具体规则或代码。

### F. Quality Report 与 Scored Report 审计

检查报告生成器及相关 Schema。

重点回答：

1. Quality Report 如何聚合 MetricResult？
2. 是否保留记录级 Evidence？
3. 是否只保留 Evidence ID？
4. Issue Summary 如何计算？
5. Report 是否支持分页加载具体问题？
6. Report 中的 `population` 与 `coverage_analysis` 是否使用统一口径？
7. `guide_coverage` 是否正确处理指标版本？
8. 为什么报告同时出现 0202@1.0.0 和 0202@2.0.0？
9. 指标实现覆盖率 16/16 的依据是什么？
10. 指标执行覆盖率 1/16 的依据是什么？
11. 观察性覆盖率 1/16 的依据是什么？
12. 有效评分覆盖率 0/0 的依据是什么？
13. 当前报告能否区分执行成功、规则失败和正式评分不可用？
14. Scored Report 如何读取运营评分配置？
15. 为什么运营评分期待 `safe_record_ratio`？
16. 0202@2.0.0 是否已经适配当前 Scoring Profile？
17. 是否存在指标版本与评分配置版本不一致的问题？
18. 哪些评分限制属于刻意设计，哪些属于实际缺陷？

必须区分：

- 原始 Measurement
- RuleEvaluation 结果
- 正式指南评分
- MVP 运营估计评分

不要把缺少正式评分权重直接判断为程序 Bug。

### G. 前端评测结果模块审计

定位当前前端评测结果页面及其组件。

重点回答：

1. 评测总览由哪些组件实现？
2. 覆盖率分析由哪些组件实现？
3. 指标详情由哪些组件实现？
4. 质量问题列表由哪些组件实现？
5. 是否已有证据详情或原文定位组件？
6. 前端通过哪些 API 获取数据？
7. 哪些字段直接来自后端？
8. 哪些字段经过前端二次计算？
9. 英文说明来自后端还是前端？
10. 当前是否有统一的中文枚举映射？
11. 是否支持 Issue 分页、筛选和搜索？
12. 是否支持从 Issue 跳转到原始记录？
13. 当前是否区分执行状态、质量判定状态和评分状态？
14. 当前哪些组件可以直接复用？
15. 前端是否存在因 API 字段缺失导致的信息丢失？

请建立前端组件与后端 API 的对应关系。

### H. 通用性与架构边界审计

当前改造以 0202 为例，但后续还需要支持：

- 0204 结构完整性：条款、编号、文本位置
- 0206 内容一致性：跨记录关联、冲突证据
- 0208 重复内容程度：重复记录组、文本片段

请审计现有架构是否支持：

1. 单记录单证据
2. 单记录多证据
3. 跨记录关联证据
4. 多记录组成的异常组
5. 字段级证据
6. 文本 Span 证据
7. 数据集级统计证据
8. 不同 Calculator 的统一证据输出
9. 单指标评测与综合评测共享结果链路

重点识别通用层与指标专属逻辑的边界。

本阶段只分析现状和扩展约束，不需要设计完整的新 Schema。

---

## 四、审计方法要求

### 1. 必须进行真实代码追踪

对每个重要结论，尽量提供：

- 文件路径
- 类名或函数名
- 关键代码行号
- 调用关系
- 相关 Schema
- 数据流向

不要只给出概念性描述。

### 2. 区分事实与推断

使用以下标识：

- `已验证`：有明确代码或运行结果支持。
- `推断`：根据调用关系推断，但缺少直接证据。
- `待验证`：需要真实数据库、Worker、模型服务或运行环境进一步验证。

如果当前运行环境无法访问数据库或 Artifact Store，应明确说明，不能将无法验证的部分当作事实。

### 3. 优先只读检查

允许：

- 阅读源码
- 阅读配置
- 阅读数据库模型
- 阅读迁移文件
- 阅读测试
- 查看 Git 状态
- 执行不会修改业务数据的静态检查
- 在确保无副作用的情况下执行只读测试

禁止：

- 修改业务代码
- 修改配置
- 执行数据库迁移
- 清空或覆盖数据
- 重新执行大规模评测
- 调用高成本模型服务
- 删除或重建 Artifact
- 自动安装新依赖

### 4. 不要提前实现

本阶段不需要：

- 新建 Finding 表
- 修改 Evidence Schema
- 修改 Calculator
- 修改 Report
- 修改前端
- 修复评分映射

也不要直接输出可执行的代码修改补丁。

---

## 五、输出格式

请在以下目录生成审计报告：

`docs/development/评测结果模块优化/`

文件名：

`Phase1-现状审计与根因分析.md`

报告必须包含以下章节。

### 1. 执行摘要

用不超过 500 字说明：

- 当前最主要的问题
- 最关键的根因
- 已有能力
- 最大架构约束
- 第二阶段最需要优先解决的事项

### 2. 当前完整数据链路

用 Mermaid 绘制真实代码对应的数据流图。

至少覆盖：

Dataset → Reader → Calculator → MetricResult → RuleEvaluation → QualityIssue / Evidence → Report → Frontend

每个节点标注实际模块或函数。

### 3. 关键模块与文件索引

使用表格：

| 模块 | 文件路径 | 核心类/函数 | 当前职责 | 主要问题 |
| ---- | -------- | ----------- | -------- | -------- |

### 4. 问题根因分析

按照 A～H 八个审计方向分别说明：

- 当前实现
- 代码证据
- 问题根因
- 已有可复用能力
- 缺失能力
- 影响范围
- 验证状态

### 5. 当前 Schema 与数据模型

整理：

- Calculator Output
- MetricResult
- Evidence
- QualityIssue
- QualityIssueIndex
- RuleEvaluation
- QualityReport
- ScoredReport

说明它们的实际关联关系和持久化方式。

### 6. 前后端 API 映射

整理：

| 前端功能 | 前端组件 | API | 后端 Handler | 数据来源 | 当前限制 |
| -------- | -------- | --- | ------------ | -------- | -------- |

### 7. 0202 真实执行链路复盘

基于给定 Run ID，结合代码与可访问的运行数据，解释：

- 为什么只扫描 20 条
- 为什么命中 1 个实体
- 为什么生成 Dataset-level Issue
- 为什么 Evidence 无法定位原文
- 为什么规则 FAIL
- 为什么正式评分为空
- 为什么运营估计评分为空

对于无法通过当前环境确认的结论，必须标注待验证。

### 8. 已有能力复用清单

分类：

- 可直接复用
- 需要扩展
- 需要新增
- 存在架构冲突

### 9. 问题优先级与依赖关系

按 P0 / P1 / P2 分类。

说明哪些问题必须先解决，哪些可以独立处理。

### 10. 第二阶段设计决策清单

列出下一阶段必须明确的技术决策。

每项包含：

- 决策问题
- 当前可选方向
- 现有架构约束
- 需要进一步验证的信息
- 可能影响的模块

注意：本章只列出决策问题和候选方向，不展开完整技术实现方案。

### 11. 审计结论

明确回答：

**当前评测结果模块的问题，究竟主要来自 Calculator、执行编排、证据模型、问题生成、报告聚合、评分配置还是前端展示？**

允许多因素结论，但必须指出各因素的具体责任边界。

---

## 六、最终交付

完成审计后：

1. 保存完整 Markdown 报告。
2. 在终端输出报告路径。
3. 输出一份适合直接发送给技术负责人审阅的摘要。
4. 摘要重点包含：
   - 最关键的 5～10 个发现
   - 已有可复用能力
   - 必须新增或扩展的能力
   - 主要架构风险
   - 第二阶段待决策事项
5. 明确说明本阶段没有修改业务代码、数据库或运行数据。

不要开始第二阶段的技术方案设计或代码实现。

本次任务到完成审计报告为止。
