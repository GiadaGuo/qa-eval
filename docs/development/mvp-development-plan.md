# qa-eval 数据质量评测平台 MVP 开发计划

- 状态：待确认
- 制定日期：2026-10-27
- 适用范围：首个可运行、可交互、具有真实数据流转能力的平台 MVP
- 首个验证数据集：`policy.jhsjk_article`
- 首个真实指标：`02080103` 重复内容程度

## 1. 文档定位

本文记录当前代码基础上的 MVP 差距、范围取舍、模块结构、最小数据模型与 API 补充、开发依赖顺序及验收路径。

相关文档的职责如下：

- `plan/v0/pm-v0.md`：产品模块、业务流程和长期能力边界。
- `plan/v0/rd-v0.md`：正式技术架构和工程约束。
- `docs/architecture/canonical-data-flow-models-v1.md`：canonical 数据流模型与不变量。
- `docs/adr/0001-skill-metric-ownership-and-shared-evaluation-layers.md`：指标 Skill 与平台共享层的职责边界。
- `docs/development/canonical-models-progress.md`：canonical contracts、schemas 和执行器的实际落地进度。
- 本文：MVP 实施范围、先后次序和逐步验收标准。

若产品、技术方案与实际代码不一致，应明确记录差异并通过版本化迁移解决，不得为了前端便利静默改变领域语义。

## 2. MVP 目标与约束

目标是在保留现有 React、FastAPI、PostgreSQL、MinIO/S3 兼容存储及 canonical 领域模型的基础上，完成以下真实链路：

```text
上传真实数据
  -> Dataset Version / Manifest / SampleRecord
  -> Dataset Profile
  -> Metric Definition / Calculator Registry
  -> Quality Contract Version
  -> Evaluation Run / Stage Run
  -> Metric Result
  -> Rule Evaluation
  -> Quality Issue / IssueIndex
  -> Quality Report
  -> Web 展示与 Agent 查询接口
```

必须遵守：

1. Dataset Version 是不可变的评测输入边界。
2. Metric Definition、Calculator Implementation 和判定规则保持独立。
3. Calculator 只生成客观 Metric Result；Rule Evaluator 独立判定。
4. Report 从已持久化结果聚合，不直接依赖 Calculator 临时返回值。
5. 正式结果必须可追溯到数据版本、契约版本、Run、StageRun、指标和 Calculator 版本。
6. `ERROR`、`MISSING`、`NOT_APPLICABLE` 和 `NOT_EVALUATED` 不得转换成数值零或质量 FAIL。
7. 本地验证产物必须保留 `LOCAL_VALIDATION` 标识，不得冒充正式平台 Run。
8. 禁止以前端写死数据或静态 Agent 回复冒充真实平台能力。

## 3. 当前实现盘点

### 3.1 已实现并可直接复用

- FastAPI 应用工厂、健康检查和 PostgreSQL Session 依赖。
- Dataset 的领域实体、Repository、Application Service、ORM 和创建/列表/详情 API。
- DatasetVersion 与 DatasetManifest 的早期领域模型、ORM 和 Alembic 迁移。
- MySQL schema、record count 和 preview API。
- React + TypeScript + Vite + Ant Design 前端工程。
- `DatasetManifest 2.0`、`SampleRecord 2.0`、`RejectedRecord 1.0`、`MetricResult 1.0` canonical contracts 和 JSON Schema。
- `QualityContractVersion 1.0`、`RuleEvaluation 1.0`、`QualityIssue 1.0`、`IssueIndex 1.0`、`QualityReport 1.0` contracts 和 JSON Schema。
- 共享 Rule Evaluator 和 Quality Report Generator。
- `02080103` Calculator v2.0.0、证据 schema、运行脚本和回归测试。
- `policy.jhsjk_article` 的真实快照、14,960 条 SampleRecord、MetricResult、Rule、7,562 条 Issue 和最终报告产物。
- PostgreSQL、Redis 和 MinIO/S3 兼容服务的 Docker Compose 定义。

真实数据基线：

| 项目 | 当前结果 |
| --- | ---: |
| 输入记录 | 14,960 |
| 有效文本 | 14,928 |
| 排除记录 | 32 |
| exact duplicate affected | 89 |
| exact redundant confirmed | 45 |
| near-duplicate candidates | 7,517 |
| 当前规则 | `exact_redundant_record_ratio <= 0.01` |
| 当前 gate | PASS |

