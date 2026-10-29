# MySQL jhsjk_article 数据集综合质量评测 Demo 开发技术方案

- 状态：P0 Demo 已实施并完成真实 Run 验证；Agent Runtime 生产化（P2）待实施
- 编制日期：2026-10-28（Asia/Shanghai）
- 依据：`plan/Idea-Pool/1029演示.md`、`docs/development/dataset-comprehensive-evaluation-plan.md` 与当前工作区源码
- 目标版本：单日可开发、联调、演示的最小可信闭环

## 1. 目标与交付范围

### 1.1 当日 Demo 效果

从已上传并发布的 `jhsjk_article` Dataset Version 进入“数据集综合质量评测”，系统以确定性规则生成结构化指标建议；用户调整并确认后发布一个多 item Quality Contract，提交真实后台 Evaluation Run，看到 Run ID，最终查看包含雷达图、指标状态、有效覆盖率、问题和整改建议的综合报告。

主链路如下：

```text
已发布 JSONL Dataset Version
  → 数据集版本内 Agent 综合评测工作台（对话 + Proposal 双栏）
  → Agent 加载 Data Quality Skill
  → Agent 通过 Tool Adapter 调用确定性推荐 API
  → Agent 返回解释 + 结构化 Proposal
  → 用户调整、校验、确认
  → 一个多 item Quality Contract
  → Evaluation Run / 多 StageRun
  → MetricResult / Rule / Issue / Report
  → 雷达图与规则模板整改建议
```

### 1.2 交付物

1. 可从前端完成的真实端到端 Demo，保留现有单指标入口。
2. 数据质量指南全维度覆盖视图；真实值与占位状态严格分开。
3. 本方案、可复现测试和 Demo 操作脚本。
4. 开发完成后用新 Run 的真实结果编写 `docs/cases/mysql-jhsjk-article-quality-evaluation-case.md`。

### 1.3 非目标

- 本轮跳过 MySQL 直连接入；按人工评审结论复用手动上传的 JSONL 与已发布 Dataset Version。
- 不实现多 Agent、通用工作流引擎、完整 DAG 或分布式调度。V1 实现一个有界主 Agent；LLM Provider 不可用时可降级为确定性 Agent policy，但调用链、会话、Skill 和 Tool 轨迹不得由前端模拟。
- 不在今天补齐所有复杂文本算法，不为占位指标制造数值或证据。
- 不把已评测子集分数宣传为指南全指标综合得分。
- 不在今天实现新的 Logical Plan。当前执行服务已有 Physical Plan；人工评审允许其不阻塞推荐闭环。

## 2. 当前系统能力审计

状态以当前源码为准，不把设计文档中的计划当作实现。

| 模块                           | 状态                                               | 代码/API/表                                                                                                           | 可复用能力                                                      | 缺口与今天动作                                                                                                           |
| ------------------------------ | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Dataset                        | 已实现且有测试                                     | `domain/entities/dataset.py`；`apps/api/routes/datasets.py`；`datasets`                                         | 创建、列表、详情、删除                                          | 无核心改动                                                                                                               |
| Dataset Version / Manifest     | 已实现且有测试                                     | `dataset_version_service.py`；`dataset_versions.py`；`dataset_versions`、`dataset_manifests`                  | JSONL 上传、字段映射、标准化、发布、样本读取                    | 工作台入口绑定已发布版本                                                                                                 |
| MySQL Datasource / Adapter     | 部分实现                                           | `infrastructure/mysql/`；`routes/mysql_datasource.py`                                                             | schema、preview、adapter 基础能力                               | 人工评审已允许跳过直连；不作为 Demo 阻塞项                                                                               |
| Field Mapping                  | 已实现                                             | `contracts/datasets.py`；`MysqlDatasourcePage.tsx`                                                                | JSONL 字段映射、NULL 保真                                       | 复用现有映射                                                                                                             |
| Dataset Profile                | 已实现                                             | `dataset_service.py`、profile repository；`GET .../profile`；`dataset_profiles`                                 | 记录数、角色/模态等画像和 fingerprint                           | 推荐服务读取最新 profile；不新增画像算法                                                                                 |
| Metric Definition              | 已实现，覆盖配置已扩展                             | `configs/registry/metrics/`；`MetricRegistryService`；`metric_definitions`                                      | 0201–0208、02080101–02080107 可注册展示                       | 必须区分“已注册”与“可执行”                                                                                           |
| Calculator Implementation      | 部分实现                                           | `calculators/protocol.py`、`loader.py`；`calculator_implementations`                                            | 受信任加载、参数 schema、code digest；0204、02080103 可执行     | 其余文本指标多为 unavailable descriptor，占位不得调度                                                                    |
| Binding / Applicability        | 已实现且验证通过                                   | `configs/registry/bindings/`；`ApplicabilityService`                                                              | 四态 `APPLICABLE/NOT_APPLICABLE/NEEDS_INPUT/UNAVAILABLE`      | 推荐层聚合现有决策，不复制判定逻辑                                                                                       |
| Evaluation Proposal            | 已实现并通过真实 Run 验证                          | `contracts/proposals.py`；`evaluation_recommendation_service.py`；`application/agent/tools/adapter.py`；`POST /agent-sessions/{id}/messages` | 确定性生成、四态覆盖、校验、ProposalRevision 持久化与恢复        | 生产化阶段补并发 revision/幂等约束与执行生命周期                                                        |
| Quality Contract               | 已实现且验证通过                                   | `contracts/quality.py`；`QualityContractService.publish_multi`；`POST .../multi`；`quality_contract_versions` | 一个 Contract 多 item、原子发布、单指标兼容                     | 工作台调用现有发布 API；不迁移数据库                                                                                     |
| Physical Plan / Run / StageRun | 多 Stage 顺序执行已实现                            | `contracts/execution.py`、`evaluation_run_service.py`；`POST /evaluation-runs`；三张执行表                      | 创建时真实生成 Physical Plan，线程池后台执行，逐指标 Stage 状态 | 今天只要求提交后返回 Run ID；不做 Logical Plan/依赖 DAG 增强                                                             |
| Metric Result                  | 已实现                                             | `metric_results`；`GET .../metric-results`                                                                        | 多结果、Evidence、错误状态                                      | 前端展示状态和真实观测                                                                                                   |
| Rule Evaluation                | 已实现                                             | `rule_evaluator.py`；`rule_evaluations`                                                                           | 阈值 PASS/FAIL/UNKNOWN/SKIPPED                                  | 不将执行 ERROR 等同规则 FAIL                                                                                             |
| Quality Issue / IssueIndex     | 部分实现                                           | `quality_result_service.py`；`quality_issues`、`issue_indexes`                                                  | 02080103 已可生成问题与样本下钻                                 | 其他 detector 有限，报告必须披露                                                                                         |
| Quality Report                 | 已实现 Report v1.1 并通过真实 Run 验证             | `report_generator.py`；`quality_reports`；`GET /evaluation-runs/{id}/quality-report`；`EvaluationResultsPage.tsx` | 多结果、规则、问题、指南覆盖、已评测得分、有效覆盖率、N/A 雷达图与建议 | 继续增加可执行指标覆盖；不得把当前 2 个指标宣传为指南全量评分                                           |
| Agent / Skill / Tool           | V1 已实现并通过真实 Run 验证                       | `application/agent/`；`a7d2c6f41b90_add_agent_sessions.py`；`POST /agent-sessions`、`/messages`、`/contract-confirmations`、`/evaluation-runs` | 可恢复会话、Skill/Tool 审计、确定性 fallback、人工写操作门禁、结果观察 | P2 补 Provider streaming、持久队列、租约、真正取消、完整预算/重试与多进程恢复                            |
| React 前端                     | 双栏 Agent 工作台和综合报告页已实现                | `AgentEvaluationWorkspace.tsx`；`EvaluationResultsPage.tsx`；`agentSessions.ts`                                   | 会话恢复、Proposal/覆盖矩阵、人工确认、Run 轮询通知、雷达图和证据下钻 | 本次实证通过同一 HTTP API 完成；浏览器实际点击流程仍需人工 UI 验收                                       |
| 后台任务                       | V1 已实现，仍为进程内线程池                        | `routes/evaluation_runs.py`                                                                                         | POST 202 后单 worker 异步执行                                   | Demo 已验证；生产化必须迁移到持久队列、租约和崩溃恢复                                                                    |
| Migration / 测试               | P0 已实现并完成 PostgreSQL 验证                    | Alembic 单一 head `e84d9b73a6f1`；`a7d2c6f41b90_add_agent_sessions.py`；`tests/integration/test_agent_session_api.py`；`test_comprehensive_evaluation_e2e.py` | 2026-10-29：完整 pytest 371 passed、6 个显式 MySQL integration skipped；schema drift、前端 lint/build 通过 | Ruff 尚有 22 项来自当前未提交的文本困惑度/纯度及 Skill 脚本改动，质量门未完全全绿                         |

