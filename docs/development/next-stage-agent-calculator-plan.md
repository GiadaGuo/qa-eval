# qa-eval 下一阶段开发计划：Calculator 平台化、Plan 与 Agent 推荐

- 状态：实施中（闭环 0、1、1.5、2A 已完成）
- 制定日期：2026-10-28
- 前置基线：MVP 后端主链路已基本贯通
- 推荐路线：最小 Proposal + Registry/Calculator Protocol + Plan 接口纵向切片
- 实施方式：按闭环逐个开发、逐个验收，不并行铺开远期能力

## 1. 文档定位

本文承接 `docs/development/mvp-development-plan.md`，规划 MVP 之后的下一阶段开发。目标不是重建现有 Registry、Contract、Run 或 Report，而是在现有真实执行链路上补齐可扩展 Calculator、结构化评测方案和 Agent 推荐能力。

相关设计约束：

- `docs/adr/0001-skill-metric-ownership-and-shared-evaluation-layers.md`：Skill 与平台共享层的职责边界。
- `docs/architecture/canonical-data-flow-models-v1.md`：canonical 数据流模型与不变量。
- `plan/Idea-Pool/agent开发/agent设计.md`：Agent V1 的产品与研究设想。
- `docs/development/mvp-development-plan.md`：已有 MVP 范围和验收路径。

本文以当前源码为准。旧进度文档中与当前实现冲突的描述不作为开发事实。

### 1.1 所有闭环的统一完成门禁

从闭环 1 之后，每个闭环只有同时满足自动化质量门和真实运行冒烟，才可标记为“已完成”。仅有 unit/integration pytest、lint 或 build 通过，不足以完成闭环。

自动化质量门至少包括：

- 完整 pytest 通过，或仅保留有明确原因的环境跳过。
- 后端 Ruff/lint 通过。
- 前端 lint 和 production build 通过。
- Pydantic contract 与 checked-in JSON Schema 无漂移。

真实运行冒烟必须使用当前开发环境的真实 PostgreSQL 和 ArtifactStore，执行：

```text
确认 PostgreSQL migration 位于单一 head
  -> 读取当前数据库 Registry
  -> 显式执行或确认本版本 Registry Sync
  -> Registry readiness 通过
  -> 使用已发布的真实/固定 E2E Dataset Version
  -> 发布一个新的 Quality Contract Version
  -> 创建一个新的 Evaluation Run
  -> 等待 StageRun 结束
  -> 校验 MetricResult 与 Evidence
  -> 校验 Rule Evaluation / Issue / IssueIndex
  -> 成功生成并读取 Quality Report
```

约束：

- 冒烟必须使用数据库中“当前 Registry”，不能只验证 source-controlled JSON 或测试内临时 seed。
- 每次必须新建 Contract Version 和 Evaluation Run，不得用历史成功 Run 代替。
- Calculator ERROR、StageRun FAILED、Report 缺失或 Registry readiness 失败时，闭环不得标记完成。
- 若预期规则结果为 FAIL，可视为业务判定成功；但 Evaluation Run 必须完成执行，Report 必须可生成，不能以执行 ERROR 代替质量 FAIL。
- 验收记录必须保存数据库名/环境、Registry fingerprint 或关键 descriptor digest、dataset_version_id、contract_version_id、run_id、StageRun 状态、Report 状态及执行时间；不得记录凭据或敏感原始内容。
- 未经用户授权，不连接生产数据库；默认使用本地开发 PostgreSQL。

## 2. 下一阶段目标

最终目标是形成以下真实链路：

```text
用户选择 Dataset Version
  -> 平台读取 Manifest / Profile
  -> 查询 Metric / Calculator / Binding
  -> 判断适用性和运行可用性
  -> 规则 Baseline 或 Agent 生成 Evaluation Proposal
  -> 平台校验 Proposal
  -> 用户确认并发布 Quality Contract
  -> 编译 Logical Plan / Physical Plan
  -> Evaluation Run 调用已注册 Calculator
  -> Metric Result / Evidence / Rule / Issue / Report
  -> Agent 基于持久化事实解释结果
```

下一阶段必须实现：

