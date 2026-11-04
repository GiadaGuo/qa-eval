
# Phase 1 开发任务：Evaluation Proposal 可编辑配置与评测提交闭环

## 一、任务背景

当前项目为 `qa-eval` 数据集质量评测平台，已实现：

- Dataset Version / Dataset Profile
- Metric Definition / Calculator Registry
- Quality Contract
- Logical Evaluation Plan
- Physical Evaluation Plan
- Evaluation Run / StageRun
- Metric Result / Rule Evaluation / Quality Report
- Agent Evaluation Proposal
- Shared LLM MVP 基础能力与三指标共享推理闭环

当前 Agent 能够针对数据集生成 Evaluation Proposal，推荐指标及其对应 Calculator，但部分指标处于 `NEEDS_INPUT` 状态。

主要问题是：

**Evaluation Proposal 目前缺少完整的用户配置能力。**

用户无法在 Proposal 中补充或修改 Calculator Parameters、字段绑定、规则判定策略和阈值，因此即使 Calculator 已经实现，也无法完成从推荐方案到 Quality Contract 再到 Evaluation Run 的完整闭环。

本次开发只解决这一问题。

## 二、最终目标

实现：

```text
Agent 推荐 Evaluation Proposal
            ↓
展示推荐指标与 Calculator
            ↓
识别缺失参数 / NEEDS_INPUT
            ↓
用户展开指标配置
            ↓
填写或修改参数、字段绑定、规则策略
            ↓
前后端校验
            ↓
Proposal 状态更新为 READY
            ↓
用户确认 Evaluation Proposal
            ↓
生成 Quality Contract
            ↓
提交 Evaluation Run
            ↓
展示评测状态与结果
```

注意：

- `NEEDS_INPUT` 是需要用户补充配置的状态，不是 Calculator 不可用。
- 不得通过写死默认参数、跳过校验或删除必填字段来强行变成 READY。
- 不得为了演示而伪造规则、阈值、评分或 Evaluation Run 结果。
- 用户可以保留部分指标不参与本次评测，但不能将不完整指标伪装成已准备就绪。

## 三、先审查现有实现

请先检查项目中与以下能力有关的实际代码：

1. Evaluation Proposal 的 Schema、持久化模型及 API。
2. Agent 推荐指标和 Calculator 的生成逻辑。
3. `NEEDS_INPUT` 的产生条件及状态更新机制。
4. Calculator Descriptor 的 Parameters Schema。
5. Metric Definition 的指标方向、结果类型和计算要求。
6. Dataset Profile、Field Mapping、Content Role。
7. Quality Contract 的创建、版本化、确认及校验逻辑。
8. Rule Definition / Rule Evaluation / Threshold 配置。
9. Logical Planner、Physical Planner 与 Evaluation Run 提交接口。
10. React 前端 Proposal 组件及 Agent 对话状态管理。
11. 现有测试和数据库迁移。

重点回答：

- `NEEDS_INPUT` 究竟缺少什么？
- 缺失项来自 Calculator、Contract、Rule，还是 Dataset Binding？
- 当前 Proposal 是否已经保存配置草稿？
- 现有后端是否支持更新 Proposal？
- 前端是否只是缺少表单？
- 目前是否存在 Proposal → Contract 的参数丢失问题？

**必须以现有项目实际实现为准，不要重新创建一套平行的 Proposal / Contract 系统。**

完成审查后给出简短实施计划，然后直接开发，不需要等待我确认。

## 四、建立统一的 Proposal 配置模型

每个推荐指标至少需要区分以下信息：

### 4.1 Calculator Parameters

决定指标如何计算，例如：

- KenLM 模型路径或模型引用
- LLM Provider / Model 配置
- 分词策略
- 文本长度参数
- 规则检测开关
- Calculator 特有的参数

参数定义必须来自 Calculator Descriptor 或现有参数 Schema。