### 3.2 部分实现

| 能力 | 已有基础 | 主要差距 |
| --- | --- | --- |
| Dataset | ORM、Repository、API | 仍是旧简化字段；缺 owner/namespace 与 UUIDv7 新建策略 |
| Dataset Version | 状态实体、ORM、迁移 | 无版本 API、上传、发布服务和 canonical artifact 引用 |
| Manifest | 旧 ORM JSONB + canonical v2 contract | 尚未统一写入、读取和校验边界 |
| 标准化 | MySQL adapter 和真实迁移脚本 | 无平台 NormalizationRun、服务和 API |
| Calculator | 真实算法和证据可用 | 尚未注册为平台 Calculator entrypoint |
| Contract | contract、publisher 和真实实例 | 无通用应用服务、数据库和 API |
| Rule/Issue/Report | contract、执行器和真实产物 | 无 ORM、Repository、API 和 Web 展示 |
| 前端 | MySQL 字段映射原型 | 无平台布局、路由和六个业务模块 |

### 3.3 尚未实现

- 浏览器文件上传和 ArtifactStore 应用端口。
- JSONL/Parquet 的平台级注册、标准化和发布闭环。
- DatasetProfile contract、profiler、持久化和 API。
- MetricDefinition、CalculatorDescriptor 和 Binding 的 executable contract、Registry、持久化和 API。
- Logical/Physical Plan executable contract。
- 正式 EvaluationRun、StageRun、状态机和持久化。
- MetricResult、RuleEvaluation、QualityIssue、IssueIndex、QualityReport 的数据库控制面。
- 报告综合评分的明确、版本化聚合规则。
- 前端数据集、指标、契约、任务、报告和 Agent 页面。
- LLM/Agent 模型接入。
- 浏览器端端到端测试。

## 4. MVP 范围取舍

### 4.1 保留

- React + TypeScript + Vite + Ant Design。
- FastAPI、Pydantic v2、SQLAlchemy、Alembic 和 PostgreSQL。
- MinIO/S3 兼容对象存储作为数据面。
- canonical contracts 和严格 schema 校验。
- Dataset Version 不可变边界。
- Registry、Contract、Run、Result、Rule、Issue、Report 的对象分层。
- `02080103` 真实 Calculator、证据与回归数据。
- 高基数 evidence/issue 文件存对象存储，数据库保存控制面与索引。

### 4.2 简化

- Planner 仅生成顺序执行的最小 Physical Plan，不实现通用 DAG 编译和拓扑排序。
- 单进程执行器按 Contract item 顺序调用 Calculator。
- StageRun 首期仅支持一次 attempt，不做自动重试。
- 首批只让 `02080103` 进入可执行状态；其他指标可以注册为未实现或不可执行。
- observations、parameters、summary 等结构先使用经过 Pydantic 校验的 JSONB。
- Contract 生命周期先支持 Draft、Publish、Retire。
- Issue 高基数明细保留在 JSONL/Parquet；PostgreSQL 保存索引和必要的 confirmed issue 查询字段。
- Agent 未接模型时只提供明确的 `NOT_CONFIGURED` 状态和接口契约。

### 4.3 推迟

- Celery、Redis 队列和分布式 Worker。
- 通用 DAG 调度、并行 stage、复杂重试、租约、心跳和缓存复用。
- Spark、Ray、Kubernetes 和多机执行。
- 数据质量指标的完整横向实现。
- 图像、音频、视频真实 Calculator。
- 完整 RBAC、多租户、审计和高级可观测性。
- Issue 人工反馈、自动修复和复评闭环。
- LLM 自动评分或默认质量门禁。

## 5. 前端模块与路由

统一布局包括左侧模块导航、顶部标题和操作区、主内容区，以及统一的 loading、empty、error、not configured 和 not evaluated 状态。

```text
/
├── /datasets
│   ├── /new
│   └── /:datasetId
│       └── /versions/:versionId
├── /metrics
│   └── /:metricKey/:version
├── /contracts
│   ├── /new
│   └── /:contractVersionId
├── /runs
│   ├── /new
│   └── /:runId
├── /reports
│   └── /:reportId
└── /agent
```