1. 新增 Calculator 时不再修改 EvaluationRun 执行器。
2. Agent 只能推荐 Registry 中真实存在且适用的指标与 Calculator。
3. Agent 输出必须先形成结构化 Proposal，再经过平台校验和用户确认。
4. 多指标 Contract 可以编译为可追溯的执行计划。
5. Skill 保存领域流程、推荐策略和工具说明，不复制平台标准定义或正式算法。
6. 所有正式结果继续复用现有 Contract、Run、Result、Rule、Issue 和 Report 链路。

下一阶段暂不实现：

- SFT、GRPO 或完整 DeepPrep 复现。
- 多 Agent 协作系统。
- 通用分布式 DAG 调度器。
- Spark、Ray、Kubernetes 或 Celery 集群。
- Agent 自动发布 Contract、自动启动 Run 或执行高风险修复。
- 一次性实现全部指南指标。

## 3. 当前代码基线

### 3.1 可直接复用

- PostgreSQL Metric Definition、Calculator Implementation、Binding 三类 Registry 表。
- source-controlled Registry JSON 和幂等 seed。
- Registry 只读 API 与指标管理页面。
- Dataset Version、Manifest、Profile 和 ArtifactStore。
- Quality Contract Version 及发布校验。
- Physical Plan、Evaluation Run、Stage Run 和单进程后台执行器。
- Metric Result、Evidence、Rule Evaluation、Quality Issue、IssueIndex 和 Quality Report。
- `02080103` 的真实 Calculator、回归数据和执行证据。
- Data Quality Skill 与 Documentation Quality Skill 的领域资料和局部验证脚本。

### 3.2 必须补齐的缺口

| 主题 | 当前状态 | 下一阶段要求 |
| --- | --- | --- |
| Calculator 调用 | 执行器硬编码导入 `02080103` | 按 Registry entrypoint 动态加载受信任实现 |
| Calculator 所有权 | 平台包装器运行时加载 `.agents/skills` 脚本 | 正式算法移入平台包，Skill 调用平台实现 |
| 参数校验 | 只覆盖部分 JSON Schema 规则 | 统一 JSON Schema 校验和语义约束 |
| 适用性 | 只检查模态和 content role | 统一 Applicability Validator |
| 多指标 Contract | canonical model 支持，发布接口只创建一个 item | 支持一次发布多个已校验 item |
| Logical Plan | 尚未实现 | 增加最小依赖模型和不可变计划 |
| Physical Plan | 顺序 Stage 配置快照 | 增加依赖、输入输出绑定和执行策略 |
| Evaluation Proposal | 尚未实现 | 增加结构化候选方案和校验结果 |
| Agent/LLM | 尚无 Runtime 和 Provider | 先规则 Baseline，后原生 Tool Calling |
| Skill Runtime | 当前主要面向开发工作流 | 增加受控元数据、推荐规则和工具权限 |

### 3.3 开发前质量门槛

截至 2026-10-28 的最新本地验证：

- 前端 `build` 通过。
- 前端 `lint` 通过。
- unit + contract：133 passed、2 failed。
- 完整 pytest：164 passed、2 failed、6 skipped、17 errors。
- 2 个失败来自 `QualityReportV1` 与已提交 JSON Schema 漂移。
- 17 个 error 来自当前环境无法连接 PostgreSQL 测试库。

因此第一个闭环必须先恢复 canonical schema 和真实数据库测试基线。

## 4. 总体架构边界

### 4.1 Metric Definition

描述“评测什么”，包括标准语义、编号、版本、measurement、适用范围、方向和限制。Metric Definition 不包含 Calculator 参数、Contract 阈值或 Agent 推荐理由。

### 4.2 Calculator Implementation

描述“如何计算”，包括输入、输出、参数、代码版本、运行依赖、成本、证据能力和语义覆盖。Calculator 只生成客观 Metric Result 和 Evidence，不执行质量判定。

### 4.3 Quality Contract

记录用户确认后的正式评测意图，包括 Dataset Version、Metric、measurement、Calculator 约束、参数、阈值、严重度和缺失策略。

### 4.4 Skill

保存所属维度的评测流程、适用性知识、推荐策略、工具使用说明和示例。Skill 不复制完整 Metric Definition，不持有正式 Calculator 算法，不发布 Contract，不伪造 Run 或 Report。

### 4.5 Agent

负责理解用户目标、选择 Skill、调用平台查询与校验工具、生成 Proposal 和解释结果。Agent 不直接访问 ORM、数据库或 ArtifactStore，不创建未注册指标，不绕过用户确认。

## 5. 分阶段闭环

## 闭环 0：恢复可信开发基线