结论（2026-10-29 更新）：P0 的真实 Agent Runtime/会话/Tool 调用层、多指标 Contract、后台 Run、Report v1.1 与前端工作台均已落地，并已用 `policy.jhsjk_article` 14,960 条固定快照完成一条全新真实 Run。当前可以进入 Agent Runtime 生产化开发；主要缺口已转为持久队列与租约、请求幂等/并发控制、真正的超时取消、完整预算/重试、streaming 与多进程恢复。全指南可执行覆盖仍有限，报告必须继续并列展示已评测得分和有效覆盖率。

## 3. jhsjk_article 数据准备情况

### 3.1 当前事实

- 人工评审确认已经支持手动上传对应 JSONL，Step 1 可跳过。
- 工作区存在上传产物：`data/artifacts/datasets/ba0a8b3a-4bdb-420e-8f41-1c0509c521fb/01a17c8d-b995-7a9c-801a-73e6551bc2ab/raw/jhsjk_article.jsonl`。
- 已有标准化实现 `application/normalization/jhsjk_article_normalizer.py`，开发 MySQL 初始化 schema 位于 `dev/mysql/init/01_schema.sql`。
- 闭环 2A 验收记录中的历史 Dataset Version 为 `01a17c8d-b995-7a9c-801a-73e6551bc2ab`，历史 Profile fingerprint 为 `sha256:8f17...24cf0`。
- 历史 Run `01a1805d-8c21-7fff-ac34-0254dcb9c885` 曾真实执行 0204 与 02080103，产生两个 OK MetricResult 和 45 条 Issue。

以上 UUID 与结果仅作为已有能力证据。Demo 必须从页面当前选中版本读取实际 ID，并创建新 Proposal、Contract 和 Run；不得复用历史结果冒充本次执行。

### 3.2 字段与映射

以当前 Dataset Version 已保存的 `FieldMappingSpecV2` 和 Manifest 为权威，不在方案中硬编码生产表结构。最低输入要求：

| 目标                                | 来源                  | 约束                                                  |
| ----------------------------------- | --------------------- | ----------------------------------------------------- |
| `sample_id`                       | JSONL 唯一 ID 字段    | `REJECT_MISSING`                                    |
| `article.body.plain`              | 文章纯文本字段        | 保留 null/空串差异                                    |
| 可选 title/summary/html/source/time | 已映射角色/attributes | 缺失时由 Applicability 返回 `NEEDS_INPUT`，不得猜测 |

页面进入综合评测前读取版本详情、Manifest 和 Profile，并展示实际记录数、字段角色、normalized artifact fingerprint。若版本非 `PUBLISHED`、无 normalized artifact 或 profile 不存在，则禁用提交并给出修复入口。

### 3.3 阻塞项

- 当前数据库中的对象可能随环境变化，不能仅凭仓库文件断言仍存在；演示前必须执行预检。
- 复杂指标缺少模型、业务声明、标签或外部证据，即使 descriptor 已注册也不能真实计算。
- 数据集类型（通识/行业通识/行业专识）与目标用途若未声明，0207 应为 `NEEDS_INPUT`，不是 FAIL。

## 4. 指南全维度指标覆盖矩阵

权威来源为 `plan/Idea-Pool/指标开发/文本指标清单.md` 和 Registry 配置。`已注册占位` 表示可展示但 Calculator 不可执行。