不要在前端硬编码每个 Calculator 的完整参数结构。

### 4.2 Dataset Binding

决定 Calculator 使用哪些输入字段，例如：

- `plain_text_content` → `body`
- `title` → `title`
- `summary` → `summary`

字段选项应来自当前 Dataset Version 的 Schema / Field Mapping。

必须校验字段是否存在、类型是否兼容，以及是否满足 Calculator 的 content role 要求。

### 4.3 Rule Configuration

决定计算结果如何进行质量判定，例如：

- 阈值比较
- 合格范围
- 是否仅观察
- 规则启用状态
- 判定方向
- 适用范围

Calculator Parameters 和 Rule Configuration 必须分离。

不能把“怎么计算”和“什么结果算合格”混为一谈。

### 4.4 Applicability / Coverage

用于说明：

- 指标是否适用当前数据集。
- 必需输入是否完整。
- Calculator 能否执行。
- 计算是否只能覆盖部分指标定义。
- 是否允许正式评分。

不要用一个 READY 状态掩盖上述不同含义。

如果现有 Schema 已支持这些概念，应复用；否则进行最小向后兼容扩展。

## 五、NEEDS_INPUT 状态与校验机制

请将 `NEEDS_INPUT` 从单纯的阻塞标签改为可操作状态。

每个 NEEDS_INPUT 指标应能够展示：

- 缺失配置项。
- 缺失原因。
- 对应配置入口。
- 参数要求或取值范围。
- 当前填写值。
- 校验错误。

建议采用以下状态语义，具体命名以现有系统为准：

- `READY`：所有必需配置满足要求，可以生成有效 Contract。
- `NEEDS_INPUT`：缺少用户可补充的配置。
- `NOT_APPLICABLE`：指标不适用当前数据集。
- `UNAVAILABLE`：Calculator 或运行依赖不可用。
- `INVALID`：配置存在错误或不符合 Schema。

不要将 Calculator 不存在、运行依赖缺失和用户未填写阈值都归为 NEEDS_INPUT。

### 5.1 校验时机

至少支持：

1. 表单编辑时进行基础校验。
2. 保存 Proposal 时进行后端 Schema 校验。
3. 确认 Proposal 时进行完整配置校验。
4. 生成 Quality Contract 时进行最终校验。

前端校验只负责用户体验，后端必须是最终校验权威。

### 5.2 状态更新

用户填写配置后：

- 保存到当前 Proposal / Session 的草稿。
- 重新执行准备状态校验。
- 配置完整时由 NEEDS_INPUT 变为 READY。
- 配置无效时展示具体错误。
- 页面刷新后仍保留已保存配置。
- 切换 Agent Session 后不会混入其他会话的配置。

不允许仅在 React 本地将状态改成 READY，而后端实际 Contract 仍缺少参数。

## 六、前端：Evaluation Proposal 可编辑配置界面

保持项目现有森系视觉风格，优先复用现有 React 组件和 UI 库。

不要重做 Agent 对话页面。

### 6.1 指标卡片

每个推荐指标展示：

- 指标编号及名称。
- 推荐 Calculator。
- Calculator 执行模式（DIRECT / SHARED_INFERENCE 等）。
- 推荐原因。
- 配置完整性状态。
- 需要补充的字段。
- 展开配置入口。

### 6.2 配置面板

用户点击「配置」后，可编辑：

**A. 计算参数**

根据 Calculator Parameters Schema 动态生成控件：

- 数字：数字输入。
- 布尔：开关。
- 枚举：下拉选择。
- 字符串：文本输入。
- 必填字段：明确标识。
- 可选字段：允许使用已有默认值。

对于复杂对象参数，优先复用项目现有编辑能力，不要为了 MVP 开发一套大型 JSON Schema Form 框架。

**B. 字段绑定**

根据当前 Dataset Version 的实际字段提供选择。

显示 content role 与真实字段名的映射。

不允许选择不存在的字段。