状态：已完成（2026-10-28）。验收命令、固定 fixture、完整测试结果和旧进度差异见 `docs/progress/next-stage-loop-0-trusted-baseline.md`。

### 目标

在扩展协议和执行器前，确保当前 MVP 的 canonical contract、数据库迁移和浏览器主链路可信。

### 范围

- 修复 `QualityReportV1` 与 JSON Schema 漂移。
- 在真实 `qa_eval_test` PostgreSQL 上执行 Alembic migration 和完整 pytest。
- 固化一份小型 E2E 数据，包含正常记录、空文本、exact duplicate 和 near-duplicate candidate。
- 重新验收上传、发布、Contract、Run、Report 主链路。
- 记录当前有效实现与旧进度文档之间的差异。

### 不包含

- 新指标。
- Agent 或 Proposal。
- Calculator Protocol 重构。

### 验收路径

```text
生成/校验 JSON Schema
  -> pytest 全部通过或仅保留明确环境跳过
  -> Alembic 单一 head
  -> 浏览器上传 E2E fixture
  -> 发布 Dataset Version
  -> 运行 02080103
  -> 查看 Report 和 Issue
```

### 完成定义

- Pydantic contract 与 checked-in JSON Schema 无漂移。
- PostgreSQL 集成测试可重复运行。
- 前端 build、lint 通过。
- E2E 验收结果有书面记录。

---

## 闭环 1：Calculator 真正插件化

状态：已完成（2026-10-28）。Protocol、Loader、02080103 迁移、显式 Registry 同步及真实运行证据见 `docs/progress/next-stage-loop-1-calculator-plugins.md` 和 `docs/progress/next-stage-loop-1.5-representative-metrics.md`。

### 目标

让 Registry 中的 Calculator 从“可展示元数据”变为“可由执行器通用调用的实现”。

### 范围

1. 定义最小 Calculator Protocol：
   - `validate(context, parameters)`。
   - `execute(context, parameters)`。
   - 统一返回 observations、population、semantic coverage、evidence 和 diagnostics。
2. 实现受信任 Calculator Loader：
   - 解析 Registry entrypoint。
   - 校验实现符合 Protocol。
   - 校验 key/version/code digest。
   - 拒绝未注册或不受信任的 entrypoint。
3. 将 `02080103` 正式算法收敛到 `src/qa_eval/calculators/`。
4. 反转 Skill 依赖：Skill 脚本调用平台 Calculator，平台运行不再依赖 `.agents/skills`。
5. EvaluationRun 执行器删除 `02080103` 专用硬编码调用。
6. 使用标准 JSON Schema validator 校验参数，并保留 Calculator 语义校验钩子。
7. 将 Registry seed 从 GET 查询的隐式写入迁移为显式同步入口。

### Registry 增量字段

优先扩展现有 Calculator Descriptor，不创建第二套注册中心：

- runtime kind 和运行依赖。
- input artifact requirements。
- resource/cost profile。
- timeout 和确定性策略。
- applicability constraints。
- semantic coverage。
- availability status。
- sensitive-content access policy。

### 验收路径

```text
注册测试 Calculator
  -> Registry 同步
  -> 发布引用该 Calculator 的 Contract
  -> 编译 Physical Plan
  -> Executor 通过 entrypoint 加载
  -> 生成 MetricResult 和 Evidence
  -> 无需修改 EvaluationRunService
```

### 完成定义

- 新增 Calculator 不修改执行器源码。
- `02080103` 数值和证据回归不变。
- 错误 entrypoint、digest、参数和依赖均产生结构化错误。
- Skill 与平台不存在两份需要同步维护的正式算法。

---

## 闭环 1.5：最小代表性指标切片

状态：已完成（2026-10-28）。实现、自动化门禁与真实 PostgreSQL/ArtifactStore 冒烟证据见 `docs/progress/next-stage-loop-1.5-representative-metrics.md`。

### 定位

在进入 Applicability、Proposal 与多指标 Contract 之前，补充足以验证“多 Metric”和“同一 Metric 多 Calculator”的最小真实 Registry 样本。本闭环不追求指标数量，不提前实现闭环 2 的 Proposal、Applicability Service 或多指标 Contract。

前置条件：闭环 1 必须先通过统一完成门禁中的真实 PostgreSQL 冒烟。

### 目标