| 编号     | 指标         | 文本数据适用性                | 当前实现/策略                                                                            | Demo 状态                             |
| -------- | ------------ | ----------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------- |
| 0201     | 格式规范性   | 适用，需预定格式规则          | descriptor/binding 占位；可在 P1 增加 JSONL/schema 确定性实现                            | `UNAVAILABLE`；P1 可真实计算        |
| 0202     | 安全规范性   | 适用                          | 需要安全模型或规则+语义复核                                                              | `UNAVAILABLE`                       |
| 0203     | 标注规范性   | 取决于是否为有监督/含标注数据 | 当前文章数据若无 annotation，依据 Manifest 判 `NOT_APPLICABLE`                         | `NOT_APPLICABLE` 或 `NEEDS_INPUT` |
| 0204     | 结构完整性   | 适用                          | `structural_completeness` 已实现，统计必需角色/空值                                    | `SUCCESS/ERROR`，真实执行           |
| 0205     | 内容真实性   | 适用但需血缘/外部证据         | 仅占位，LLM 不能独立证明真实性                                                           | `UNAVAILABLE`/`NEEDS_INPUT`       |
| 0206     | 内容一致性   | 有关联字段时适用              | 需要字段关系、embedding 或语义判断                                                       | `UNAVAILABLE`/`NEEDS_INPUT`       |
| 0207     | 类型一致性   | 有数据集类型声明时适用        | 需要类型声明与分类器                                                                     | `NEEDS_INPUT` 或 `UNAVAILABLE`    |
| 0208     | 内容干净性   | 适用，为父级汇总              | 不独立伪造值；由子指标覆盖展示，评分时避免父子重复计权                                   | `NOT_EVALUATED`（派生展示）         |
| 02080101 | 文本困惑程度 | 适用                          | 需 KenLM/模型，当前占位                                                                  | `UNAVAILABLE`                       |
| 02080102 | 知识信息密度 | 适用                          | 缺少可操作 rubric/实现，当前占位                                                         | `UNAVAILABLE`                       |
| 02080103 | 重复内容程度 | 适用                          | `exact_text_duplicate` 与 `text_duplicate_degree` 已实现；Demo 推荐低成本 exact 实现 | `SUCCESS/ERROR`，真实执行           |
| 02080104 | 文本完整程度 | 适用                          | 长度/截断规则 descriptor 占位；P1 可补确定性基础规则                                     | `UNAVAILABLE`；P1 可真实计算        |
| 02080105 | 信息缺失程度 | title/段落结构映射齐全时适用  | 当前占位                                                                                 | `NEEDS_INPUT`/`UNAVAILABLE`       |
| 02080106 | 文本纯净程度 | 适用                          | 广告/乱码/特殊符号规则当前占位                                                           | `UNAVAILABLE`                       |
| 02080107 | 文本连贯程度 | 适用                          | 需语言模型或明确 rubric，当前占位                                                        | `UNAVAILABLE`                       |

说明：当前 MetricResult 状态 canonical 为 `OK/NOT_APPLICABLE/MISSING/ERROR`；`UNAVAILABLE/NEEDS_INPUT` 属于推荐/适用性阶段，不应强行创建不可执行 Contract item。报告的“全指南覆盖矩阵”从 Proposal validation 快照和 Registry 生成，未入执行计划者记 `NOT_EVALUATED` 并保留原因；不创建虚假 MetricResult。

## 5. 端到端业务闭环设计

### Step 1：数据准备（按人工评审跳过直连）

| 项目       | 内容                                                        |
| ---------- | ----------------------------------------------------------- |
| 用户操作   | 在数据集详情选择已上传、已发布的 `jhsjk_article` 版本     |
| 输入       | Dataset Version ID                                          |
| API        | `GET /dataset-versions/{id}`、`/manifest`、`/profile` |
| 后台处理   | 校验发布状态、normalized artifact、profile fingerprint      |
| 持久化对象 | 复用 Dataset、DatasetVersion、Manifest、Profile             |
| 前端反馈   | 版本、记录数、字段角色、画像、指纹；不显示 MySQL 接入向导   |
| 输出       | 可评测的固定版本上下文                                      |
| 异常处理   | 未发布/无画像时禁用入口，提示先完成发布                     |
| 验收标准   | 页面可核对版本和记录数，输入范围可复现                      |

### Step 2：进入综合评测

| 项目       | 内容                                                                               |
| ---------- | ---------------------------------------------------------------------------------- |
| 用户操作   | 点击“数据集综合质量评测”                                                         |
| 输入       | 当前 Dataset 与 Version                                                            |
| API        | `POST /api/v1/agent-sessions` 或恢复该版本未结束 Session；加载版本上下文         |
| 后台处理   | 创建/恢复绑定 `dataset_version_id` 的 Agent Session，注册可用 Skill 与 Tool      |
| 持久化对象 | AgentSession、ConversationMessage、ToolInvocation；未确认前不创建平台 Contract/Run |
| 前端反馈   | 进入双栏工作台：左侧 Agent 对话/Skill/Tool 进度，右侧 Proposal 与配置              |
| 输出       | `datasetVersionId` 绑定的 Agent 会话                                             |
| 异常处理   | 非发布版本阻止进入                                                                 |
| 验收标准   | 原单指标入口不变；综合入口不会丢失版本身份；刷新可恢复对话、Proposal 和 Tool 轨迹  |

### Step 3：Agent 对话驱动的确定性推荐

| 项目       | 内容                                                                                                                                                                                                 |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 用户操作   | 在 Agent 对话窗口输入“为当前数据集推荐综合质量评测方案”，或点击发送同义快捷指令                                                                                                                    |
| 输入       | 用户消息 + Agent Session 中绑定的 Version；Agent 再读取 Manifest、Profile、Registry                                                                                                                  |
| API        | 前端仅调用 `POST /api/v1/agent-sessions/{id}/messages`；Agent Tool Adapter 内部调用 `recommend_evaluation_proposal`（可映射到内部 `/evaluation-proposals/recommend`）和现有 `/validate` 能力 |
| 后台处理   | Agent 识别意图、加载 Data Quality Skill、调用 profile/registry/recommend/validate Tools；确定性推荐服务枚举指标并生成 Proposal/coverage；Agent 基于 Tool Result 生成解释，不自行编造候选             |
| 持久化对象 | AgentSession、消息、SkillInvocation、ToolInvocation、Proposal revision/快照                                                                                                                          |
| 前端反馈   | 左栏流式/轮询显示 Agent 回复和真实 Tool 状态；右栏同步显示指标、原因、实现、参数、可执行性、限制和成本                                                                                               |
| 输出       | AgentMessage + `EvaluationProposalV1` + validation + guide coverage 的结构化附件                                                                                                                   |
| 异常处理   | Tool 失败保留错误轨迹；stale profile、Registry 不一致时 Agent 解释原因并有界重试或请求用户操作                                                                                                       |
| 验收标准   | 前端不能直接调用推荐 API；Proposal 必须能追溯到 Agent Session、SkillInvocation 和成功的 ToolInvocation，并可转换为多 item Contract 请求                                                              |

### Step 4：用户确认并发布多指标 Contract

| 项目       | 内容                                                                               |
| ---------- | ---------------------------------------------------------------------------------- |
| 用户操作   | 调整指标/阈值，点击“校验”，明确确认后“发布契约”                                |
| 输入       | Proposal revision                                                                  |
| API        | `POST /evaluation-proposals/validate`；`POST /quality-contract-versions/multi` |
| 后台处理   | 全 item 原子校验并发布一个 Contract                                                |
| 持久化对象 | `quality_contract_versions.document`，含多个 item                                |
| 前端反馈   | 显示 Contract Version ID、版本、item 数；清除或标记草案已发布                      |
| 输出       | Published Quality Contract                                                         |
| 异常处理   | 任一 item 非 APPLICABLE/非法则整份不发布；展示 item finding                        |
| 验收标准   | 必须经过用户确认；不是多个单指标 Contract                                          |

