
# Codex 开发任务：Quality Evaluation Agent V1

## 一、项目背景与开发目标

你正在参与一个高质量数据集质量评测平台（Quality Evaluation Platform）的后续开发。

项目已有或正在完成以下核心业务流程：

```text
Dataset Version
    ↓
Dataset Profile
    ↓
Quality Contract
    ↓
Logical Evaluation Plan
    ↓
Physical Evaluation Plan
    ↓
Evaluation Run + Stage Runs
    ↓
Metric Result
    ↓
Rule Evaluation
    ↓
Quality Issue
    ↓
Quality Report
```

系统核心设计原则：

1. Metric Definition：定义评价指标的业务语义、计算要求、适用范围。
2. Calculator Implementation：定义指标的具体计算实现。
3. Quality Contract：引用指标和计算器，配置参数、规则和阈值。
4. Evaluation Plan：将质量契约转换为可执行的逻辑和物理计划。
5. Evaluation Run：执行计算并记录状态、结果、证据和异常。
6. Quality Report：汇总质量分数、质量问题和可追溯证据。

以上是目标架构，不代表所有模块已经完成。必须以仓库实际代码、数据库迁移、API、测试及运行结果确认当前实现状态。

当前开发目标分为三个阶段：

- Phase A：完成最小闭环后的前端体验与视觉优化。
- Phase B：引入 Agent Runtime、Skill Registry 和 Tool Adapter，实现智能评测方案推荐。
- Phase C：借鉴 DeepPrep 的 Plan–Execute–Feedback 机制，实现受约束的执行反馈与方案修正。

本轮不进行 SFT、GRPO 或其他模型训练，也不要求复现 DeepPrep 的完整树搜索算法。

**最终目标：用户选择一个数据集，Agent 自动分析其特征，结合高质量数据集评价指南及已注册指标，推荐适用的评测方案。用户审核后，可将方案转为 Quality Contract 并使用现有平台执行，最终获得质量报告。**

---

## 二、首先审查仓库，不允许直接重构

在编写代码前，完成 Repository Audit。

### 2.1 需要检查的内容

1. 项目目录结构、技术栈、环境配置、启动方式。
2. 后端 FastAPI Router、Service、Repository、ORM、Alembic。
3. 前端 React 页面、组件、路由、API Client、状态管理和 UI 框架。
4. Dataset Version、Dataset Profile、Metric Definition、Calculator、Quality Contract 等领域模型。
5. Logical/Physical Evaluation Plan 的现有实现。
6. Evaluation Run、Stage Run、Metric Result、Quality Issue、Quality Report 的实现。
7. 已有单元测试、集成测试、端到端测试。
8. 现有 AI/LLM、MCP、Agent、Skill 相关代码或依赖。

特别注意：

- 不允许根据文档假定功能已经实现。
- 不允许重新创建已经存在的领域实体。
- 不允许绕过现有 Quality Contract 和 Evaluation Executor。
- 不允许为了接入 Agent 改变现有指标计算结果的语义。
- 不允许随意更换现有前端框架、后端框架或数据库。

### 2.2 审查产出

生成：

`docs/agent-v1/01-repository-audit.md`

包含：

- 已实现能力。
- 部分实现能力。
- 缺失能力。
- 可复用的 API、Service 和数据模型。
- 需要新增的组件。
- 技术风险和兼容性问题。

同时输出一张现有架构与目标架构的差异表。

**先完成审查和方案设计，等待我确认后再开始大规模修改代码。**

---

## 三、Phase A：前端体验与视觉优化

### 3.1 目标

将当前偏工程化的管理界面优化为清晰、专业、适合演示的数据质量评测平台。

不改变已有核心业务流程。

前端必须继续使用项目现有 React 技术栈。

### 3.2 设计要求

采用统一的 Design System：

- 统一字体层级、间距、圆角、边框和阴影。
- 统一表格、表单、按钮、筛选器和状态标签。
- 统一加载、空状态、错误状态和成功反馈。
- 使用专业、克制的企业级 SaaS 视觉风格。
- 优先使用现有 UI 组件库。
- 支持主要桌面分辨率，避免内容溢出。
- 对颜色使用遵循无障碍原则，状态不能只依赖红绿区分。