1. 新增一个正式、确定性、低成本的 Metric/Calculator：`0204` 结构完整性。
2. 为现有 `02080103` 新增一个 exact-only Calculator。
3. 形成以下真实 Registry 形态：
   - 至少两个可执行 Metric。
   - `02080103` 至少两个可选择 Calculator。
   - Calculator 在成本、参数和 semantic coverage 上存在可验证差异。

### 范围

#### 1. `0204` 结构完整性

Metric Definition 保留指南语义：数据填充完整，不包含缺失值，或缺失值处于用户认可的合理范围。

Calculator 只计算客观事实，不内置“合理范围”或质量结论：

- 输入为 canonical `SampleRecord 2.0` 和显式选择的 content role/metadata field。
- 区分字段不存在、NULL、空字符串和有效值，不无依据地合并缺失类型。
- 至少输出完整记录数、缺失记录数、可评测记录总数和完整记录比例。
- 使用 Contract rule/threshold 表达允许的缺失范围。
- Evidence 仅保留必要的 sample ID、缺失类别和字段/role，不复制敏感原始文本。
- 缺少用户必须指定的字段或 role 时返回结构化校验错误或缺失输入，不伪装成质量 FAIL。

#### 2. `02080103` exact-only Calculator

- 只覆盖 normalized exact duplicate，不覆盖字符/词 n-gram、MinHash candidate、段落重复或语义重复。
- 复用平台内正式 normalization/exact duplicate 实现，不复制另一份需要同步维护的算法。
- 使用独立 Calculator key、version、entrypoint、code digest 和 Descriptor。
- 与完整 `text_duplicate_degree` Calculator 同时绑定 `02080103@1.0.0`。
- Descriptor 明确较低 cost 和较窄 semantic coverage，便于闭环 2 验证候选排序与用户选择。
- 两个 Calculator 的结果不得因为 Metric key 相同而混合；Plan、StageRun 和 MetricResult 必须能追溯实际 Calculator。

#### 3. 不适用候选测试数据

为后续闭环 2 准备测试专用的图像 Metric/Calculator Registry fixture，用于验证文本数据集的 `NOT_APPLICABLE`。该 fixture 不进入正式 Registry，不伪装成已实现图像 Calculator。

### 不包含

- 批量实现文本指标清单。
- PPL、Embedding、分类器或 LLM-assisted Calculator。
- `02080104`–`02080107` 的代理指标实现。
- Proposal 持久化、Applicability Service、多指标 Contract 或 Logical Plan。
- 图像 Calculator 的正式实现与发布。

### 验收路径

```text
完成闭环 1 真实环境冒烟
  -> 注册并同步 0204 Metric/Calculator/Binding
  -> 注册并同步 02080103 exact-only Calculator/Binding
  -> Registry readiness 通过
  -> 在真实 PostgreSQL 上为 0204 发布新 Contract 并新建 Run
  -> 生成 0204 MetricResult / Evidence / Rule / Issue / Report
  -> 为 02080103 exact-only 发布新 Contract 并新建 Run
  -> 校验 exact 数值与完整版 Calculator 的 exact observation 回归一致
  -> 生成并读取最终 Report
```

### 完成定义

- `0204` 与 `02080103` 均存在可执行的正式 Metric/Calculator/Binding。
- `02080103` 的 exact-only 与完整版 Calculator 可被 Registry 明确区分和独立选择。
- `0204` 不把 NULL、空字符串、字段不存在和执行错误混成同一种质量结论。
- exact-only Calculator 的 exact observation 与现有 Calculator 回归一致，且不声称覆盖未计算的语义。
- 测试用图像候选不会进入正式 Registry。
- pytest、lint、build、schema drift 全部通过。
- 按统一完成门禁，两个新增 Calculator 均完成“真实 PostgreSQL + 当前 Registry + 新建 Evaluation Run + Report”冒烟，并记录 Contract/Run/Report 证据。

---

## 闭环 2：Applicability、Proposal 与多指标 Contract

状态：2A 已完成（2026-10-28），2B 待实施。2A 已交付确定性 Applicability Validator、最小 `EvaluationProposalV1`、Proposal Validation Service、多指标 Contract 原子发布和单指标兼容；验收记录见 `docs/progress/next-stage-loop-2a-applicability-proposal-contract.md`。规则推荐 Baseline、Proposal revision 持久化与完整人工工作台归入 2B。

### 目标