### 5.1 数据集管理

- 数据集列表与创建。
- JSONL 文件上传。
- Dataset Version、Manifest 和 artifact 信息。
- 字段结构、SampleRecord 预览和 reject 摘要。
- Dataset Profile 统计。

### 5.2 指标管理

- 指标定义以卡片形式展示，支持按指标编码或名称搜索。
- 点击指标卡打开指标详情窗口；窗口内按 Binding 列出该指标可用的 Calculator 卡片。
- measurement、单位、方向、适用模态和语义覆盖。
- Calculator 版本、entrypoint、参数 schema、输入角色、输出 schema 和证据类型。
- 指标与 Calculator Binding 状态。
- 从可用 Calculator 卡片发起“构建质量契约”，进入规则配置向导。
- 该入口只负责携带已选 Metric、Calculator 和 Binding 上下文；Contract、Run 和 Report 仍由各自后端领域服务持久化，禁止把规则或运行状态写入 Registry。

### 5.3 契约管理

- 选择 Dataset Version。
- 选择 Metric、measurement 和 Calculator 实现约束。
- 配置 Calculator 参数、operator、threshold、severity 和 `on_missing`。
- 发布前校验；已发布版本只读。

### 5.4 评测任务

- 创建 EvaluationRun。
- 展示 Dataset Version、Contract、状态、时间、已完成/失败指标和错误。
- 展示 StageRun、MetricResult 和 RuleEvaluation。

### 5.5 质量报告

- execution status、gate status 和 coverage。
- 综合分、维度分及其计算覆盖范围。
- 指标 observation、阈值、判定和证据。
- confirmed issue 与 candidate issue 分开展示。
- Issue 分页、证据定位和受控 SampleRecord 预览。

### 5.6 Agent

- 消息、历史记录和当前 Dataset/Report 上下文。
- 后端未配置模型时明确显示“Agent 尚未接入”。
- 接入后通过后端应用服务查询真实数据，不依赖前端临时状态。

## 6. 最小数据模型补充

### 6.1 新增 canonical contracts

- `TemporalValue`
- `NormalizationRun`
- `DatasetProfile`
- `MetricDefinition`
- `CalculatorDescriptor`
- `MetricCalculatorBinding`
- MVP `PhysicalEvaluationPlan`
- `EvaluationRun`
- `StageRun`

Logical Plan 保留设计和扩展接口；MVP 可以不实现通用 DAG compiler。Physical Plan 保存不可变的 Calculator 绑定、完整参数、输入输出 schema 和顺序 stage。

### 6.2 PostgreSQL 控制面

最小新增表：

- `artifacts`
- `dataset_profiles`
- `metric_definitions`
- `calculator_implementations`
- `metric_calculator_bindings`
- `quality_contracts`
- `quality_contract_versions`
- `physical_evaluation_plans`
- `evaluation_runs`
- `stage_runs`
- `metric_results`
- `rule_evaluations`
- `quality_issues`
- `issue_indices`
- `quality_reports`

### 6.3 现有表的兼容迁移

`datasets`：

- 增加 `namespace`。
- `owner_id` MVP 可使用开发环境默认主体。
- 旧 `modality` 字段先通过 mapper 兼容为 canonical `modalities`，避免破坏性迁移。

`dataset_versions`：

- 旧 `version` 兼容映射为 `version_label`。
- 增加 manifest、raw、normalized artifact 引用和 content fingerprint。
- 状态收敛到 canonical 状态。
- 新记录使用 UUIDv7；历史 UUIDv4 保留，不原地重写。

`dataset_manifests`：

- JSONB 写入前必须经过 `DatasetManifestV2` 校验。
- 保存 schema version。
- 校验 document 中 ID 与数据库外键一致。

迁移采用新增列、回填、双读兼容、切换写路径的顺序，不大规模重构或原地改写历史产物。

## 7. 综合评分策略

当前 `QualityReport 1.0` 只有 gate、coverage、Metric Result 摘录和 Issue 汇总，没有已冻结的综合评分算法。前端不得自行平均 PASS/FAIL、原始比例或指南权重。

MVP 建议由 Quality Contract 显式配置版本化 scoring policy：

```text
scoring:
  method: BINARY_RULE | LINEAR_RANGE | PIECEWISE
  weight: number
  best: optional number
  worst: optional number
```