人工评审所说“当前窗口文件系统内保存”调整为 Agent 会话作用域保存：后端持久化 Session、消息、Tool 轨迹和 Proposal revision，前端 `localStorage` 只保存 `agentSessionId` 与 `activeRunId` 作为恢复指针，不能作为 Proposal 或对话的唯一事实源。正式确认后的契约仍以数据库 `quality_contract_versions` 为唯一权威。

### Step 5：提交真实任务

| 项目       | 内容                                                            |
| ---------- | --------------------------------------------------------------- |
| 用户操作   | 点击“提交评测”                                                |
| 输入       | Contract Version ID                                             |
| API        | `POST /api/v1/evaluation-runs`                                |
| 后台处理   | 现有服务编译 Physical Plan，持久化 Run/Stage，再由线程池执行    |
| 持久化对象 | PhysicalEvaluationPlan、EvaluationRun、StageRun                 |
| 前端反馈   | 202 后立即显示 Run ID 和 PENDING/RUNNING；轮询 GET Run          |
| 输出       | 真实 Run ID                                                     |
| 异常处理   | Calculator digest/输入失败返回结构化错误，不模拟成功            |
| 验收标准   | 按人工评审，现阶段提交后展示 Run ID 即满足；Logical Plan 不阻塞 |

### Step 6：执行全维度覆盖中的可执行子集

| 项目       | 内容                                                                        |
| ---------- | --------------------------------------------------------------------------- |
| 用户操作   | 无，等待/查看进度                                                           |
| 输入       | Physical stages 与 normalized artifact                                      |
| API        | `GET /evaluation-runs/{id}`、`/stage-runs`、`/metric-results`         |
| 后台处理   | 顺序执行 0204、02080103；占位指标不进入执行计划                             |
| 持久化对象 | StageRun、MetricResult、Evidence                                            |
| 前端反馈   | 每指标阶段状态；覆盖矩阵同时显示未执行原因                                  |
| 输出       | 真实结果与证据，非执行项的推荐状态快照                                      |
| 异常处理   | 失败记 ERROR/FAILED；其他独立 stage 依现有顺序语义继续/终止，以代码行为展示 |
| 验收标准   | 至少两个真实指标结果；所有指南项均有明确展示状态                            |

### Step 7：综合报告与雷达图

| 项目       | 内容                                                                   |
| ---------- | ---------------------------------------------------------------------- |
| 用户操作   | Run 终态后打开报告                                                     |
| 输入       | Contract、Result、Rule、Issue、guide coverage snapshot                 |
| API        | 现有 quality-report/finalize；扩展报告字段或新增 presentation endpoint |
| 后台处理   | 计算有效覆盖率、已评测得分、维度展示值、建议；不重复计权 0208 父子     |
| 持久化对象 | QualityReport（建议扩展 schema v1.1 或独立 presentation DTO）          |
| 前端反馈   | 强制展示雷达图、分数口径、覆盖率、指标明细、问题、局限性               |
| 输出       | 可下钻综合报告                                                         |
| 异常处理   | 无可评分项时雷达轴为 N/A，不画 0 分；报告仍可生成                      |
| 验收标准   | 雷达图存在；真实/占位清晰；可下钻证据                                  |

### Step 8：结果通知与整改建议

| 项目       | 内容                                                                                                                     |
| ---------- | ------------------------------------------------------------------------------------------------------------------------ |
| 用户操作   | 无需再次提问；Agent 对话窗口自动追加完成消息，用户可点击报告卡片                                                         |
| 输入       | Run 终态、Rule、Issue                                                                                                    |
| API        | Agent Runtime 通过 `get_evaluation_status` / `get_quality_report` Tool 观察状态；前端可并行轮询现有 Run API 更新进度 |
| 后台处理   | Agent 状态观察器在 Run 终态后读取真实 Report，按 FAIL/ERROR/Issue code 生成优先级建议并写入会话消息                      |
| 持久化对象 | 报告建议字段 + Agent 会话消息/结果附件                                                                                   |
| 前端反馈   | Agent 对话消息与 Ant Design notification 同时给出完成/部分失败、薄弱项、报告入口                                         |
| 输出       | 结果摘要与整改建议                                                                                                       |
| 异常处理   | 页面刷新后从 localStorage 的 activeRunId 恢复轮询，或在结果列表查找 Run                                                  |
| 验收标准   | 用户不需重新发问；完成时 Agent 会话收到可追溯结果消息，UI 同步通知                                                       |

## 6. Agent 推荐与编排设计

### 6.1 Agent 工作台与调用边界

工作台采用 `agent设计.md` 规定的双栏结构：左侧是 Agent 对话、当前 Dataset Version、已加载 Skill 和 Tool 调用时间线；右侧是由 Agent Tool Result 驱动的 Evaluation Proposal、覆盖矩阵、Calculator/参数及确认操作。右栏不是独立向导，其数据源必须是当前 Agent Session 中的结构化 Proposal 附件。

```text
User message
  → Agent Runtime（会话、预算、人工确认状态）
  → Skill Registry.load("data-quality")
  → Tool Adapter.get_dataset_profile / discover_metrics
  → Tool Adapter.recommend_evaluation_proposal
  → Recommendation Service（确定性规则）
  → Tool Adapter.validate_evaluation_proposal
  → Agent explanation + Proposal attachment
  → Workbench right panel
```

边界要求：

- 浏览器只发送 Agent 消息和人工确认，不直接调用 recommend Tool API。
- Agent 负责选择 Skill、组织 Tool 调用、解释 findings；Recommendation Service 负责确定性候选选择。
- Agent 不直接查询数据库、不实现指标算法、不跳过 Proposal/Contract 校验。
- `create_quality_contract`、`create_evaluation_run` 是写入/执行 Tool，只有前端产生明确确认事件后才可调用。
- Tool 输入输出采用 Pydantic/JSON Schema，并记录 session、tool name、arguments digest、status、result refs、started/finished time。

### 6.2 V1 Runtime 方案

V1 采用一个主 Agent + Skill Registry + Python Service Tool Adapter，不引入多 Agent。LLM Provider 通过环境变量配置；若演示环境没有模型配置，运行同一 Agent 状态机的确定性 policy：根据用户的推荐/校验/确认/执行意图选择固定 Tool，但仍真实记录会话、Skill 与 Tool 轨迹。不得由前端生成伪 Agent 回复或伪 Tool 进度。