在接入 LLM 前建立确定性的合法候选集和正式 Proposal 边界。

### 范围

1. 实现 Applicability Validator，输入：
   - Dataset Manifest。
   - Dataset Profile。
   - Metric Definition。
   - Calculator Descriptor。
   - Binding。
2. 输出统一状态：
   - `APPLICABLE`。
   - `NOT_APPLICABLE`。
   - `NEEDS_INPUT`。
   - `UNAVAILABLE`。
3. 定义 `EvaluationProposalV1`，至少包含：
   - Dataset Version 与 Profile fingerprint。
   - 推荐 Metric/measurement。
   - 候选和选中的 Calculator。
   - 参数、规则和阈值建议。
   - 适用性结果、理由、限制和缺失条件。
   - Proposal revision、创建者和校验结果。
4. 实现 Proposal Validation Service。
5. 实现无需 LLM 的规则推荐 Baseline：
   - 模态过滤。
   - role/artifact 过滤。
   - Binding enabled 过滤。
   - runtime availability 过滤。
   - priority/cost 排序。
   - 默认参数解析。
6. 扩展 Quality Contract 发布接口，一次接收多个已校验 item。
7. Proposal 经过用户确认后才可转换为 Contract。

### 2A / 2B 拆分

闭环 2A（已完成）：

- 四态 Applicability Validator，以及可追溯 finding。
- 最小 `EvaluationProposalV1` 与 checked-in JSON Schema。
- Proposal Validation Service，校验 Dataset/Profile fingerprint、Registry、Binding、measurement、参数、阈值和选中 Calculator。
- 公开的多 item Contract 发布请求与原子校验/发布。
- 现有单指标发布接口兼容。
- Proposal 到正式 Contract item 的受控转换；Proposal 本身不伪造平台发布状态。

闭环 2B（待实施）：

- 无需 LLM 的规则推荐 Baseline 与候选排序。
- 用户保存、提交校验或 Agent 生成 Proposal 时的 revision/findings 持久化。
- 完整 Proposal 编辑、确认与发布工作台。
- 在 2A 校验边界之上补充用户确认审计，不改变 Contract/Run canonical 生命周期。

### 持久化策略

- 临时预览可以不持久化。
- 用户保存、提交校验或 Agent 生成的 Proposal 必须持久化 revision 和 validation findings。
- Proposal 不生成 Contract ID、Run ID 或 Published 状态。

### 验收路径

```text
选择 Dataset Version
  -> Baseline 查询 Profile 和 Registry
  -> 过滤不适用 Calculator
  -> 生成结构化 Proposal
  -> 平台返回校验结果
  -> 用户调整并确认
  -> 发布包含两个 item 的 Quality Contract
```

### 完成定义

- 未注册 Metric、Calculator 和参数无法通过 Proposal 校验。
- 文本数据集不会推荐图像 Calculator。
- 缺少必需 role 时给出 `NEEDS_INPUT`，不伪装成质量失败。
- 多指标 Contract 不依赖前端拼接私有字段。

---

## 闭环 3：最小 Logical Plan 与多指标执行

### 目标

让多指标 Contract 可以表达简单依赖、编译为可追溯计划并由现有执行器执行。

### Logical Plan V1

只描述“算什么”和逻辑依赖：

- logical plan ID、Dataset Version、Contract Version。
- logical stage ID。
- Metric/measurement/scope。
- required inputs。
- `depends_on[]`。
- applicability decision。
- semantic fingerprint。

不包含具体 Python entrypoint、机器资源或临时文件路径。

### Physical Plan 增量

- 对应 logical stage ID。
- Calculator/Binding 快照。
- resolved parameters。
- input/output artifact bindings。
- `depends_on[]` 和执行 order。
- timeout、重试和 backend 的最小配置。
- cache/input fingerprint 预留字段。

### 执行器 V1

- 拓扑排序和环检测。
- 仍采用单进程顺序执行，不实现通用并行调度。
- 支持必需依赖与可选依赖。
- 支持依赖失败传播。
- 支持共享中间 Artifact 的引用。
- 对相同 feature producer 做最小去重，不做通用优化器。

Stage 调度状态与 MetricResult 状态保持分离：

```text
StageRun:
PENDING / RUNNING / SUCCEEDED / FAILED /
SKIPPED_DEPENDENCY / NOT_APPLICABLE / MISSING_INPUT

MetricResult:
OK / NOT_APPLICABLE / MISSING / ERROR
```