聚合约束：

1. 只有配置 scoring 且 MetricResult 为 `OK` 的项目进入分数。
2. `NOT_APPLICABLE` 从分母排除。
3. `MISSING`、`ERROR` 和 `NOT_EVALUATED` 不记零分，单独进入 coverage 和可信度提示。
4. Report 保存 `score`、`scored_weight`、`configured_weight` 和评分算法版本。
5. 首个单指标报告只能称为“当前契约已评测项得分”，不得冒充完整数据质量维度得分。

该模型调整需在编码前单独确认，并通过新 schema 和 migration 落地。

## 8. 最小 API 方案

### 8.1 Dataset 与 Dataset Version

```http
POST /api/v1/datasets
GET  /api/v1/datasets
GET  /api/v1/datasets/{dataset_id}

POST /api/v1/datasets/{dataset_id}/versions
POST /api/v1/dataset-versions/{version_id}/files
POST /api/v1/dataset-versions/{version_id}/publish
GET  /api/v1/dataset-versions/{version_id}
GET  /api/v1/dataset-versions/{version_id}/manifest
GET  /api/v1/dataset-versions/{version_id}/samples
GET  /api/v1/dataset-versions/{version_id}/profile
```

### 8.2 Registry

```http
GET /api/v1/metrics
GET /api/v1/metrics/{metric_key}/versions/{version}
GET /api/v1/calculators
GET /api/v1/calculators/{calculator_key}/versions/{version}
```

### 8.3 Quality Contract

```http
POST /api/v1/contracts
PUT  /api/v1/contracts/{contract_id}/draft
POST /api/v1/contracts/{contract_id}/publish
GET  /api/v1/contracts
GET  /api/v1/contract-versions/{contract_version_id}
POST /api/v1/contract-versions/{contract_version_id}/validate
```

### 8.4 Evaluation Run

```http
POST /api/v1/evaluation-runs
GET  /api/v1/evaluation-runs
GET  /api/v1/evaluation-runs/{run_id}
GET  /api/v1/evaluation-runs/{run_id}/metric-results
GET  /api/v1/evaluation-runs/{run_id}/rule-evaluations
GET  /api/v1/evaluation-runs/{run_id}/issues
```

创建 Run 的最小输入：

```json
{
  "dataset_version_id": "uuid",
  "contract_version_id": "uuid",
  "idempotency_key": "client-generated-key"
}
```

### 8.5 Report 与 Agent

```http
GET  /api/v1/reports
GET  /api/v1/reports/{report_id}
GET  /api/v1/reports/{report_id}/issues
GET  /api/v1/issues/{issue_id}/evidence

GET  /api/v1/agent/status
POST /api/v1/agent/conversations
POST /api/v1/agent/conversations/{conversation_id}/messages
```

## 9. 单进程执行器

MVP 执行流程：

1. 接收 `dataset_version_id` 和 `contract_version_id`。
2. 校验 Dataset Version 已发布且 artifact 可读取。
3. 校验 Contract 已发布，Metric、measurement、参数和 Calculator Binding 有效。
4. 生成不可变的最小 Physical Plan。
5. 创建 `EvaluationRun(PENDING)`，然后更新为 `RUNNING`。
6. 按 Contract item 顺序创建 StageRun 并调用 Calculator。
7. 原子提交 MetricResult 和 EvidenceArtifact 引用。
8. 执行 Rule Evaluator；错误或缺失结果生成 UNKNOWN/SKIPPED，不生成伪 FAIL。
9. 从已提交 evidence 生成 QualityIssue 和 IssueIndex。
10. 从已持久化事实生成 QualityReport。
11. 根据执行事实更新 Run 为 `SUCCEEDED`、`PARTIAL_SUCCEEDED` 或 `FAILED`。

质量 gate 状态与执行状态必须分离：质量 FAIL 不等于 Run FAILED，Run SUCCEEDED 也不等于质量 PASS。

## 10. 按依赖排序的开发任务

### T1：共享协议补齐

- 增加 Profile、Registry、Run 和 Stage contracts/schemas。
- 定义最小 Physical Plan。
- 单独确认综合评分策略。
- 增加 Pydantic/JSON Schema 一致性测试。