**C. 规则策略**

至少支持现有规则引擎已经实现的策略，例如：

- 阈值比较。
- 区间判定（如果后端支持）。
- 仅观察，不进行合格判定。

具体可选策略必须来自后端实际支持的规则类型，不得只在前端增加选项却无法执行。

对于需要数值阈值的规则，用户应能填写阈值。

对于只输出原始统计量的指标，不得自动套用 0～1 评分阈值。

### 6.3 交互要求

- 用户可以修改 Agent 推荐的默认配置。
- 可以保存草稿。
- 可以恢复已保存配置。
- 可以查看配置错误。
- 可以选择不纳入本次评测的指标。
- 可以确认所有 READY 的已选指标。
- 不允许提交仍存在必填配置缺失的已选指标。
- 不能因为一个未选中的指标 NEEDS_INPUT，就阻止其他已配置指标评测。

界面应清楚区分：

**计算准备就绪** 和 **已配置质量判定规则**。

如果现有 Contract 支持仅计算、不进行阈值判定，应允许用户选择「仅观察」模式，不应强制所有指标都填写阈值。

## 七、重点适配已实现的 Calculator

本次重点验证以下指标：

| Metric ID | 要求                                              |
| --------- | ------------------------------------------------- |
| 0201      | 正确展示并编辑现有计算参数和规则配置              |
| 02080101  | 支持 KenLM 运行参数；不擅自生成 PPL → 质量分映射 |
| 02080102  | 支持 Shared LLM Rubric 相关参数及规则配置         |
| 02080104  | 支持现有文本完整程度 Calculator 的参数与规则      |
| 02080105  | 支持 Shared LLM 参数及规则配置                    |
| 02080106  | 支持显式规则检测参数；保留部分覆盖语义            |
| 02080107  | 支持 Shared LLM 参数及规则配置                    |

同时检查现有 `02080103` 重复程度 Calculator 的兼容性，不得破坏其现有评测能力。

请根据实际 Calculator Descriptor 确定每个指标需要的配置，不要假定所有指标都有相同参数或相同分数范围。

特别注意：

**02080101：** PPL 是原始困惑度统计，不得默认使用 0～1 的评分阈值。

**02080106：** 如果当前只覆盖显式干扰规则，应保留 PARTIAL / coverage 信息，不得将其自动视为完整文本纯净程度。

**Shared LLM：** Proposal 配置不得破坏已有三个语义指标共享推理的兼容性分组。

## 八、Proposal → Quality Contract → Evaluation Run

这是本次最重要的端到端验收。

### 8.1 Contract 生成

用户确认 Proposal 后，必须正确保留：

- Metric ID / Version
- Calculator ID / Version
- Calculator Parameters
- Dataset Binding
- Rule Configuration
- Applicability / Coverage 相关约束
- 用户启用或排除的指标

Contract 必须通过现有 Schema 和 Planner 校验。

不要只把指标 ID 传给后端，然后重新使用默认参数生成 Contract。

### 8.2 Evaluation Run

用户确认后，沿用现有执行链路：

Quality Contract
→ Logical Evaluation Plan
→ Physical Evaluation Plan
→ Evaluation Run
→ StageRuns
→ Metric Results
→ Rule Evaluation
→ Report

不要创建新的执行引擎。

对于 DIRECT 和 Shared LLM 指标，应继续使用现有混合 StageRun 能力。

### 8.3 防止重复提交

- 保存配置不触发评测。
- 确认 Contract 不应隐式重复提交 Run。
- 用户明确提交评测后才创建 Evaluation Run。
- 处理按钮重复点击、请求重试和页面刷新。
- 不得因切换 Agent Session 重复提交评测。

如现有流程采用一次确认后自动提交评测，应保持既有业务语义，但必须保证单次确认不会意外创建多个 Run。

## 九、Agent Session 隔离

项目已支持或正在使用同一 Dataset Version 下的多个 Agent Session。