推荐算法仍选择“确定性规则推荐”，不实现轻量 Logical Planner。它符合既有 2A 规划：Agent 调用 Tool 得到 Proposal，Applicability/Binding 负责合法性，用户确认后由 Contract 固化意图，执行服务继续编译 Physical Plan。Logical Plan 不影响 Agent→Tool→Proposal 接口。

备选轻量 Planner 的优点是更早表达依赖，代价是今天需新增 canonical、持久化和迁移，且当前两个独立指标没有真实依赖，收益不足。

### 6.3 Agent Session 与最小持久化

最小模型：

| 对象                | 关键字段                                                                  | 用途                                |
| ------------------- | ------------------------------------------------------------------------- | ----------------------------------- |
| AgentSession        | id、dataset_version_id、status、active_skill、created/updated_at          | 绑定版本与会话生命周期              |
| ConversationMessage | session_id、role、content、attachments、created_at                        | 恢复真实对话及 Proposal/Report 卡片 |
| SkillInvocation     | session_id、skill_key/version、status、reason                             | 证明 Agent 加载了何种专业流程       |
| ToolInvocation      | session_id、tool_name、arguments_digest、status、result_refs、error       | 展示真实调用状态与审计              |
| ProposalRevision    | session_id、proposal JSON、validation JSON、profile fingerprint、revision | 右栏事实源与修改历史                |

这些是 Agent 运行层对象，不替代 canonical Proposal、Contract、Run 或 Report。可使用一个 migration 建表；若单日时间极紧，可先用工作区受控 JSON session store 实现接口，但必须是后端事实源、原子写入且仅用于 Demo，并在文档标明非生产方案。`localStorage` 只保存 session ID。

### 6.4 推荐规则

1. 读取所有数据质量 Metric，并按指南顺序输出覆盖项。
2. 对每个 Binding/Calculator 调用现有 `ApplicabilityService`。
3. 候选必须 Binding 合法且 Calculator `AVAILABLE`；否则只进入 coverage findings。
4. 排序：`APPLICABLE` > 其他；确定性 > 非确定性；成本 LOW > MEDIUM > HIGH；同级按 calculator key/version，保证可复现。
5. V1 每个 `metric_key@version` 最多选一个 Calculator，避免 Report 身份折叠。
6. 默认只把 `APPLICABLE` 项放入 Proposal；`NEEDS_INPUT/UNAVAILABLE/NOT_APPLICABLE` 放入覆盖矩阵，不发布成 Contract item。
7. 阈值来自显式 Demo policy 配置；没有权威阈值时标注“平台演示口径”，不得称为指南阈值。

Tool 内部确定性算法伪代码：

```python
for metric in registry.data_quality_metrics():
    decisions = [applicability.evaluate(context, metric, c, binding)
                 for c, binding in registry.candidates(metric)]
    executable = sorted(d for d in decisions if d.status == "APPLICABLE")
    if executable:
        proposal.items.append(to_item(metric, executable[0], demo_policy))
    coverage.append(to_coverage(metric, decisions))
validate(proposal)  # 必须复用现有校验服务
```

### 6.5 Agent 消息与 Tool 响应示例

前端发送：

```json
{
  "content": "请为当前 jhsjk_article 版本推荐数据质量综合评测方案",
  "client_request_id": "<uuid>"
}
```

Agent 返回的消息附件引用 Tool 结果，而不是在自然语言中复制一份不可校验的数据：

```json
{
  "message": {
    "role": "assistant",
    "content": "已加载数据质量 Skill，并根据当前画像推荐 2 个可执行指标；其余指标的缺失条件已列在右侧。",
    "attachments": [{"type": "EVALUATION_PROPOSAL", "proposal_id": "<uuid>", "revision": 1}]
  },
  "invocations": [
    {"tool": "get_dataset_profile", "status": "SUCCEEDED"},
    {"tool": "recommend_evaluation_proposal", "status": "SUCCEEDED"},
    {"tool": "validate_evaluation_proposal", "status": "SUCCEEDED"}
  ]
}
```

其中 `recommend_evaluation_proposal` Tool Result 的核心结构如下：

```json
{
  "proposal": {
    "schema_version": "1.0",
    "proposal_id": "<uuid7>",
    "revision": 1,
    "dataset_version_id": "<selected-version-id>",
    "profile_fingerprint": "sha256:<current-profile>",
    "created_by": "AGENT",
    "items": [
      {
        "key": "structural-completeness",
        "metric": {"key": "0204", "version": "1.0.0", "measurement": "complete_record_ratio"},
        "candidates": [{"key": "structural_completeness", "version": "1.0.0"}],
        "selected_calculator": {"key": "structural_completeness", "version": "1.0.0"},
        "parameters": {"required_content_roles": ["article.body.plain"]},
        "rule": {"operator": "GTE", "threshold": 0.95, "severity": "ERROR", "on_missing": "UNKNOWN"},
        "rationale": "画像包含 article.body.plain，且 Calculator 当前可加载",
        "limitations": ["0.95 为 Demo policy，不是指南规定阈值"]
      }
    ],
    "created_at": "2026-10-28T10:00:00+08:00"
  },
  "validation": {"valid": true, "items": []},
  "guide_coverage": [
    {"metric_key": "0202", "status": "UNAVAILABLE", "reason": "内容安全 Calculator 未部署", "cost": "HIGH"}
  ]
}
```

## 7. 多指标 Contract 与执行规划设计

### 7.1 Contract

现有 `QualityContractVersionV1.items` 已原生支持多指标，`publish_multi` 已原子发布，因此不需要 migration。单指标 API 继续包装为一个 item，不改行为。Proposal 到发布请求为纯映射：Metric/measurement、selected Calculator、parameters、rule 和 missing policy 均逐 item 保留。

关键不变量：

- 一个 Contract 绑定一个 Dataset Version。
- 一个 `metric_key@version` 在 V1 Contract 中最多出现一次。
- 只有 Proposal validation 为 valid 且选中项均 `APPLICABLE` 才允许确认发布。
- Proposal 是可变草案；Published Contract 不可变并是正式审计记录。

### 7.2 执行规划

今天复用 `EvaluationRunService.create` 的 Contract→Physical stages 编译，不新增 Logical Plan。每个 Contract item 对应一个 Physical Stage/StageRun，执行器真实加载 Registry descriptor 和代码 digest。前端以 Run 和 StageRun API 展示状态。

未来的 Logical Plan 可插入为：

```text
Published Contract → Logical Plan（意图/依赖/适用性快照）→ Physical Plan → Run
```

其引入不应改变 Recommendation/Proposal/Contract API。只有出现跨指标依赖、共享中间产物或复杂失败传播时再实施。