避免为了视觉效果引入大量无必要的动画或第三方依赖。

### 3.3 重点优化页面

根据仓库实际页面调整，重点包括：

**Dataset 页面**

展示数据集列表、版本、来源、模态、样本规模、字段结构和 Dataset Profile。

**Metric Registry 页面**

按照指南的维度、指标编号、子指标展示 Metric Definition。

支持查看指标描述、计算方法、Calculator 实现、适用条件和版本。

**Quality Contract 页面**

支持选择指标、Calculator、参数、规则与阈值。

显示参数校验错误和不适用原因。

**Evaluation Run 页面**

展示执行进度、阶段状态、计算器执行结果和错误信息。

**Quality Report 页面**

提供：

- 综合质量分数。
- 三大评价维度的汇总。
- 雷达图或其他适合的质量分布图。
- 各指标得分与权重。
- 质量问题列表。
- 指标计算证据和详情。
- 不适用、未执行、执行失败等状态。

必须区分：

- 质量得分低。
- 指标不适用。
- 指标未执行。
- 指标执行失败。
- 证据不足。

不能把未执行指标默认计算为零分。

### 3.4 验收标准

1. 核心页面视觉风格统一。
2. 现有 API 与业务流程不受影响。
3. 所有关键页面具有 Loading、Empty、Error 状态。
4. Quality Report 可以从维度逐级下钻到指标及证据。
5. 前端 Build、Lint、相关测试通过。
6. 提供优化前后页面截图或可复核的视觉检查结果。

---

## 四、Phase B：Agent + Skill + Tool 架构

### 4.1 总体架构

目标结构：

```text
User
  ↓
Quality Evaluation Agent
  │
  ├── Documentation Quality Skill
  │
  ├── Data Quality Skill
  │
  └── Model Application Skill
  │
  ↓
Tool Adapter / Tool Registry
  ↓
Existing Quality Evaluation Platform
  │
  ├── Dataset Service
  ├── Metric Registry
  ├── Calculator Registry
  ├── Quality Contract Service
  ├── Evaluation Planner
  ├── Evaluation Executor
  └── Report Service
```

必须明确以下职责边界。

**Agent**

负责理解用户目标、读取数据集信息、选择 Skill、组织工具调用、推荐方案和解释结果。

**Skill**

负责某一评价维度的专业工作流程、工具使用说明、评价规则约束和决策指导。

**Tool**

负责向 Agent 暴露平台能力，提供稳定的输入输出 Schema。

**Platform**

负责指标定义、计算器管理、契约校验、执行计划、确定性计算、结果持久化和质量判定。

禁止让 Agent 自行生成未经注册的指标定义或擅自修改官方评价标准。

### 4.2 Agent Runtime

首先调研项目现有依赖，判断是否适合使用 LangGraph、PydanticAI、原生 LLM Tool Calling 或其他轻量框架。

不要为了接入 Agent 而引入复杂的多 Agent 框架。

V1 优先实现一个主 Agent，三个可加载的领域 Skill。

Agent Runtime 至少支持：

- LLM Provider 抽象。
- Tool Calling。
- Skill Discovery。
- Skill Loading。
- Conversation State。
- 结构化输出校验。
- 工具调用日志。
- 超时和错误处理。
- 最大调用次数及 Token 预算。
- 人工确认节点。

LLM Provider 的模型、API Key、Base URL 等通过环境变量配置。

不得硬编码密钥。

### 4.3 Skill Registry

设计可扩展的 Skill Registry。

第一版注册三个 Skill：

```text
skills/
  documentation-quality/
    SKILL.md

  data-quality/
    SKILL.md

  model-application/
    SKILL.md
```

实际目录根据仓库结构调整。

每个 Skill 至少包含：

- 名称和描述。
- 适用场景。
- 触发条件。
- 相关指标维度。
- 可用工具。
- 标准工作流程。
- 输入要求。
- 输出 Schema。
- 错误处理规范。
- 证据与可追溯要求。