本次必须保证：

- Proposal 配置绑定当前 Session。
- Session A 的草稿不会出现在 Session B。
- Session A 的 Contract 不会被 Session B 修改。
- Session B 可以对同一 Dataset Version 重新生成不同 Proposal。
- 页面刷新后能够恢复当前 Session 的配置。
- 切换会话时不会被旧请求覆盖状态。

如果 Session 管理已实现，直接复用，不要重新开发多会话系统。

## 十、测试要求

请补充或完善：

### 单元测试

- Calculator Parameters Schema 驱动配置校验。
- 必填参数缺失。
- 参数类型错误。
- 字段绑定不存在。
- 字段类型不兼容。
- Rule Configuration 合法与非法。
- NEEDS_INPUT → READY。
- READY → INVALID / NEEDS_INPUT。
- 未选中指标不阻塞提交。
- PARTIAL 与仅观察策略的处理。

### 集成测试

- Proposal 草稿保存和恢复。
- Proposal 确认。
- Proposal → Quality Contract 参数完整传递。
- Quality Contract → Logical Plan 校验。
- DIRECT + Shared LLM 混合执行。
- Metric Result → Rule Evaluation。
- Agent Session 间配置隔离。

### 前端测试

- 配置面板正常打开。
- 动态表单正确展示。
- 参数修改和保存。
- 校验错误展示。
- 状态更新。
- 已选指标提交校验。
- 页面刷新恢复。
- 会话切换隔离。

### 端到端测试

使用现有 `jhsjk_article` 数据集：

1. Agent 生成 Evaluation Proposal。
2. 选择多个已实现指标。
3. 至少包含一个 DIRECT 和一个 Shared LLM 指标。
4. 对 NEEDS_INPUT 指标填写必要配置。
5. 设置或选择合法规则策略。
6. 保存并确认 Proposal。
7. 生成 Quality Contract。
8. 提交 Evaluation Run。
9. 验证 Metric Result 和 Rule Evaluation。
10. 验证报告中的分数、状态和覆盖范围符合指标定义。

若真实 MySQL、KenLM 模型或 LLM API 不可用，请使用 Mock 完成可离线测试，并明确区分真实验证与 Mock 验证。

不得将 Mock 结果冒充真实评测结果。

## 十一、开发边界

本次不开发：

- 新的 Calculator。
- 0202、0203、0205、0206、0207、0208。
- Shared LLM Operator 新功能。
- 新的 Agent 推荐算法。
- 新的综合评分模型。
- 新的质量报告可视化。
- 新的多会话管理系统。
- 对现有计算指标公式的修改。

允许为完成 Proposal 配置闭环，对相关 Schema、API、前端和 Contract 生成逻辑做必要的最小兼容修改。

当前已不存在并行 Agent 开发冲突，不需要沿用之前针对其他 Agent 的文件所有权限制，但仍需保护现有未提交修改，不使用破坏性 Git 命令。

## 十二、交付要求

请直接完成代码开发，不要只生成设计文档。

完成后汇报：

1. 当前 NEEDS_INPUT 的根因分类。
2. 修改文件清单。
3. 新增或修改的 API / Schema。
4. Proposal 配置面板的实际交互。
5. 七个重点指标的配置支持情况。
6. Proposal → Contract → Run 的实际验证结果。
7. 单元、集成、前端测试结果。
8. 真实数据和真实 Provider 的验证情况。
9. 仍存在的阻塞项及原因。
10. 对 Phase 2 补齐六个指标 Calculator 的接口建议。

**最终验收标准：用户在 Evaluation Proposal 中能够手动配置已推荐 Calculator 的必要参数、字段绑定和规则策略，使可配置的 NEEDS_INPUT 指标变为 READY，并成功生成 Quality Contract、提交 Evaluation Run，得到可追溯的评测结果。**

完成 Phase 1 后停止，不要自动进入 Phase 2。