### T2：Dataset Version 上传与画像

- 实现 ArtifactStore port 和 MinIO/S3 adapter。
- 实现 JSONL 上传、checksum 和 artifact 提交。
- 实现 DatasetVersion/Manifest 发布服务。
- 标准化为 SampleRecord 2.0，生成 reject 和 DatasetProfile。

### T3：Metric/Calculator Registry

- 将 `02080103.yaml` 拆分为 MetricDefinition 和 CalculatorDescriptor。
- 注册 Python Calculator entrypoint 和参数 schema。
- 建立 Binding、Repository 和只读 API。
- 指标管理首页改为指标卡片网格，支持按指标编码或名称搜索。
- 指标详情使用窗口展示 Metric Definition，并只列出通过 Binding 关联的 Calculator 卡片。
- Calculator 不可用、Binding 停用或不兼容时禁用“构建质量契约”，并展示明确原因。

### T4：Quality Contract

- 将现有 publisher 收敛为平台 application service。
- 持久化 Draft 和 Published Contract Version。
- 校验 measurement、参数类型、规则类型和 Calculator 可用性。

### T5：Evaluation Run

- 实现最小 Physical Plan、EvaluationRun 和 StageRun。
- 实现单进程顺序执行器。
- 将现有 `02080103` Calculator 接入正式 Run。
- 持久化 MetricResult 和 evidence 引用。

### T6：Rule、Issue 与 Report

- 复用现有 Rule Evaluator 和 Report Generator。
- 持久化 RuleEvaluation、QualityIssue 和 IssueIndex。
- 从已持久化事实生成 QualityReport 和显式评分。
- 实现 Issue 分页、筛选和证据 API。

### T7：React 平台

- 建立统一布局、路由、API client 和错误处理。
- 依次接入 Dataset、Registry、Contract、Run 和 Report。
- 禁止直接 import 历史 JSON 作为正式页面数据。

### T8：Agent

- 实现只读的 Dataset/Metric/Run/Report 查询服务。
- 定义对话上下文 DTO 和 Agent status API。
- 有模型配置后再启用真实对话；未配置时明确展示不可用状态。

### T9：端到端验收

- 浏览器上传真实 JSONL。
- 发布 Dataset Version 并查看画像。
- 创建并发布 Contract。
- 创建 EvaluationRun 并真实调用 Calculator。
- 查看 MetricResult、Rule、Issue、Report 和 evidence 下钻。

## 11. 最短可交付路径

每次只完成一个可独立验证的纵向闭环。

### 闭环 1：API/UI 读取现有真实产物

目的：最快建立真实报告展示，不用模拟数据。

验收：

- 后端 API 返回现有 Dataset Version、MetricResult、Rule、Issue 和 Report。
- React 展示当前真实报告与问题下钻。
- 页面明确显示 `LOCAL_VALIDATION`，不将其登记为正式平台 Run。

### 闭环 2：真实文件上传与 Dataset Profile

验收：

- 浏览器上传 JSONL 并创建不可变 Dataset Version。
- Manifest、checksum、record count、SampleRecord 和 reject 数量闭合。
- Profile 来自完整数据，不以 preview 代替。

### 闭环 3：指标驱动的评测入口（重构）

目的：用户从指标卡片进入 Calculator 选择、质量契约配置、后台评测和结果查看。该闭环是统一的产品流程，但实现上继续保持 Registry、Contract、Run 和 Report 四层解耦。

#### 3A：指标卡片与 Calculator 选择

验收：

- `02080103` 定义与 Calculator 独立注册。
- 指标管理首页以卡片展示已注册指标。
- 搜索框可以按指标编码或名称匹配指标卡。
- 点击指标卡打开详情窗口，展示 definition、measurement、适用范围和语义限制。
- 详情窗口只列出通过 Binding 关联的 Calculator 卡片，并展示名称、版本、输入角色、参数 schema、输出 schema 和证据能力。
- Binding 停用或 Calculator 不可用时不得进入可执行流程，并展示原因。

#### 3B：从 Calculator 构建质量契约

验收：