### 7.3 状态映射

| 层           | 状态                                 | 含义                       |
| ------------ | ------------------------------------ | -------------------------- |
| 推荐         | `APPLICABLE`                       | 可进入 Contract            |
| 推荐         | `NOT_APPLICABLE`                   | 对该版本不适用，不是质量差 |
| 推荐         | `NEEDS_INPUT`                      | 缺业务声明/字段/参数       |
| 推荐         | `UNAVAILABLE`                      | 定义存在但实现不可运行     |
| Stage        | `PENDING/RUNNING/SUCCEEDED/FAILED` | 实际执行状态               |
| MetricResult | `OK/NOT_APPLICABLE/MISSING/ERROR`  | 客观计算结果状态           |
| Rule         | `PASS/FAIL/UNKNOWN/SKIPPED`        | 契约判定，和执行状态分离   |

## 8. 综合评分与报告设计

### 8.1 防误导原则

指南清单未提供可直接使用的跨指标数值权重，不能声称“官方综合分”。报告同时展示：

1. `evaluated_score`：仅已成功且具有可归一化测量的叶子指标之平台演示得分。
2. `effective_coverage`：指南适用叶子指标中成功评测的比例。
3. 未完成数量及原因分布。
4. `score_scope` 明示被纳入的指标；0208 父指标和子指标不可重复计权。

### 8.2 公式

设指南叶子指标集合为 `L`；经适用性确定为适用或仍待输入的集合为 `A`；成功且可评分集合为 `E`。

```text
有效评测覆盖率 C = Σ(i∈E) w_i / Σ(i∈A) w_i
已评测维度得分 D_d = Σ(i∈E_d) w_i·s_i / Σ(i∈E_d) w_i
已评测综合得分 S = Σ(i∈E) w_i·s_i / Σ(i∈E) w_i
```

- 若指南存在明确权重则用指南权重；否则 V1 对同层叶子指标等权，并在 `scoring_policy` 声明“平台演示口径”。
- `NOT_APPLICABLE` 从分母排除；`NEEDS_INPUT/UNAVAILABLE/ERROR/MISSING/NOT_EVALUATED` 留在适用分母并降低覆盖率，但不按 0 分混入 `S`。
- 无 `E_d` 时维度值为 `null/N/A`，雷达图显示空缺样式，不画 0。
- 比例型“越高越好”指标 `s_i = clamp(100*x, 0, 100)`；“越低越好”指标 `s_i = clamp(100*(1-x), 0, 100)`。其他值只有 Registry 明确 normalization 时才可评分。
- 雷达图轴采用指南维度 0201–0208。0208 由其可评分子指标聚合；若 0208 有独立结果则只能二选一，禁止父子同时加权。

报告标题必须写“已评测指标得分”，旁边同时展示覆盖率；只有 `C=100%` 且全部适用指标成功时，才可简化为“综合质量得分”。

### 8.3 报告结构

建议以 versioned `QualityReportV1.1` 扩展，或先新增不落库的 `ComprehensiveReportView` DTO；若落库则必须同步 canonical schema，不在前端临时计算权威值。

```json
{
  "scoring": {
    "evaluated_score": 92.4,
    "effective_coverage": 0.1429,
    "policy": "GUIDE_WEIGHT_OR_EQUAL_LEAF_FALLBACK_V1",
    "included_metrics": ["0204", "02080103"],
    "disclaimer": "仅代表已评测指标，不代表指南全指标综合质量"
  },
  "radar": [
    {"dimension": "0204", "score": 98.0, "coverage": 1.0},
    {"dimension": "0208", "score": 86.8, "coverage": 0.2}
  ],
  "guide_coverage": [],
  "recommendations": []
}
```

整改建议采用确定性模板：Rule FAIL 优先于 ERROR，ERROR 优先于 UNAVAILABLE；再按 severity 和 Issue 数排序。例如 02080103 FAIL 且有 confirmed duplicate groups，则建议“先按 evidence group 审核并去除冗余副本”；没有证据时不得给出具体样本断言。

## 9. 今天的最小开发任务拆解

| 优先级 | 开发任务                                           | 修改文件                                                                                                                   | API/数据模型                                                        | 预计耗时   | 依赖                    | 验收方式                                                     |
| ------ | -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- | ---------- | ----------------------- | ------------------------------------------------------------ |
| P0     | Agent Session/Message/Invocation 最小模型          | 新增 `application/agent/`、ORM/repository、migration、API route                                                          | Session、Message、SkillInvocation、ToolInvocation、ProposalRevision | 1.5h       | 数据库                  | 刷新后按 session ID 恢复对话、Proposal 和 Tool 轨迹          |
| P0     | 单主 Agent Runtime、Skill Registry 与 Tool Adapter | 新增 `application/agent/runtime.py`、`skill_registry.py`、`tools/`                                                   | `POST /agent-sessions`、`POST /agent-sessions/{id}/messages`    | 2h         | 上项、现有 Service      | 用户消息触发真实 Skill/Tool 调用；调用有超时、次数限制与日志 |
| P0     | 确定性推荐 Tool 与响应 DTO                         | 新增 `recommendation_service.py`，扩展 `contracts/proposals.py`、Tool Adapter                                          | 内部 `recommend_evaluation_proposal` Tool；不作为前端直调接口     | 1.25h      | Registry、Applicability | 0204/02080103 可执行，其余有结构化原因；Tool Result 确定性   |
| P0     | Dataset Version 双栏 Agent 工作台                  | 新增 `AgentEvaluationWorkspace.tsx`、对话/ToolTimeline/Proposal components；修改 `App.tsx`、`DatasetVersionPage.tsx` | Agent Session/Message API                                           | 2h         | Runtime                 | 左栏真实对话/调用状态，右栏同步 Proposal；刷新恢复           |
| P0     | 人工确认与多 item Contract Tool                    | Agent confirmation handler、复用 quality contract service                                                                  | `create_quality_contract` Tool → 现有 `/multi` 能力            | 0.75h      | 工作台                  | 未确认 Agent 不得调用写 Tool；一次生成一个多 item Contract   |
| P0     | 提交 Run Tool、轮询与 Agent 完成消息               | Runtime/Tool Adapter、工作台、evaluationRuns client                                                                        | `create_evaluation_run`、status/report Tools                      | 0.75h      | Contract                | 立即显示真实 Run ID，终态写回 Agent 消息并通知               |
| P0     | 综合报告评分/覆盖 DTO                              | `report_generator.py`、`quality.py`、schema generator、API/client                                                      | Report v1.1 或 presentation DTO                                     | 1.5h       | 真实结果                | 分数、覆盖率与状态规则单测通过，无可评分项为 N/A             |
| P0     | 雷达图与建议区                                     | `EvaluationResultsPage.tsx`、样式；优先纯 SVG/现有依赖                                                                   | 无或报告扩展                                                        | 1h         | 报告 DTO                | 本次一定展示雷达图；N/A 不冒充 0                             |
| P0     | E2E 与回归                                         | `tests/integration/`、`tests/unit/`、前端测试                                                                          | 全链路                                                              | 1h         | 全部 P0                 | 上传版本→推荐→确认→Run→报告；原单指标测试不退化          |
| P1     | 0201 基础格式规范性 Calculator                     | calculator、descriptor、binding、tests                                                                                     | Registry                                                            | 1–1.5h    | P0 稳定                 | 真实 JSONL/schema 规则和 Evidence                            |
| P1     | 02080104 基础文本完整性 Calculator                 | calculator、descriptor、binding、tests                                                                                     | Registry                                                            | 1–1.5h    | 明确长度/截断 policy    | 不冒充语义完整性，名称披露“基础规则实现”                   |
| P2     | Agent Runtime 生产化                               | Provider streaming、队列、租约、完整预算/重试策略                                                                          | Agent execution lifecycle                                           | 2–3d      | V1 轨迹模型             | 多进程恢复、取消与重试可靠                                   |
| P2     | Logical Plan 与 Stage DAG                          | 按原综合规划实施                                                                                                           | 新 canonical/表/迁移                                                | 2–3d      | 真实依赖用例            | 拓扑、失败传播、身份测试                                     |
| P2     | LLM/模型指标                                       | 独立 Calculator 插件                                                                                                       | Registry + 模型运行时                                               | 按指标评估 | 模型、rubric、校准集    | 精度/成本/稳定性验证                                         |