### 验收路径

```text
多指标 Contract
  -> Logical Plan
  -> 环与依赖校验
  -> Physical Plan
  -> 顺序执行多个 Stage
  -> 一个 Stage 失败时正确传播
  -> 生成部分成功 Run 和多指标 Report
```

### 完成定义

- Contract、Logical Plan、Physical Plan、StageRun 和 MetricResult 可以完整追溯。
- 多指标执行不依赖硬编码指标顺序。
- 质量 FAIL 不改变 Run 的执行成功状态。
- NOT_APPLICABLE、MISSING 和 ERROR 不按零分处理。

---

## 闭环 4：代表性指标扩充

### 目标

用少量代表性 Calculator 验证 Protocol、适用性、多实现和不同输入类型，不追求指标数量。

### 候选切片

| 指标/Calculator | 类型 | 优先级 | 主要验证点 |
| --- | --- | --- | --- |
| `02080103` exact-only Calculator | 确定性、低成本 | P0 | 同一 Metric 多 Calculator、成本与语义覆盖差异 |
| `0204` 结构完整性 | 确定性统计 | P0 | 字段映射、NULL/空串、reject、Profile |
| `02080106` 文本纯净程度 | 规则与统计 | P1 | HTML 残留、乱码、字符特征、evidence |
| `0101` 基本信息完整性 | 文档 Rubric | P1 | 非 SampleRecord 输入、Documentation Skill |
| `0206` 内容一致性 | Embedding/LLM 辅助 | P2 | 模型版本、成本、UNKNOWN、人工校准 |

`02080201` 图像分辨率先作为适用性测试：文本数据集必须返回 `NOT_APPLICABLE`。待图像 Artifact 和标准化链路可用后，再实现真实计算。

### 多 Calculator 约束

`02080103` 可以同时绑定：

- `exact_text_duplicate`：只覆盖 exact/normalized duplicate，成本低。
- `text_duplicate_degree`：覆盖 exact、n-gram、MinHash candidate 和 paragraph。

平台必须通过 Binding、Calculator version、resolved parameters 和 semantic coverage 区分口径。不同 Calculator 的结果不得因 metric key 相同而直接混合。

### 完成定义

- 至少两个 Metric 可以在同一正式 Run 中执行。
- 至少一个 Metric 具有两个可选择的 Calculator。
- 至少一个不适用指标被正确排除。
- LLM-assisted Calculator 进入正式链路前必须有固定模型、Prompt/Rubric、成本记录和校准集。

---

## 闭环 5：Agent 推荐 Demo

### 目标

让用户选择 Dataset Version 后，由一个主 Agent 调用平台工具生成可校验 Proposal，并复用闭环 2–4 的正式链路。

### Agent V1 架构

第一版使用轻量实现：

- `LLMProvider` 抽象。
- 原生结构化输出/Tool Calling。
- Pydantic Proposal 校验。
- 显式有限状态流程。
- 一个主 Agent，按需加载三个领域 Skill。

暂不引入 LangGraph 或多 Agent。出现长生命周期恢复、复杂 replanning、并行 Skill 编排等真实需求后再评估框架。

### 三个 Skill

1. Documentation Quality Skill：0101–0104。
2. Data Quality Skill：0201–0208 及模态子指标。
3. Model Application Skill：0301–0305；当前尚未建立，应直接遵守共享平台 ADR。

每个生产 Skill 至少声明：

- skill key/version 和所属 dimension。
- 可推荐 Metric 范围。
- 所需 Profile/Manifest 信息。
- 允许调用的 Tool。
- 推荐规则和限制。
- Proposal schema version。

Skill 不复制 Registry 中的完整指标或 Calculator 定义。

### Tool Adapter V1

- `get_dataset_version`
- `get_dataset_manifest`
- `get_dataset_profile`
- `list_metrics`
- `list_compatible_calculators`
- `check_applicability`
- `validate_calculator_parameters`
- `validate_evaluation_proposal`
- `create_contract_draft`
- `publish_contract`
- `compile_plan`
- `create_evaluation_run`
- `get_run_status`
- `get_quality_report`

第一版 Tool 直接包装 application service。MCP 只作为未来适配层，不创建第二套业务后端。

### 权限与确认

