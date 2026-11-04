
# 任务：推进质量评测平台 Logical Evaluation Plan MVP 开发

## 一、背景与目标

当前项目是高质量数据集质量评测平台，已具备一定的 Metric Definition、Calculator Implementation、Quality Contract、Evaluation Run、Metric Result 等基础能力，并正在开发数据集维度综合评测。

现有系统支持单指标评测，后续需要支持数据集维度的多指标综合评测，以及规则算子、专用模型算子、LLM 算子的混合执行。

本次开发重点是 **Logical Evaluation Plan（逻辑评测计划）**。

目标是将 Quality Contract 中声明的多个指标及其计算实现，解析成结构化、可验证、可扩展的 Logical Plan，明确：

1. 需要评测哪些指标。
2. 每个指标使用哪个 Calculator Implementation。
3. 各指标需要哪些数据输入及中间结果。
4. 指标之间是否存在依赖关系。
5. 哪些计算可以共享，尤其是多个语义指标共享一次 LLM 推理。
6. 逻辑计算结果如何映射回独立的 Metric Result。
7. 如何为后续 Physical Plan 和 Stage DAG 提供稳定的输入。

本阶段不要求实现完整的 Physical Planner、DAG 调度器或 Agent Runtime 生产化。

## 二、首先审查现有项目

请先阅读项目中的架构文档、开发计划、现有代码和数据库模型，重点检查：

- Metric Definition 与 Calculator Implementation 的注册和关联方式。
- Quality Contract 的结构及其对单指标、多指标的支持情况。
- Dataset Version、Dataset Profile、Field Mapping 等数据输入能力。
- Evaluation Run、Stage Run、Metric Result 的现有实现。
- Calculator 的输入输出接口、参数配置和执行流程。
- 当前数据集维度综合评测的实现情况。
- Agent 推荐指标、构建契约、提交任务的现有调用边界。
- 已注册的规则、文本指标及占位 Calculator。
- 现有测试体系和数据库迁移规范。

**优先复用已有实现，不要根据概念名称重新创建一套平行架构。**

如果当前 Contract 已经支持多指标，应直接在其基础上扩展，不要重新设计 Contract。

同时保留现有单指标评测入口，不得破坏已有评测闭环。

## 三、Logical Plan 的核心设计

建议将 Logical Plan 设计为独立的领域层或应用服务，而不是放在 Agent 或具体 Calculator 内部。

### 1. Contract 解析

输入至少包括：

- Dataset Version
- Dataset Profile（如果已存在）
- Quality Contract
- Metric Definition
- Calculator Implementation
- Calculator Parameters

输出 Logical Evaluation Plan。

要求：

- 验证指标是否已注册。
- 验证 Calculator 是否可用。
- 校验指标与数据类型、模态、字段的适配关系。
- 解析 Calculator 的输入依赖。
- 识别指标之间的计算依赖。
- 处理未实现、不可用、不适用的指标。
- 输出明确的规划错误和原因。

对于尚未实现的占位 Calculator，不得将其视为真实可执行算子。

### 2. Logical Plan 数据结构

请根据项目现有领域模型设计，不必机械采用以下字段。

建议至少表达：

- plan_id / plan_version
- contract_id / contract_version
- dataset_version_id
- metric bindings
- calculator bindings
- logical operations
- input dependencies
- output bindings
- shared computation groups
- dependency edges
- planning diagnostics

Logical Plan 应明确区分：

- Metric：要评价什么。
- Calculator：用什么方法评价。
- Logical Operation：逻辑上需要完成什么计算。
- Shared Computation：哪些指标可以复用一次计算。
- Output Binding：计算结果对应哪个 Metric Result。

Logical Plan 应尽可能保持声明式，不直接包含具体 Worker、队列、并发度、设备等物理执行细节。

### 3. 共享计算识别

这是本次 MVP 的重点。

考虑以下三个文本指标：

- 02080102 知识信息密度
- 02080105 信息缺失程度
- 02080107 文本连贯程度

三个指标都可能使用同一文本字段，通过 LLM 进行语义判断。

系统应能够识别它们是否具备共享推理的条件，并在 Logical Plan 中表示为一个共享语义计算组。

但必须注意：

**共享输入字段不代表一定可以共享 LLM 调用。**

至少考虑以下条件：

- 是否使用兼容的 Provider / Model。
- 是否针对相同的数据记录或抽样集合。
- 是否具备兼容的 Prompt / Rubric 组合方式。
- 是否具有相同的数据预处理需求。
- 是否有相容的上下文长度和隐私要求。
- 是否允许共享执行。
- 是否能够从共享输出中独立提取各指标结果。

对于不能安全合并的指标，应保持独立执行。

不要为了减少调用次数而牺牲评测语义、可追溯性或正确性。

本阶段可以采用确定性的共享分组规则，不要求使用 LLM 自动规划。