- 用户在 Calculator 卡片点击“构建质量契约”后进入配置向导。
- 向导携带 Registry 中真实的 metric、measurement、calculator 和 binding 标识，不复制或篡改 Registry 定义。
- 用户选择已发布 Dataset Version，配置 Calculator 参数、operator、threshold、severity 和 `on_missing`。
- measurement、参数类型、阈值类型和 Calculator 兼容性由后端校验。
- Published Contract Version 不可修改，只能发布新版本。

#### 3C：异步提交正式评测

验收：

- 契约发布后可点击“开始评测”，提交 `dataset_version_id` 与 `contract_version_id`。
- 后端创建正式 EvaluationRun 并异步执行；API 立即返回 `run_id`，前端通过轮询或事件接口更新状态。
- 单进程后台执行器真实调用已绑定 Calculator，不要求 Celery 或 Redis。
- MetricResult 可追溯到 Run、Stage、Dataset、Metric、Calculator 和 Contract。
- Calculator 失败记录 ERROR，不产生伪造的质量 FAIL；质量 FAIL 与执行 FAILED 分离。

#### 3D：评测结果模块

验收：

- 主导航增加“评测结果”，替代面向用户的“本地验证报告”入口。
- 页面结构可以复用 `LocalValidationReportPage`，但数据必须来自正式 Run/Report API。
- 列表展示 Run 状态、数据集版本、契约、开始/完成时间及成功/失败指标数。
- 详情展示 MetricResult、RuleEvaluation、QualityIssue、QualityReport 和 evidence 下钻。
- RuleEvaluation 只读取已持久化 observation；Report 只聚合已持久化结果、规则和 IssueIndex。
- confirmed 与 candidate 问题独立展示；未评测、失败和不适用项目不按零分处理。
- `LOCAL_VALIDATION` 页面在正式结果链路验收前保留为开发诊断入口，不冒充平台 EvaluationRun。

#### 闭环 3 完整验收路径

```text
搜索并选择指标卡
  -> 查看该指标的 Calculator 卡片
  -> 选择 Calculator 并构建质量契约
  -> 发布 Contract Version
  -> 提交后台 EvaluationRun
  -> 在“评测结果”查看报告和证据
```

闭环 3 可以按 3A、3B、3C、3D 分批提交，但只有四部分均接入真实后端并通过完整路径验收后，闭环 3 才能标记为完成。

### 最终验收：浏览器端完整业务流程

```text
上传真实 JSONL
  -> 发布 Dataset Version
  -> 查看 Profile
  -> 搜索并打开 02080103 指标卡
  -> 选择“文本重复内容计算器”
  -> 配置规则并发布 Contract
  -> 提交后台 Evaluation Run
  -> 在“评测结果”查看 Metric Result / Rule / Issue / Report / evidence
```

## 12. 当前验证基线

2026-10-27 审查结果：

- unit + contract tests：103 passed。
- 完整 pytest：105 passed、6 skipped、8 errors。
- 8 个 error 均发生在 PostgreSQL 测试库连接阶段，当前审查环境无法访问 `127.0.0.1:5432`；未观察到业务断言失败。
- 6 个 MySQL integration tests 因未启用 `RUN_MYSQL_INTEGRATION=1` 跳过。
- 前端 build 通过。
- 前端 lint 通过。
- canonical schema 漂移检查通过。
- 真实完成报告的 Pydantic、JSON Schema、checksum 和引用一致性校验均通过。

## 13. 开发与提交约定

1. 用户确认本计划后再开始修改业务代码。
2. 每次只完成一个可独立运行、测试和验收的闭环。
3. 优先复用现有 Calculator、contracts、执行器和真实产物。
4. 新增或修改 canonical 模型时，同步更新 Pydantic、JSON Schema、migration、API DTO 和一致性测试。
5. 不覆盖历史运行目录；不可变产物使用新版本或新 Run ID。
6. 当前工作树已有未提交内容，开发时必须避免覆盖或混入无关变更。
7. 每个闭环交付时说明修改文件、运行命令、测试结果、未解决风险和下一步。

## 14. 开始实施前需要确认的决策

1. 是否按本文的“最短可交付路径”从闭环 1 开始。
2. 综合评分是否采用 Contract 显式 scoring policy；具体首版算法另行确认。
3. MVP 文件上传是否首批只支持 JSONL，Parquet 放到后续闭环。
4. Agent 是否接受“先完成 UI、status API 和后端查询契约，未配置模型时明确不可用”。