- 查询 Profile、Registry、Run 和 Report 可以自动执行。
- 发布 Contract 和创建 Run 必须经过用户明确确认。
- Agent 不能访问 ORM、数据库 Session、凭据或任意文件路径。
- Agent 不能生成未注册 Metric/Calculator，也不能修改 Calculator 结果和 Rule 判定。

### 验收路径

```text
用户选择 Dataset Version 并描述目标
  -> Agent 读取 Profile
  -> 加载 Data Quality Skill
  -> 调用兼容性查询 Tool
  -> 生成 EvaluationProposalV1
  -> 平台校验
  -> Agent 根据结构化 finding 最多修正一次
  -> 用户确认
  -> 发布 Contract
  -> 创建 Run
  -> 查看真实 Report
```

### 完成定义

- Agent 的未注册对象幻觉率为 0。
- 所有 Proposal 必须通过平台校验或明确显示失败原因。
- 未配置 LLM 时返回 `NOT_CONFIGURED`，不模拟对话结果。
- Agent 推荐失败不影响人工使用现有 Contract/Run 流程。
- Agent 对结果的解释只读取持久化事实，不重新计算指标或分数。

---

## 闭环 6：Agent 质量评估与受约束反馈

### 目标

证明 Agent 相比规则 Baseline 有实际增益，并为后续 Plan–Execute–Feedback 建立可复现基线。

### 评价集

建立固定的 Dataset Manifest、Profile、用户目标和人工期望 Proposal 案例集，覆盖：

- 文本、无标注数据。
- 缺失必需 role。
- 不适用模态。
- 同一 Metric 多 Calculator。
- 高成本 Calculator 不可用。
- 需要用户补充阈值或业务声明。

### 指标

- 指标适用性 precision/recall。
- Calculator 选择准确率。
- Proposal 首次校验通过率。
- 未注册对象幻觉率。
- 缺失条件识别率。
- 用户修改项数量和修改幅度。
- Proposal 接受率。
- token 成本和响应时间。
- 不同模型/重复运行的一致性。
- Proposal 到 Contract、Plan、Run、Report 的追溯完整率。

### 受约束反馈

V1 只允许：

```text
Agent Proposal
  -> Platform Validation Findings
  -> 在合法候选集内修正一次
  -> 用户确认
```

执行结果驱动的动态 replanning、自动换 Calculator 和自动重试放到后续版本。

### 完成定义

- Baseline 与 Agent 使用同一评价集。
- 可以量化 Agent 相对规则 Baseline 的增益和成本。
- 推荐轨迹保留输入 fingerprint、Skill、Tool 调用、Proposal revision 和 validation findings。

## 6. 推荐实施顺序与并行关系

```text
闭环 0：可信基线
  -> 闭环 1：Calculator 插件化
  -> 闭环 2：Applicability + Proposal + 多指标 Contract
       -> 闭环 3：Logical Plan + 多指标执行
       -> 闭环 4：代表性指标扩充
            -> 闭环 5：Agent 推荐 Demo
                 -> 闭环 6：Agent 质量评估
```

允许的并行关系：

- 闭环 2 完成 Proposal contract 后，闭环 3 的计划编译器与闭环 4 的 Calculator 开发可以并行。
- Documentation Skill 的平台映射可以与数据质量第二、第三个 Calculator 并行。
- Agent UI 可以在闭环 5 后半段与 Agent Runtime 并行，但不得用静态数据冒充 Tool 调用。

不建议并行：

- 闭环 0 未完成前修改 canonical contract。
- Calculator Protocol 未冻结前批量开发指标。
- Proposal Validator 未完成前接入 LLM 推荐。
- 多指标 Contract 未完成前实现复杂 Logical Plan。

## 7. 每个闭环的交付模板

每完成一个闭环，应在 `docs/progress/` 增加一份记录，至少包括：

1. 本闭环目标和非目标。
2. 实际修改的 contracts、schemas、migration、service、API 和页面。
3. 数据迁移和兼容策略。
4. 测试命令与结果。
5. 浏览器或 API 验收路径。
6. 已知限制与未完成项。
7. 下一闭环的明确前置条件。

只有满足“完成定义”并通过完整验收路径后，闭环才标记完成；局部模型、页面或接口存在不等于闭环完成。

## 8. 第一轮建议

下一次开发只进入“闭环 0：恢复可信开发基线”。完成后再确认闭环 1 的 Calculator Protocol 细节，不同时开始 Agent Runtime、Logical Plan 或新指标开发。