采用 Progressive Disclosure：

1. Agent 首先读取 Skill 的名称和描述。
2. 根据用户任务加载相关 Skill 的详细说明。
3. Skill 按需调用 Tool。
4. 不要求每次对话都加载全部 Skill 内容。

Skill 不应复制平台中的完整 Metric Definition。

应通过 Metric Registry 动态获取指标定义和版本，避免标准出现多份不一致的副本。

### 4.4 三个 Skill 的职责

**Documentation Quality Skill**

负责说明文档质量评测方案。

例如：

- 0101 基本信息完整性。
- 0102 内容特征完整性。
- 0103 建设过程完整性。

具体指标集合以平台注册的官方指标为准。

支持文档结构识别、评价要求映射、证据定位和规则/LLM 评测方案推荐。

**Data Quality Skill**

负责数据质量指标方案推荐。

支持：

- 读取 Dataset Profile。
- 识别字段类型及模态。
- 查询适用指标。
- 查询 Calculator。
- 比较可用计算实现。
- 推荐参数和执行依赖。
- 识别数据不满足指标前置条件的情况。

重点支持现有重复度 Calculator 的完整案例。

**Model Application Skill**

负责模型应用评价方案。

支持：

- 判断数据集是否适合进行模型应用评价。
- 识别下游任务类型。
- 查询已注册模型评价指标。
- 推荐可用模型、基线与评价协议。
- 识别缺失的标签、训练数据或实验条件。

V1 不要求自动训练模型。

如果缺少模型执行器，必须明确输出 `NOT_IMPLEMENTED` 或对应能力缺失状态，不得模拟模型评测结果。

---

## 五、Tool API 设计

优先复用现有 FastAPI Service。

建议研究以下工具能力：

```python
get_dataset_profile(dataset_version_id)

discover_metrics(dataset_version_id, dimension)

get_metric_definition(metric_code, version)

list_calculators(metric_code)

validate_calculator_applicability(
    dataset_version_id,
    calculator_id
)

create_quality_contract(
    dataset_version_id,
    metric_configs
)

validate_quality_contract(contract_id)

create_evaluation_plan(contract_id)

run_evaluation(plan_id)

get_evaluation_status(run_id)

get_metric_results(run_id)

get_metric_evidence(run_id, metric_code)

get_quality_report(run_id)
```

以上是能力清单，不要求严格采用这些函数名。

请先与现有 API 对照，优先复用。

Tool 的输入输出必须使用明确的 JSON Schema / Pydantic Model。

工具返回应尽量包含：

```json
{
  "status": "SUCCESS",
  "data": {},
  "errors": [],
  "warnings": [],
  "evidence_refs": [],
  "version_info": {}
}
```

这是概念示例，最终 Schema 需要结合现有数据模型设计。

Tool Adapter 应允许未来扩展 MCP Server，但第一版可以直接通过 Python Service Adapter 接入。

不要为了 MCP 再创建一套独立业务后端。

对所有写入、执行类工具设置权限与确认机制。

---

## 六、核心功能：智能评测方案推荐

这是 Agent V1 的核心交付。

### 6.1 用户流程

```text
用户选择 Dataset Version
         ↓
Agent 读取 Dataset Profile
         ↓
Agent 查询 Metric Registry
         ↓
Agent 识别适用评价维度
         ↓
按需加载三个 Skill
         ↓
查询 Calculator 与执行条件
         ↓
生成 Evaluation Proposal
         ↓
平台校验 Proposal
         ↓
前端展示推荐方案
         ↓
用户修改 / 确认
         ↓
转换为 Quality Contract
         ↓
生成 Evaluation Plan
         ↓
使用现有 Executor 执行
         ↓
Quality Report
```

### 6.2 Evaluation Proposal 设计

新增独立的评测方案模型。

建议包含：