### 9.1 实施状态审计（2026-10-29）

| 原任务 | 状态 | 实现与验证证据 |
| ------ | ---- | ------------ |
| Agent Session/Message/Invocation 最小模型 | 已完成 | `src/qa_eval/application/agent/`、`src/qa_eval/infrastructure/db/models/agent.py`、migration `a7d2c6f41b90`、`tests/integration/test_agent_session_api.py` |
| 单主 Agent Runtime、Skill Registry 与 Tool Adapter | 已完成（V1） | `runtime.py`、`skill_registry.py`、`tools/adapter.py`；真实 Session 保留 Skill/Tool invocation 与 arguments digest |
| 确定性推荐 Tool 与响应 DTO | 已完成 | `evaluation_recommendation_service.py`、Proposal v1、Applicability/Binding 复用；真实 Proposal 仅选择 0204、02080103 |
| Dataset Version 双栏 Agent 工作台 | 已完成代码实现 | `AgentEvaluationWorkspace.tsx`；会话恢复、Proposal/覆盖矩阵、Tool timeline 已实现；本次案例仍缺浏览器实际点击验收 |
| 人工确认与多 item Contract Tool | 已完成 | `POST /agent-sessions/{id}/contract-confirmations`；本次 Contract Version `01a1852e-251f-72fe-8f28-504307408481` 含 2 items |
| 提交 Run Tool、轮询与 Agent 完成消息 | 已完成 | `POST /agent-sessions/{id}/evaluation-runs` 与 status Tool；本次 Run `01a1852e-2546-73ec-8ea9-b7e6b63ca491` 成功并写回完成消息 |
| 综合报告评分/覆盖 DTO | 已完成 | QualityReport 1.1；本次已评测得分 99.59495、有效覆盖率 14.2857%，带强制免责声明 |
| 雷达图与建议区 | 已完成 | `EvaluationResultsPage.tsx`；0201–0208 轴，未评测轴为 N/A，0208 只聚合叶子结果 |
| E2E 与回归 | 部分完成 | 完整 pytest 371 passed、6 个需显式 MySQL 环境的测试 skipped；schema drift、前端 lint/build 通过；Ruff 受工作区既有未提交改动的 22 项问题阻塞 |
| 0201 / 02080104 P1 Calculator | 非本案例前置，未纳入本次 Contract | 继续保持 NOT_EVALUATED，不创建伪 MetricResult |
| Agent Runtime 生产化 | 待实施 | Provider streaming、持久队列、租约、幂等与并发控制、真正取消、完整预算/重试、多进程恢复 |
| Logical Plan 与 Stage DAG | 已有最小 Logical Plan，完整 DAG 仍待增强 | 本次 Run 已生成 Logical/Physical Plan；依赖拓扑与失败传播仍按后续 P2 处理 |

关键路径：预检 → Agent Session/Runtime → Skill/Tool Adapter → 推荐 Tool → 双栏工作台 → 用户确认/发布 Tool → Run Tool/轮询 → 报告评分 DTO → 雷达图/Agent 结果消息 → E2E。可并行的是 Agent 后端、前端双栏框架、报告公式与测试夹具；Contract 和 Run 内核无需重写。

时间不足时依次降级：不用 LLM，改由确定性 Agent policy 选择 Tool；不做 P1 新算法；雷达图用轻量 SVG；建议只用固定模板。不得降级 Agent 对话窗口、Agent→Skill→Tool 的真实调用链、会话/Tool 轨迹、用户确认、真实后台执行、结果持久化、报告展示和雷达图。

## 10. 测试与验收计划

| 场景            | 测试重点                                      | 预期                                                      |
| --------------- | --------------------------------------------- | --------------------------------------------------------- |
| Agent 会话      | Session 绑定版本、消息恢复、并发 revision     | 刷新可恢复；不同版本/Session 不串数据                     |
| Skill/Tool      | Data Quality Skill 发现与加载；Tool allowlist | 推荐前有 SkillInvocation；未声明 Tool 不可调用            |
| 调用边界        | 浏览器网络请求与后端审计                      | 前端不直调 recommend；每份 Proposal 有成功 ToolInvocation |
| 数据集准备      | JSONL 上传、映射、发布、profile               | 固定版本可复现；NULL/空串不混淆                           |
| 推荐            | 同一版本/Registry 输出稳定；四态完整          | 0204/02080103 入 Proposal，其他项有原因                   |
| 多指标 Contract | 原子发布、重复 metric 拒绝                    | 一个 Contract 多 items；失败不残留半成品                  |
| 用户确认        | 未勾选确认禁止发布                            | 明确人为门禁                                              |
| stale proposal  | profile fingerprint 改变                      | validation 拒绝并要求重新推荐                             |
| 任务提交        | POST 202 与真实 dispatcher                    | 返回 Run ID；DB 有 Plan/Run/Stage                         |
| Calculator      | 0204、02080103 真实 artifact 输入             | OK 结果含 numerator/denominator/Evidence                  |
| 占位状态        | unavailable/needs input/not applicable        | 不创建伪 Result；覆盖矩阵原因正确                         |
| 执行失败        | digest、参数、artifact 错误                   | ERROR/FAILED，不转换为质量 FAIL                           |
| 评分            | N/A、方向归一化、父子去重、覆盖率             | 公式单测；分数免责声明存在                                |
| 报告            | 多 Result、Rule、Issue、coverage              | 指标下钻与证据一致；雷达图轴正确                          |
| Agent 反馈      | Tool 失败、可修复/不可修复错误、调用上限      | 有界重试或请求用户；不静默改标准                          |
| 通知            | PENDING→RUNNING→终态轮询                    | Agent 自动追加结果消息并通知；刷新可恢复 activeRunId      |
| 前端 E2E        | 从 Version 到报告                             | 不经过指标库单指标入口也能闭环                            |
| 历史追溯        | 新旧 Run 并存                                 | 新报告引用新 Contract/Run；历史 ID 不混用                 |
| 单指标回归      | 原入口/API                                    | 行为保持不变                                              |

