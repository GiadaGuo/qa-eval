# qa-eval 数据集质量评测平台

qa-eval 是一个数据集质量评测平台 MVP：把数据集版本化地接入平台，通过可注册的指标/Calculator
体系执行评测运行，产出可追溯的质量结果、证据与报告；并支持「说明文档评测」与「Agent
对话式评测工作台」两条辅助链路。

- 后端：Python 3.12+ / FastAPI / SQLAlchemy 2 / Alembic / SQLite（`uv` 管理依赖）
- 前端：React 19 + TypeScript + Ant Design 6 + Vite（`apps/web`）
- 语义契约：Pydantic 模型（`src/qa_eval/contracts/`）生成冻结的 JSON Schema（`schemas/`）

## 目录

- [仓库结构](#仓库结构)
- [快速开始](#快速开始)
- [模块与数据流转](#模块与数据流转)
- [数据持久化](#数据持久化)
- [指标注册表（Registry）](#指标注册表registry)
- [如何新增一个 Calculator](#如何新增一个-calculator)
- [API 一览](#api-一览)
- [测试](#测试)
- [更多文档](#更多文档)

## 仓库结构

```
apps/
  api/                  FastAPI 应用（路由、依赖注入、后台执行器）
  web/                  React 前端（Vite dev server 把 /api 代理到 127.0.0.1:8000）
src/qa_eval/
  application/          应用服务层
    services/           数据集/版本/契约/Run/结果/注册表等业务服务
    agent/              Agent 会话运行时（runtime、skill/tool 注册表）
    document_evaluation/ 说明文档评测流水线（解析、rubric、prompt、校验、报告）
    evaluation/         规则评估、报告生成、运营评分
    profiling/          数据集画像（DatasetProfile）
    normalization/      jhsjk 文章标准化 adapter
  calculators/          平台 Calculator 实现（受信任包，见下文）
  contracts/            Pydantic wire 契约（metrics/execution/quality/datasets 等）
  domain/               领域实体、repository 端口、ArtifactStore 端口
  infrastructure/       db（SQLAlchemy 模型与仓库）、llm、mysql、storage（本地/S3）
  plugins/adapters/     字段映射 adapter（FieldMappingSpec → SampleRecord 2.0）
configs/registry/       指标注册表文件源：metrics/ calculators/ bindings/ 三目录
configs/document-evaluation/ 说明文档评测 rubric（冻结版本 JSON）
migrations/             Alembic 迁移（env.py 只读 QA_EVAL_DATABASE_URL）
schemas/                由 contracts 生成的冻结 JSON Schema
deploy/                 历史/可选 docker-compose（PostgreSQL/Redis/MinIO）
data/runtime/           本地 SQLite 数据库
data/artifacts/         本地 ArtifactStore 根目录（数据面产物）
docs/                   架构文档、ADR、开发计划、SOP
external/dingo/         外部 dingo 仓库（参考/集成用，非平台运行时依赖）
scripts/                数据导出、校验、Schema 生成等脚本
tests/                  unit + integration（pytest）
```

## 快速开始

本地单机模式不需要 Docker、PostgreSQL、Redis 或 MinIO：

```bash
# 初始化 .venv、SQLite schema 和本地 Artifact 目录，然后启动单 worker API
./scripts/start-local.sh

# 另开终端启动前端；Vite 把 /api 代理到 127.0.0.1:8000
cd apps/web
npm install
npm run dev
```

启动脚本默认使用 `data/runtime/qa-eval.sqlite3` 和 `data/artifacts/`，即使旧 `.env`
仍含 PostgreSQL/S3 配置也会切到本地模式。只初始化、不启动 API：

```bash
QA_EVAL_INIT_ONLY=1 ./scripts/start-local.sh
```

开发时需要自动重载可设置 `QA_EVAL_RELOAD=1`。API 默认地址为
`http://127.0.0.1:8000`；启动后同步指标注册表：

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/registry/sync
```

可选依赖：文本困惑度（02080101）需要 KenLM：`uv sync --extra kenlm`，模型准备见
`docs/development/kenlm-model-preparation.md`。

## 模块与数据流转

整体分层：`apps/api`（HTTP + 后台线程）→ `application`（服务编排）→ `domain`（实体与端口）
→ `infrastructure`（SQLite / 可选 PostgreSQL / MySQL / LLM / 存储）。平台共四条业务链路。

### 链路 A：数据集接入（闭环 2）

```
MySQL 数据源（schema 探查/预览）或本地 JSONL 上传
  → POST /datasets 创建 Dataset（逻辑身份，UUIDv7）
  → POST /datasets/{id}/versions 创建 DatasetVersion（DRAFT）
  → 上传原始文件 / 保存 FieldMappingSpec（字段 → content role / metadata / sample_id ...）
  → POST /dataset-versions/{id}/publish：
       运行标准化（plugins/adapters，按 FieldMappingSpec）
       → 产出 SampleRecord 2.0（contents[]/annotations[]/source/lineage）
       → 写 Artifact：raw + normalized（sha256 校验、两段式提交）
       → 生成 DatasetManifest 2.0 与 DatasetProfile
       → 版本置 PUBLISHED（此后内容不可变）
```

此后所有评测都引用不可变的 `dataset_version_id`；`/dataset-versions/{id}/samples`、
`/manifest`、`/profile` 提供只读视图。

### 链路 B：指标评测主链路（闭环 3）

```
configs/registry（metrics/calculators/bindings JSON）
  → POST /registry/sync 同步进数据库（Registry 三表）
  → 前端「质量契约构建页」或 Agent 推荐，选择指标 + measurement + 阈值
  → POST /quality-contract-versions 发布 QualityContractVersion（DRAFT→PUBLISHED，不可变）
  → POST /evaluation-runs {contract_version_id}（202 立即返回）：
       create：编译 LogicalEvaluationPlan（算什么：指标/参数/依赖 DAG）
              + PhysicalEvaluationPlan（怎么执行：Calculator 绑定、resolved 参数）
       派发后台单线程执行 execute()：
       逐 StageRun 运行 → TrustedCalculatorLoader 加载 Calculator
         （校验 entrypoint 白名单、运行时依赖、身份与 code_digest）
       → Calculator.execute() 消费 normalized SampleRecord，
         产出 MetricResult（observations/population/semantic_coverage）
         + Evidence 产物（样本级明细，写 ArtifactStore）
  → POST /evaluation-runs/{run_id}/finalize：
       RuleEvaluation（契约阈值判定 PASS/FAIL/UNKNOWN）
       QualityIssue（可定位问题样本，CONFIRMED/CANDIDATE）+ IssueIndex
       QualityReport（汇总快照，DB JSON/JSONB + artifact 双写）
  → 前端「评测结果页」：Run 列表、雷达图、规则明细、Issue 分页下钻
```

SHARED_INFERENCE 模式的 Calculator（如共享 LLM 推理指标）由 Run 服务先做一次记录级共享
推理，再按 `score_shared` 对多指标分别打分，避免重复调用 LLM。

### 链路 C：说明文档评测（P3）

```
上传 .md（≤2MB，UTF-8）→ document_versions（版本元数据 + 原始 artifact）
  → POST /document-report-tasks（202，后台单线程逐阶段 commit）
       解析：parse_markdown → 行号化 lines.json（回写 parsed_artifact_uri）
       逐指标：DocumentEvaluator 调 OpenAI 兼容 LLM（冻结 rubric + 行号化全文），
              Pydantic + 程序语义双层校验，失败带反馈重试，耗尽记 ERROR 而非 0 分
       报告：程序复算分数 → document_quality_reports + reports/{id}/report.json
  → GET /document-quality-reports/{id}（前端报告详情页）
```

### 链路 D：Agent 评测工作台

```
POST /agent-sessions（要求 DatasetVersion 已 PUBLISHED）
  → POST .../messages：AgentRuntime 有界单 Agent 流程
       关键词选 Skill（skill_registry，当前仅 data-quality v1.0.0）
       → 固定顺序调用 3 个只读工具：get_dataset_profile → discover_metrics
         → recommend_evaluation_proposal（每次调用落 tool_invocations 审计）
       → 推荐结果落 proposal_revisions（proposal + validation 快照）
  → 人工确认事件（幂等，靠消息 attachments 防重）：
       confirm_quality_contract → 发布 QualityContractVersion
       confirm_evaluation_run   → 创建真实 Evaluation Run（复用链路 B）
  → observe：轮询真实 Run 状态与质量报告，终态后写完成消息、会话转 COMPLETED/FAILED
```

Agent 的所有「事实」都来自被审计的工具调用（与 REST API 共用同一批服务）；LLM 只负责
组织语言，未配置 `QA_EVAL_AGENT_LLM_ENABLED` 时退化为确定性中文摘要。

## 数据持久化

### SQLite 控制面（可查询事实）

本地默认数据库为 `data/runtime/qa-eval.sqlite3`，启用 WAL、外键约束和 30 秒
busy timeout。单机模式必须只启动一个 API worker；PostgreSQL 仍作为可选兼容后端保留。

一次性从旧 PostgreSQL 审计并选择性重建业务必需数据，可使用
`scripts/migrate_business_data_to_sqlite.py`。该命令默认只生成 dry-run 报告，必须显式传入
`--apply` 才写 SQLite；详细取舍见
`docs/plan/local-persistence-stage3-result-2026-11-04.md`。

| 分组       | 表                                                                                                                                          | 内容                                           |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| 数据集     | `datasets`、`dataset_versions`、`dataset_manifests`、`dataset_profiles`                                                             | 逻辑身份、不可变版本、Manifest/Profile 元数据  |
| 注册表     | `metric_definitions`、`calculator_implementations`、`metric_calculator_bindings`                                                      | configs/registry 同步后的三层数据（JSON 文档） |
| 计划与运行 | `logical_evaluation_plans`、`physical_evaluation_plans`、`evaluation_runs`、`stage_runs`、`metric_results`                        | 计划 DAG、运行状态机、阶段执行与聚合结果       |
| 质量结论   | `quality_contract_versions`、`rule_evaluations`、`quality_issues`、`issue_indices`、`quality_reports`、`scored_quality_reports` | 契约、阈值判定、问题、报告索引与运营评分       |
| 说明文档   | `document_versions`、`document_report_tasks`、`document_quality_reports`                                                              | 文档版本、异步任务（含配置快照）、LLM 评测报告 |
| Agent      | `agent_sessions`、`conversation_messages`、`skill_invocations`、`tool_invocations`、`proposal_revisions`                          | 会话、消息、调用审计、Proposal 修订            |

高基数的样本级事实不进数据库，只存聚合 + 证据引用（canonical 不变量，见
`docs/architecture/canonical-data-flow-models-v1.md` §10–11）。

### ArtifactStore 数据面（高基数产物）

由 `QA_EVAL_ARTIFACT_STORE_TYPE` 选择 `local`（默认，根目录
`QA_EVAL_ARTIFACT_STORE_LOCAL_ROOT=data/artifacts`）或 `s3`。写入统一走
staging → sha256 校验 → commit，数据库只引用已提交产物。本地布局：

```
data/artifacts/
  datasets/{dataset_id}/...                          raw / normalized / manifest / profile
  datasets/{dataset_id}/documents/{doc_id}/...        说明文档原始文件、parsed/、reports/
  evaluation-runs/{run_id}/...                        StageRun 输出、metric 证据、issue 索引、报告 JSON
```

### 配置（.env，前缀 QA_EVAL_，优先级 环境变量 > .env > 默认值）

| 变量                                                                | 说明                                                              |
| ------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `QA_EVAL_DATABASE_URL`                                            | 默认 `sqlite:///data/runtime/qa-eval.sqlite3`；Alembic 也读取它 |
| `QA_EVAL_TEST_DATABASE_URL`                                       | 测试数据库；默认示例为独立 SQLite 文件，测试优先使用临时目录      |
| `QA_EVAL_ARTIFACT_STORE_TYPE` / `..._LOCAL_ROOT` / `..._S3_*` | 数据面存储选择与参数                                              |
| `QA_EVAL_LLM_BASE_URL` / `..._API_KEY` / `..._MODEL`          | OpenAI 兼容 LLM 网关（说明文档评测、共享推理）                    |
| `QA_EVAL_KENLM_MODEL_PATH`                                        | 02080101 困惑度模型路径；未配置时该 Calculator 拒绝执行           |
| `QA_EVAL_DOCUMENT_RUBRIC_PATH`                                    | 说明文档评测 rubric（冻结版本）                                   |
| `QA_EVAL_AGENT_LLM_ENABLED`                                       | Agent 是否用 LLM 组织回复（默认 false）                           |
| `MYSQL_HOST` 等                                                   | 外部 MySQL 数据源只读访问（无前缀）                               |

## 指标注册表（Registry）

三层模型，文件源在 `configs/registry/`，通过 `POST /api/v1/registry/sync` 幂等 upsert 进数据库：

1. **MetricDefinition**（`metrics/{metric_key}-v{version}.json`）：定义「测什么」——
   编码（如 `02080106`）、`measurements[]`（每个观测值的类型/单位/方向）、适用范围、
   语义覆盖与口径说明。不含算法参数与阈值。
2. **CalculatorDescriptor**（`calculators/{key}-v{version}.json`）：定义「怎么算」——
   entrypoint（限 `qa_eval.calculators.*`）、`parameter_schema`、`code_digest`（实现源码
   sha256 指纹）、运行时依赖、`availability`（占位实现为 `UNAVAILABLE`，无 entrypoint，
   Loader 会拒绝加载）。
3. **Binding**（`bindings/{metric_key}-{calculator}.json`）：metric ↔ calculator 多对多
   绑定，带优先级、兼容约束（schema 版本、required roles）与启用开关。

运行期由 `TrustedCalculatorLoader` 强校验：entrypoint 白名单、依赖可用、
`calculator_key/version` 与 descriptor 一致、`code_digest` 与实现文件当前内容一致，
任何一项不符即拒绝执行。`GET /registry/readiness` 汇总可执行性。

## 如何新增一个 Calculator

以新增（或把占位实现为真实）一个文本指标 Calculator 为例：

1. **实现 Calculator**：新建 `src/qa_eval/calculators/<name>.py`，满足
   `qa_eval.calculators.protocol.Calculator` Protocol（共享推理型实现
   `SharedInferenceCalculator` 的 `score_shared`）：

   - 类属性 `calculator_key`、`version`；
   - `source_paths()` 返回参与指纹的全部源文件（含规则/算法辅助文件）；
   - `validate(context, parameters)` 校验输入 schema 与参数，失败抛
     `CalculatorValidationError`；
   - `execute(context, parameters)` 返回 `CalculatorOutput`
     （`observations` 的 key 必须在 MetricDefinition 的 `measurements[]` 中声明，
     并如实填写 `population`、`semantic_coverage`、`evidence`）。

   参考实现：`src/qa_eval/calculators/text_purity.py`。
2. **计算 code_digest**（descriptor 必须与实现逐字节一致）：

   ```bash
   uv run python -c "
   from qa_eval.calculators.loader import TrustedCalculatorLoader
   from qa_eval.calculators.<name> import <CalculatorClass>
   print(TrustedCalculatorLoader.code_digest(<CalculatorClass>()))
   "
   ```
3. **写 Descriptor**：`configs/registry/calculators/<key>-v<version>.json`，
   填 `entrypoint: "qa_eval.calculators.<name>:<CalculatorClass>"`、上一步的
   `code_digest`、`availability.status: "AVAILABLE"`、`parameter_schema` 与
   `semantic_coverage`。（新指标还需 `metrics/` 下新增 MetricDefinition，
   `measurements[]` 与 Calculator 输出一一对应。）
4. **写 Binding**：`configs/registry/bindings/<metric_key>-<calculator>.json`，
   声明兼容的 `sample_record_schema` 与 `required_roles`，`enabled: true`。
5. **同步并验证**：

   ```bash
   curl -s -X POST http://127.0.0.1:8000/api/v1/registry/sync
   curl -s http://127.0.0.1:8000/api/v1/registry/readiness   # ready 应为 true
   uv run pytest tests/unit/test_calculator_loader.py        # digest 一致性有单测兜底
   ```
6. **（可选）补测试与状态文档**：为算法逻辑加 `tests/unit/test_<name>.py`；
   若替换的是占位实现，更新 `docs/development/text-metrics-registry-coverage.md`
   的对应状态行。

之后在质量契约构建页/Agent 推荐中即可选择该指标并配置阈值发起 Run；执行参数由
PhysicalPlan 的 `resolved_parameters` 固化（Metric 默认值 + Contract 配置合并），
重跑可证明配置一致。

## API 一览

| 路由                                                                                                                                                                                 | 说明                                                                    |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------- |
| `GET /api/v1/health`                                                                                                                                                               | 数据库和 ArtifactStore 健康检查                                         |
| `/api/v1/datasets`、`/api/v1/datasets/{id}/versions`、`/api/v1/dataset-versions/{id}/...`                                                                                      | 数据集/版本生命周期（上传、字段映射、发布、manifest、samples、profile） |
| `/api/v1/datasources/mysql/schema`、`/preview`                                                                                                                                   | 外部 MySQL 数据源探查                                                   |
| `/api/v1/metrics`、`/calculators`、`/metric-calculator-bindings`、`/registry/sync                                                                                              | readiness                                                               |
| `/api/v1/quality-contract-versions`                                                                                                                                                | 质量契约发布与查询                                                      |
| `/api/v1/evaluation-proposals/validate`                                                                                                                                            | 评测方案（Proposal）草稿校验                                            |
| `/api/v1/evaluation-runs`（`/{id}/metric-results`、`/stage-runs`、`/logical-plans/...`）                                                                                     | 创建（202 后台执行）/查询/删除 Run                                      |
| `/api/v1/quality-reports`、`/api/v1/evaluation-runs/{run_id}/quality-report`、`/finalize`、`/quality-issues`、`/rule-evaluations`、`/quality-report/operational-scoring` | 质量报告、finalize 补偿重建、规则判定、问题下钻、运营评分               |
| `/api/v1/datasets/{id}/document-versions`、`/api/v1/document-report-tasks`、`/api/v1/document-quality-reports/{id}`                                                            | 说明文档上传、异步评测任务、报告查询                                    |
| `/api/v1/agent-sessions`（`/messages`、`/proposal-revisions/...`、`/contract-confirmations`、`/evaluation-runs`、`/evaluation-runs/{run_id}/status`）                    | Agent 会话全生命周期                                                    |
| `/api/v1/local-validation/...`                                                                                                                                                     | 本地 artifact 产物的只读校验视图                                        |

完整定义见 `apps/api/routes/`（FastAPI 自动文档：`/docs`）。

## 测试

```bash
uv run pytest                          # 全量（unit + integration）
uv run pytest tests/unit               # 单元
QA_EVAL_DATABASE_URL=sqlite:////tmp/qa-eval-test.sqlite3 \
  uv run alembic upgrade head          # 独立 SQLite migration 验证
uv run python scripts/contracts/generate_json_schemas.py   # 重新生成 schemas/
```

## 更多文档

- 架构（canonical 数据流与模型）：`docs/architecture/canonical-data-flow-models-v1.md`
- ADR：`docs/adr/`
- 开发文档：`docs/development/`（Calculator 共享推理设计、KenLM 模型准备、
  文本指标覆盖情况、逻辑计划 MVP 等）
- Docker 停机与恢复 SOP：`docs/Docker停机与恢复SOP.md`
- 命令备忘：`docs/命令备忘.md`