```json
{
  "dataset_version_id": "...",
  "proposal_status": "DRAFT",
  "dimensions": [
    {
      "dimension": "data_quality",
      "metrics": [
        {
          "metric_code": "02080103",
          "calculator_id": "...",
          "parameters": {},
          "applicability": "APPLICABLE",
          "reason": "...",
          "required_inputs": [],
          "warnings": []
        }
      ]
    }
  ],
  "missing_requirements": [],
  "execution_estimate": {},
  "standard_version": "...",
  "created_by": "agent"
}
```

以上仅为初步结构，必须结合实际模型进行规范化设计。

方案至少区分：

- 推荐执行。
- 可执行但需要用户确认参数。
- 不适用。
- 缺少输入。
- 平台尚无实现。

必须记录推荐理由、适用性证据及版本信息。

### 6.3 方案推荐约束

Agent 必须遵守：

1. 只能推荐已注册的 Metric Definition。
2. 只能选择已注册且兼容的 Calculator。
3. 必须检查输入字段、模态、参数和依赖。
4. 不能自行修改官方指标权重。
5. 不能把不适用指标当成质量低分。
6. 不允许为缺失数据生成虚假结果。
7. 不允许绕过 Quality Contract 的校验。
8. 对不确定的参数应标记为待确认。
9. 所有推荐必须能够解释依据。

---

## 七、Phase C：借鉴 DeepPrep 的 Agentic Reasoning

参考论文：

DeepPrep: An LLM-Powered Agentic System for Autonomous Data Preparation。

借鉴其：

- Operator Registry。
- Plan–Expand–Execute。
- Execution-grounded Feedback。
- Structured State。
- Non-local Backtracking 的思想。

不直接复制其数据准备任务建模。

### 7.1 第一版采用有界 Plan–Execute–Feedback

```text
PLAN
  ↓
VALIDATE
  ↓
EXECUTE
  ↓
OBSERVE
  ↓
SUCCESS / REPLAN / NEED_USER_INPUT
```

Agent 在生成评测方案后，必须通过平台进行合法性校验。

校验失败时，返回结构化反馈：

- Metric 不存在。
- Calculator 不适用。
- 缺少必要字段。
- 参数不合法。
- 依赖未满足。
- 资源限制。
- 执行异常。

Agent 根据反馈进行有约束的修正。

### 7.2 允许的自动修正

例如：

- 重新选择合法 Calculator。
- 修改非标准强制的可配置参数。
- 补充缺失的执行依赖。
- 更正可由 Schema 证据支持的字段映射。
- 对暂不适用的指标进行标记。

禁止：

- 修改标准指标定义。
- 任意放宽质量阈值。
- 将失败结果改成成功。
- 伪造计算证据。
- 静默切换到不同评价口径。
- 自动覆盖用户已确认的契约。

### 7.3 执行轨迹

为 Agent 记录结构化轨迹：

```text
Agent Session
    ↓
Skill Invocation
    ↓
Tool Call
    ↓
Tool Result
    ↓
Validation Feedback
    ↓
Agent Decision
    ↓
Proposal Revision
```

记录：

- 调用的 Skill。
- Tool 名称及参数。
- 工具返回状态。
- 决策依据。
- 方案版本。
- 修正原因。
- 最终执行结果。

后续可以扩展为 DeepPrep 风格的状态树，但 V1 不要求完整 Tree Search。

必须保留可扩展的状态模型，而不是把全部执行过程仅保存在聊天文本中。

---

## 八、前端 Agent 工作台

在 Phase A 的 Design System 基础上，新增 Agent Evaluation Workspace。

建议采用双栏布局。

左侧：

- Agent 对话。
- 当前数据集上下文。
- 已加载 Skill。
- 工具调用进度。
- Agent 的推荐说明。

右侧：

- Evaluation Proposal。
- 三个评价维度。
- 推荐指标列表。
- Calculator 和参数。
- 推荐原因。
- 适用性与风险提示。
- 用户修改和确认操作。

支持：

1. 选择数据集。
2. 请求推荐评测方案。
3. 查看推荐理由。
4. 修改指标、Calculator 和参数。
5. 校验方案。
6. 确认生成 Quality Contract。
7. 跳转 Evaluation Run。
8. 查看最终 Quality Report。