执行质量门：后端完整 pytest、Ruff、canonical schema drift、前端 lint/build；MySQL integration 仍需显式环境变量，因本 Demo 走 JSONL 不作为阻塞。人工验收还要核对页面显示的 Dataset Version、Contract Version、Run ID 和报告引用完全一致。

## 11. Demo 演示脚本

1. 打开“数据集管理”，选择 `policy.jhsjk_article`。
2. 选择已发布版本，核对版本号、记录数、字段角色、Profile 和 normalized artifact。
3. 点击“数据集综合质量评测”。页面恢复或新建该版本 Agent Session，进入双栏工作台；左侧显示对话，右侧显示 Proposal 区。
4. 在左侧输入“请推荐综合质量评测方案”并发送。Agent 依次显示“加载 Data Quality Skill → 读取 Profile/Registry → 调用推荐 Tool → 校验 Proposal”的真实状态；完成后回复推荐摘要，右侧同步展示指南全维度：0204、02080103 为可执行，其他项显示不适用、待输入或实现不可用及原因。
5. 展开两个可执行项，查看 Calculator、参数、Demo 阈值、成本和局限性；可调整阈值。
6. 点击“校验方案”，确认 profile fingerprint 与全部选中项合法。
7. 勾选确认声明并向 Agent 提交确认事件。Agent 才能调用 `create_quality_contract` Tool；页面显示一个 Contract Version ID 与两个 items。
8. 点击“提交评测”并确认。Agent 调用 `create_evaluation_run` Tool；后端创建真实 Physical Plan/Run/Stage，页面立刻显示新 Run ID 并开始轮询。
9. 任务完成时 Agent 在左侧自动追加结果卡片，同时页面通知成功/失败摘要和“查看报告”。
10. 打开报告：先看“已评测指标得分 + 有效覆盖率 + 免责声明”，再看 0201–0208 雷达图；不可评测轴显示 N/A。
11. 下钻 0204 和 02080103 的真实观测、Rule、Evidence 和 Issue；打开重复样本原文。
12. 查看按证据生成的整改建议与未评测指标原因。最后展示结果列表中本次新 Run 与历史 Run 可区分。

## 12. 最终案例文档结构

开发和真实新 Run 完成后创建 `docs/cases/mysql-jhsjk-article-quality-evaluation-case.md`：

1. 案例背景与目标
2. 数据集概况（Dataset Version、数据范围、fingerprint）
3. 评测依据与指南指标体系
4. 平台评测流程与可追溯链路
5. 确定性 Agent 推荐、人工调整与契约确认
6. Contract、Run、Calculator 与真实指标结果
7. 已评测得分、有效覆盖率与雷达图分析
8. 主要质量问题与证据样本
9. 数据整改建议及优先级
10. 未评测指标、当前能力与局限性
11. 可复现命令、测试结果与案例总结

案例只能引用本次新 Run 的真实产物；未运行前保留模板，不预填分数、问题数量或结论。

## 13. 关键技术决策汇总

| 问题                       | 推荐方案                                                               | 理由                                                            | 备选与风险                                                |
| -------------------------- | ---------------------------------------------------------------------- | --------------------------------------------------------------- | --------------------------------------------------------- |
| 多指标 Contract 是否改模型 | 不改；复用 `items` 与 `/multi`                                     | 已实现且双指标验收通过                                          | 新明细表会重复 canonical、增加迁移风险                    |
| 是否现在实现 Logical Plan  | 否                                                                     | 人工评审允许跳过，推荐与其无依赖，现有 Physical Plan 已真实执行 | 现在做会挤占综合 UI/报告时间；后续按原规划补              |
| Agent 最小实现             | 单主 Agent Runtime + Data Quality Skill + Tool Adapter；推荐算法确定性 | 满足真实对话/Tool 调用要求，同时保持结果可测试、可解释          | 无模型时用确定性 Agent policy；不能退化为前端直调推荐 API |
| 推荐 API 调用者            | 仅 Agent Tool Adapter                                                  | 推荐是 Agent 编排行为，平台 Service 只提供确定性能力            | 暴露给前端会绕过 Skill、轨迹和对话语义                    |
| 草案保存                   | 后端 Agent Session/ProposalRevision；localStorage 只存恢复指针         | 对话与 Tool 轨迹是事实，需刷新可恢复和审计                      | 仅 localStorage 会丢失后端调用链且不可审计                |
| 占位指标处理               | 覆盖矩阵展示，不进入 Contract，不造 MetricResult                       | 保持事实/状态语义                                               | 为占位造 Result 会污染执行事实                            |
| 综合评分                   | 已评测得分 + 有效覆盖率并列                                            | 避免把子集均值冒充全指标结论                                    | 单一总分最易误导                                          |
| 雷达图                     | 0201–0208 轴，空值为 N/A，0208 聚合叶子                               | 满足人工评审且避免 0 分误义/父子重复                            | 只画已评测轴会隐藏覆盖缺口                                |
| 完成推送                   | 前端轮询 + Agent 结果消息 + notification                               | 基于现有 Run API 单日可靠，并保持对话闭环                       | SSE/WebSocket 后续再做                                    |

真正闭环的不可删减条件是：固定 Dataset Version、Agent 对话窗口、可恢复会话、Agent→Skill→Tool 的真实推荐调用链与轨迹、结构化 Proposal、用户确认、一个多指标 Contract、真实 Run ID 与后台执行、结果持久化、状态诚实的全指南覆盖、真实报告与雷达图、可追溯证据。Logical Plan、更多算法、LLM 推理和实时推送不是本日闭环的前置条件。