### 4. 依赖关系

Logical Plan 应能够表达：

- Calculator 对输入字段的依赖。
- Calculator 对 Dataset Profile 的依赖。
- 指标对共享中间计算结果的依赖。
- 聚合计算对记录级评测结果的依赖。
- 质量规则判定对 Metric Result 的依赖。

需要支持依赖关系校验和环检测。

不要将完整 Stage DAG 执行逻辑提前塞进 Logical Planner。

### 5. 结果映射

重点解决一次共享计算对应多个独立指标的问题。

例如，一次 LLM 推理得到三个语义指标的原始判断，最终必须分别产生：

- 02080102 Metric Result
- 02080105 Metric Result
- 02080107 Metric Result

每个指标应保留自身的：

- Metric Definition 和 Calculator 版本
- 计算参数
- 得分及状态
- 评测覆盖率
- 证据与异常记录
- 共享计算来源标识

同一次共享推理应有共同的来源标识，便于后续追踪 Token 成本、推理耗时和原始响应。

## 四、实现范围

本阶段优先完成：

1. Logical Plan 的领域模型或 Schema。
2. Logical Planner 服务。
3. Contract 到 Logical Plan 的转换。
4. 输入与 Calculator 依赖校验。
5. 基础依赖关系建模与环检测。
6. 共享计算分组规则。
7. Logical Plan 序列化与查询能力（按现有架构决定是否需要新增 API）。
8. Logical Plan 的单元测试和集成测试。

对于 Shared LLM Calculator：

- 如果项目已经存在相关接口，优先复用并验证规划结果。
- 如果尚未实现，允许增加最小接口契约或测试替身。
- 不要求本阶段完成复杂的 Prompt 编排、真实模型批量推理和 Token 优化。
- 不得使用测试替身冒充真实模型评测结果。

对于 Stage DAG：

- 只需要保证 Logical Plan 可以被后续 Physical Planner 消费。
- 不要求本阶段实现完整的 Stage 调度和恢复机制。

## 五、验收案例

至少覆盖以下场景。

**Case 1：单指标兼容**

现有单指标 Quality Contract 能够生成有效 Logical Plan，不影响现有评测入口。

**Case 2：多规则指标**

一个 Contract 包含多个规则指标，Logical Plan 能正确解析各指标及 Calculator。

**Case 3：共享 LLM 指标**

一个 Contract 包含知识密度、信息缺失和文本连贯性。

当共享条件满足时，生成一个共享语义计算组，并保留三个独立指标的输出绑定。

当模型、参数、Prompt 约束或抽样范围不兼容时，能够正确拆分。

**Case 4：混合评测**

一个 Contract 同时包含规则指标、LLM 指标和专用算法指标，能够生成统一 Logical Plan。

**Case 5：异常处理**

覆盖：

- 指标不存在。
- Calculator 未注册。
- Calculator 仅有占位实现。
- 缺少必要字段。
- 数据模态不匹配。
- 共享分组不兼容。
- 依赖关系存在环。
- 指标不适用。

错误必须有明确的诊断信息。

## 六、工程约束

1. 遵循现有项目的技术栈、目录结构、代码风格和测试规范。
2. 优先增量开发，不做无关重构。
3. 不破坏现有单指标评测及已完成的综合评测 Demo。
4. 不将 Logical Planner 与 Agent Runtime 耦合。
5. 不在 Logical Planner 中执行真实模型推理。
6. 不将物理调度细节放入 Logical Plan。
7. 不为本次 MVP 引入不必要的复杂框架。
8. 如果需要修改数据库结构，遵循现有 Alembic 迁移规范。
9. 对规划结果提供可观测、可调试的结构化输出。
10. 对无法确认的架构决策，优先参考现有项目实现并说明取舍。

## 七、执行方式与交付

请按以下顺序执行：

**第一步：现状审查**

检查代码和文档，列出已有能力、缺失能力、需要复用的模块以及潜在冲突。

**第二步：设计方案**

给出 Logical Plan 数据结构、Planner 处理流程、共享计算分组机制、拟修改文件和测试方案。

将开发方案保存到 `docs/development/` 下，文件名遵循项目现有规范。

**第三步：实施开发**

在现有架构上完成 Logical Plan MVP，不停留在文档规划阶段。

**第四步：测试验收**

执行相关单元测试和集成测试，验证上述 Case 1–5。

**第五步：总结**

输出：

- 实际新增或修改的文件。
- Logical Plan 的结构示例。
- 共享 LLM 指标的规划示例。
- 已通过的测试。
- 当前限制。
- 下一阶段 Physical Plan / Stage DAG 的开发建议。

特别说明：本次开发的核心成果不是生成一份静态 JSON，而是建立一个可复用的 Logical Planner，使平台能够根据不同数据集和质量契约动态生成逻辑评测计划。