Agent 工作台必须展示真实 Tool 执行状态。

不得通过前端模拟 Agent 推理或伪造计算进度。

---

## 九、测试与验收

至少覆盖以下测试。

### 9.1 Skill 测试

- Skill 能够正确发现与加载。
- 能根据用户任务选择正确的 Skill。
- 未加载的 Skill 不会被错误使用。
- Skill 能访问其声明的 Tool。

### 9.2 方案推荐测试

- 正确推荐适用指标。
- 正确选择合法 Calculator。
- 缺失字段时返回不适用或待补充状态。
- 不存在的 Metric Code 被拒绝。
- 不合法的 Calculator 参数被拒绝。
- 推荐结果能通过平台校验。

### 9.3 Agent 反馈测试

- 工具失败后能识别错误类型。
- 可修复错误能触发有界重试。
- 不可修复错误能停止并请求人工处理。
- 不允许静默改变质量标准。
- 所有修正有审计记录。

### 9.4 端到端测试

使用仓库现有的真实或测试数据集。

至少完成：

```text
Dataset Version
    ↓
Agent 推荐重复度指标
    ↓
选择已注册 Calculator
    ↓
生成 Evaluation Proposal
    ↓
用户确认
    ↓
Quality Contract
    ↓
Evaluation Plan
    ↓
Evaluation Run
    ↓
Metric Result
    ↓
Quality Report
```

要求：

- 结果与手动执行相同契约的计算结果一致。
- 证据可追溯。
- 不重复实现 Calculator。
- 不破坏现有手动评测流程。

---

## 十、开发交付物

在 `docs/agent-v1/` 下生成：

```text
01-repository-audit.md
02-architecture-design.md
03-frontend-design.md
04-skill-tool-contract.md
05-evaluation-proposal-schema.md
06-implementation-plan.md
07-test-plan.md
```

开发过程必须分阶段执行。

每个阶段提供：

1. 本阶段目标。
2. 修改的文件。
3. 新增的 API 和数据模型。
4. 关键技术决策。
5. 测试命令及实际结果。
6. 已知问题。
7. 下一阶段计划。

不要一次性实现所有模块。

## 十一、当前立即执行的任务

**本次只完成 Repository Audit + Architecture Design + Implementation Plan。**

具体要求：

1. 阅读项目代码与相关文档。
2. 确认当前最小闭环的完成程度。
3. 评估前端视觉和交互问题。
4. 检查是否已有 Agent、Skill、Tool 基础设施。
5. 对比 LangGraph、PydanticAI、原生 Tool Calling 的适配性。
6. 设计 Agent Runtime、Skill Registry、Tool Adapter、Evaluation Proposal 的模块边界。
7. 明确哪些功能需要新建，哪些必须复用。
8. 给出按依赖顺序排列的开发任务和验收标准。
9. 标明数据库迁移和兼容性风险。
10. 输出上述设计文档。

**完成后停止，等待我审查方案。不要立即安装大量依赖、重构目录或开始大规模编码。**
# 用一个真实指标演示端到端调用
以 02080103 内容重复度 为例。

假设用户提出：
评测这个 JSONL 数据集的内容重复情况，给出重复度分数，并解释哪些数据重复。

系统可以这样工作：

1. Agent 识别需求：确定使用 Data Quality Skill，识别指标编号 02080103。
2. Skill 查询 Metric Registry：获取指标定义、适用数据类型、合法计算器及参数要求。
3. Skill 查询 Dataset Profile：确认文本字段、记录数量、缺失情况以及数据格式。
4. 生成或引用 Quality Contract：确定是否使用精确匹配、规范化匹配、MinHash 或语义相似度；确认阈值及判定规则。
5. 平台生成并执行 Evaluation Plan：执行相关 Calculator，记录 Stage Run、耗时、错误与输出。
6. 读取 Metric Result 和 Evidence：返回重复记录数、重复组、受影响比例、计算覆盖范围及重复样本证据。
7. Agent 解释结果：根据实际 Metric Result 生成质量分析，不重新计算分数，也不自行改变判定标准。
