# 说明文档质量评测报告生成 — 架构评估与开发计划

- 状态：Accepted（P0 已落地：[ADR-0003](../adr/0003-document-evaluation-builtin-pipeline-and-report-kind.md) 完成对 ADR-0001 的窄幅修订，rubric 已冻结至 `configs/document-evaluation/rubric/1.0.0.json`；开发按 §11 分阶段进行）
- 日期：2026-10-28
- 关联需求：`plan/Idea-Pool/数据集管理模块/新增数据集说明文档版本管理与质量评测能力.md`
- 关联决策：[ADR-0001](../adr/0001-skill-metric-ownership-and-shared-evaluation-layers.md)、[ADR-0003](../adr/0003-document-evaluation-builtin-pipeline-and-report-kind.md)

---

## 0. 关键决策摘要

| # | 决策 | 内容 |
|---|---|---|
| D1 | 技术路线 | 采用**方案 C′（混合）**：独立文档评测服务，复用 Skill 的 rubric 与确定性校验逻辑，迁移为平台内置流水线；**不内嵌 agent 运行时，不直接调用 Skill** |
| D2 | 架构对齐 | 不走 Contract/Plan 链路，但**结果格式对齐 `MetricResultV1` 语义**（指标编码、状态、证据结构一致）；以新 ADR 修订 ADR-0001，未来综合评测时并入共享 `metric_results` |
| D3 | 综合评分 | 指南**未定义** 0101–0104 跨指标权重。MVP 总分 = 四个指标得分的**等权平均，程序计算**；任一指标 `ERROR` 时总分为 `null` 并说明原因；报告内显式记录 `scoring_policy`，不冒充指南规定 |
| D4 | 输入范围 | MVP 仅支持 **Markdown**（`.md`、UTF-8、≤ 2MB）；PDF/HTML/DOCX、扫描件一律在上传时拒绝，不进 MVP |
| D5 | 执行机制 | 独立线程池执行器（与数据质量评测的 `ThreadPoolExecutor(max_workers=1)` 隔离）；任务状态与逐指标进度落库；进程启动时清扫孤儿 `RUNNING` 任务 |
| D6 | LLM 判定粒度 | **每指标一次 LLM 调用（共 4 次）**，一次返回该指标 5 个检查项的结构化判定；程序侧校验证据行号、摘录、状态枚举与算术，校验失败带错误反馈重试（至多 2 次），仍失败该指标记 `ERROR` 而非 0 分 |
| D7 | 评测标准版本 | rubric 从 Skill 迁出，冻结为 `configs/document-evaluation/rubric/1.0.0.json`；报告记录 `evaluation_standard.version` 与内容 sha256，与 `evaluator_version` 分开 |

---

## 1. 现有 Skill 的真实能力与不足

审查对象：`.agents/skills/dataset-document-evaluator/`（SKILL.md、rubric、output-schema、`inspect_markdown.py`、`validate_result.py`）。

### 1.1 真实能力

| 能力 | 说明 | 平台可复用性 |
|---|---|---|
| 20 检查项 rubric（0101–0104 × 5） | 每项有可执行的 `SATISFIED` 最低标准与反例 | **高**，直接冻结为评测标准文件（D7） |
| 五态状态模型 | `SATISFIED / INSUFFICIENT / MISSING / UNVERIFIABLE / ERROR`，异常与不合格严格分离 | **高**，原样采纳为报告状态模型 |
| 文档检查脚本 | `inspect_markdown.py`：行号编号、行哈希、标题结构、行数 | **高**，解析逻辑可移植 |
| 确定性校验脚本 | `validate_result.py`（300 行）：校验算术（score = A/5×100）、证据行号范围、摘录逐字命中、状态枚举、汇总一致性 | **高**，作为 LLM 输出后校验器移植 |
| 防幻觉边界 | 摘录必须逐字出现在引用行内；`MISSING` 必须给出 inspected scope；禁止编造许可/联系人/事实 | **高**，转化为程序校验规则 |
| 修复分级 | `AUTO_FIXABLE / DATA_DERIVABLE / REQUIRES_HUMAN_INPUT` | 高，采纳进建议字段 |
| 输出 Schema 1.0 | canonical JSON + 确定性渲染 Markdown | 中，字段结构参考，但需映射平台模型（§6.4） |

### 1.2 不足与平台化差距

1. **它不是服务，是 agent 运行时的操作说明**：语义判定依赖执行 SKILL.md 的 LLM agent（Claude Code / OpenAI agent），平台内没有可调用入口；"直接调用 Skill"（方案 A）意味着平台嵌入整套 agent 运行时。
2. **输入仅限本地 Markdown 文件路径**：无上传、无版本管理、无 URL 获取能力（其开发方案已把"平台上传 URL"列为后续范围）。
3. **私有输出格式**：`run_id / metric_code / score` 等字段与平台 `MetricResultV1` 尚未映射，正是 ADR-0001:105 指出"必须映射或迁移"的对象。
4. **无总分、无权重**：Skill 明确不产出综合分（`SKILL.md:29`、开发方案 :407），本需求的"综合评分"必须由平台另行定义（D3）。
5. **无持久化与任务管理**：无状态机、无失败重试、无历史报告，全部由 agent 会话承担。
6. **证据行校验对 LLM 不可靠输出无兜底**：校验失败在 Skill 流程里等于人工重跑；平台化后需要自动重试与降级语义（D6）。

---

## 2. 指南指标可执行性分析

指南（经 Skill rubric 转述，v1.0）对说明文档维度只定义 4 个指标、20 个检查项，计算式统一为：

```text
score(01xx) = SATISFIED 检查项数 / 5 × 100 ∈ {0, 20, 40, 60, 80, 100}
INSUFFICIENT / MISSING / UNVERIFIABLE 不计入分子；ERROR 该指标分数为 null
```

### 2.1 可执行性结论

- **20 项全部需要 LLM 语义判定**。rubric 明确"标题/关键词命中不等于满足"、"提到 ≠ 满足"，不存在可关键词化的确定性判定项；文档级 `MISSING` 也需要全文语义排查后才能下结论。
- **MVP 采用 document-only 模式**：只依据文档本身判定，无法核实的事实性声明判 `UNVERIFIABLE`，不做外部证据比对（evidence-assisted 模式连同 dataset profile 比对推迟到 Phase 4）。
- **确定性部分全部可程序化**：行号/行哈希/行数/标题结构（解析器）、证据行号范围与摘录逐字校验、状态枚举校验、算术复算、等权总分（校验器与汇总器）。LLM 只负责语义判定与建议文本。

### 2.2 逐项方法标注（摘要）

| 指标 | 检查项 | 判定 | 程序辅助 |
|---|---|---|---|
| 0101 基本信息完整性 | 规模 / 格式规范 / 文件结构 / 访问渠道 / 技术支持 | LLM | 证据行校验；空文档/超长拒绝在上传层 |
| 0102 内容特征完整性 | 模态 / 数据分布 / 标签统计 / 样例 / 局限性 | LLM | 同上；标签统计的"无标签需量化"规则写入 prompt |
| 0103 建设过程完整性 | 来源 / 采集 / 加工 / 标注规范 / 版本记录 | LLM | 同上 |
| 0104 应用说明完整性 | 许可 / 场景 / 评估方法 / 基准结果 / 应用案例 | LLM | 同上；"内部使用需含部门与范围"等反例规则写入 prompt |

无"确定性规则可直接评分"的指标项；混合点体现在**判定（LLM）与校验/计分（程序）的分离**，而非逐项区分。

---

## 3. 技术路线比较与推荐

| 维度 | A：直接调用 Skill | B：完全独立文档评测服务 | **C′：混合（推荐）** |
|---|---|---|---|
| 开发成本 | 表面低，实际需引入 agent 运行时 SDK | 高（rubric、校验全部重写） | 中（rubric/校验迁移而非重写） |
| 代码复用 | 复用 Skill 文本，不复用代码 | 低 | **高**：rubric、五态模型、`validate_result.py` 逻辑、`inspect_markdown.py` 逻辑 |
| 一键生成报告 | 需封装 agent 会话，状态不可控 | 满足 | 满足 |
| Agent 运行环境 | **必需（最大风险）** | 不需要 | 不需要（直接 LLM API 调用） |
| LLM 集成难度 | 隐含在 agent SDK 里，调试困难 | 需自建 | 需自建，但接口面最小（4 次结构化调用/任务） |
| 可解释性 | agent 过程不可复现 | 取决于实现 | **逐项证据 + 程序复算计分**，最强 |
| 版本可追溯 | Skill 版本 ≠ 标准版本 ≠ 评测器版本，纠缠 | 可拆分 | 可拆分（D7 三版本冻结进报告） |
| 测试难度 | 无法离线测试 | 高 | 中：LLM mock + golden fixtures + 校验器纯单测 |
| 长期维护 | 依赖 agent 工具链演进 | 双轨维护风险 | 单轨：Skill 转为 rubric 上游来源 |
| 与平台耦合 | 低但形成第二套运行时 | 过低，违反 ADR-0001 核心意图 | **适中**：复用 ArtifactStore/uuid7/报告语义，不复用 Contract/Plan |

**推荐 C′ 的核心理由**：Skill 的价值集中在 rubric 与确定性校验（可直接迁移），而其执行主体（agent 运行时）是平台最大的新增依赖且不可控。C′ 把"评测标准、校验逻辑"与"执行环境"解耦，用最小 LLM 接口面换取可控、可测、可审计的流水线。

---

## 4. 推荐的完整报告生成流程

```text
[前端] 说明文档版本 Tab
   │ 1. 上传 .md ──→ 校验（格式/UTF-8/≤2MB/非空）──→ ArtifactStore 存原始文件
   │                  └→ 解析产物（行号化 JSON + sha256）存为解析 artifact
   │ 2. 点击「生成质量评测报告」
   ▼
POST /document-versions/{id}/report-tasks（202）
   │ 冻结配置快照：rubric 版本+sha256、evaluator 版本、模型、prompt 版本、参数
   ▼
DocumentReportTask（PENDING → RUNNING）──独立线程池执行──
   │ 3. 逐指标（0101→0104，串行）：
   │     a. 组装 prompt（rubric 该指标 5 项 + 全文行号化内容 + 输出 Schema）
   │     b. LLM 结构化输出 → pydantic 解析
   │     c. 程序校验：证据行号范围、摘录逐字命中（行尾归一化后）、状态枚举、
   │        fixability 枚举、satisfied_count 复算
   │     d. 校验失败 → 携错误反馈重试（≤2 次）→ 仍失败：该指标 ERROR（error 原因落库）
   │     e. 指标通过 → 任务文档落库阶段进度（含耗时、调用统计）
   ▼
   │ 4. 程序汇总：score = A/5×100（复算）；总分 = 四指标等权平均（任一 ERROR → null）
   │ 5. 组装 DocumentQualityReportV1（§6.3），明细 items 同时存为 METRIC_EVIDENCE artifact
   │ 6. 持久化报告 → Task SUCCEEDED（含 report_id）
   ▼
[前端] 轮询任务状态 → 查看报告（4 指标分 / 20 项判定 / 证据行号与摘录 /
        缺失清单 / 建议 / 总分与计算口径 / 失败原因）
```

---

## 5. 规则、LLM、混合评测的具体分工

| 环节 | 承担者 | 说明 |
|---|---|---|
| 上传校验 | 程序 | 扩展名 `.md`、UTF-8 可解码、非空、≤2MB；违规直接 422，不产生任务 |
| 文档解析 | 程序 | 行号化、行数、标题结构、sha256；解析产物持久化并记录解析器版本 |
| 检查项状态判定（20 项） | **LLM** | 唯一的语义环节；prompt 内置 rubric 最低标准与反例 |
| 评分依据（reason）与建议（suggestion） | **LLM** | 每项必填；`SATISFIED` 项建议可为"无需修改" |
| 证据定位 | **LLM 产出 + 程序校验** | LLM 给行号区间与摘录；校验器逐字核对，不通过即重试 |
| `MISSING` 判定 | LLM + 程序约束 | 必须给出 inspected scope 与建议插入位置，否则校验失败 |
| 指标分、总分 | **程序** | 一律复算，绝不采信 LLM 给出的任何分数 |
| 防幻觉 | 程序 | 摘录逐字校验；状态/枚举校验；外部证据字段 MVP 恒为空 |
| 失败语义 | 程序 | 解析失败/LLM 重试耗尽 → 指标 `ERROR`，分数 `null`，**不计 0 分** |

---

## 6. 数据模型

### 6.1 DocumentVersion（领域实体 + ORM）

归属 Dataset，版本序列独立于 DatasetVersion；**上传后不可变**，无 DRAFT/PUBLISHED 生命周期。

```python
@dataclass(frozen=True)
class DocumentVersion:
    dataset_id: UUID
    version: str            # 上传时自动分配 v1、v2…，不可自定义（MVP）
    filename: str
    format: str             # 恒为 "markdown"（MVP）
    size_bytes: int
    sha256: str             # 原始文件哈希，报告可复现性的锚点
    storage_uri: str        # ArtifactStore URI（原始文件）
    parsed_artifact_uri: str | None  # 行号化解析产物（首次生成任务时产出）
    id: UUID
    created_at: datetime
```

表 `document_versions`：`id PK`、`dataset_id FK`、`version`、`sha256`、`size_bytes`、`storage_uri`、`document JSONB`，唯一约束 `(dataset_id, version)`（沿用现有"列 + document JSON"的 ORM 模式）。

约束：
- 删除规则：存在任何 report task 引用即拒绝删除（409），与已闭环的数据集删除业务对齐（数据集删除级联删除其文档版本与报告）。
- 上传新文档 = 新版本行，天然不影响历史报告。

### 6.2 DocumentReportTask（任务）

表 `document_report_tasks`：`id PK`、`document_version_id FK`、`status`（`PENDING/RUNNING/SUCCEEDED/FAILED`）、`evaluator_version`、`standard_version`、`standard_sha256`、`config JSONB`（模型、prompt 版本、参数）、`document JSONB`（阶段进度、逐指标耗时与调用统计、error）、时间戳三件套。

语义决策（回答需求 §五 的开放问题）：
- **重复评测允许**：每次点击生成新任务，历史全量保留。
- **失败重试**：旧失败任务保留审计，"重试"按钮创建新任务（不做同任务 attempt 递增，简化并发语义）。
- **可审计**：记录执行耗时、逐指标错误原因、LLM 调用统计（次数、重试、校验结果）。

### 6.3 DocumentQualityReportV1（报告契约）

新增 Pydantic 契约 `src/qa_eval/contracts/document.py`，JSON Schema 经 `scripts/contracts/generate_json_schemas.py` 生成到 `schemas/qa-eval/document-quality-report/`。

```text
DocumentQualityReportV1 (schema_version 1.0)
├── report_id / task_id / document_version_id / dataset_id
├── document: {version, filename, sha256, line_count, size_bytes}
├── evaluation_standard: {name, version, sha256}          ← D7
├── evaluator: {version, model, prompt_version, parameters,
│               structured_output_validation}             ← 冻结配置快照
├── execution_status: SUCCEEDED | PARTIAL_SUCCEEDED | FAILED
├── metrics: [ 4 × MetricSection ]
│     ├── metric: {key: "0101"…, version, name}
│     ├── status: OK | ERROR
│     ├── observations: [{satisfied_count}, {required_count},
│     │                   {score, unit: "percent"}]       ← 字段对齐 MetricResultV1
│     ├── items: [ 5 × ItemSection ]（同时整包存为 artifact）
│     │     ├── item_code / item_name
│     │     ├── status: SATISFIED|INSUFFICIENT|MISSING|UNVERIFIABLE|ERROR
│     │     ├── document_evidence: [{line_start, line_end, excerpt}]
│     │     ├── inspected_scope / reason / suggestion
│     │     └── fixability: AUTO_FIXABLE|DATA_DERIVABLE|REQUIRES_HUMAN_INPUT
│     └── error: null | string
├── overall: {score | null, scoring_policy, explanation}  ← D3，程序计算
├── missing_items: [{item_code, item_name, expected_section}]
├── recommendations: [{item_code, suggestion, fixability}]
├── limitations: [string]      ← 如文档截断、document-only 模式声明
└── generated_at
```

表 `document_quality_reports`：`report_id PK`、`document_version_id FK`、`task_id FK`、`document JSONB`、`created_at`。

### 6.4 与 MetricResultV1 的兼容关系（D2 落点）

- 指标编码沿用 `0101–0104`（ADR-0001 所有权表已认定其归属说明文档维度）；`subject` 语义为 `{scope: "DOCUMENT_VERSION", id}`。
- MetricSection 的 `metric / status / observations / evidence artifact` 字段与 `MetricResultV1` 同构，仅持久化位置不同（报告文档内 + ArtifactStore，而非 `metric_results` 表）。
- **迁移承诺**：当《数据集维度综合评测》落地需要跨维度汇总时，将 `metric_results.subject` 泛化为 `subject_type + subject_id`，文档指标结果机械迁入共享表；本 Schema 已按可机械迁移设计（无私有状态枚举、无私有标识格式）。

---

## 7. 后端服务与 API 设计

### 7.1 新增组件

| 组件 | 位置 | 职责 |
|---|---|---|
| `DocumentVersionService` | `application/services/document_version_service.py` | 上传校验、存储、列表、下载、删除（引用检查） |
| `DocumentEvaluationService` | `application/document_evaluation/` | 流水线编排：解析 → 逐指标评测 → 校验 → 汇总 → 报告 |
| 解析器 / 校验器 / Prompt | 同上（`parser.py / validator.py / prompts.py`） | 移植 `inspect_markdown.py` 与 `validate_result.py` 核心逻辑；`PROMPT_VERSION` 常量 |
| LLM 客户端 | `infrastructure/llm/` | OpenAI 兼容 HTTP 客户端：结构化输出、超时、重试、调用统计；配置入 `Settings`（model、base_url、api_key 环境变量、temperature、timeout、max_retries、max_output_tokens） |
| 任务执行器 | `apps/api/routes/document_report_tasks.py`（dispatch 模块） | 独立 `ThreadPoolExecutor`（默认 2 worker）；启动事件清扫孤儿 `RUNNING` → `FAILED{phase: INTERRUPTED}` |

### 7.2 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/datasets/{dataset_id}/document-versions` | multipart 上传（.md），201 |
| GET | `/api/v1/datasets/{dataset_id}/document-versions` | 版本列表（含最近一次报告摘要） |
| GET | `/api/v1/document-versions/{id}` | 元数据（sha256、行数、解析状态） |
| GET | `/api/v1/document-versions/{id}/download` | 原始文件流 |
| DELETE | `/api/v1/document-versions/{id}` | 被任务引用时 409 |
| POST | `/api/v1/document-versions/{id}/report-tasks` | 创建评测任务，202，冻结配置快照 |
| GET | `/api/v1/document-versions/{id}/report-tasks` | 历史任务列表 |
| GET | `/api/v1/document-report-tasks/{task_id}` | 状态 + 阶段进度 + report_id |
| GET | `/api/v1/document-quality-reports/{report_id}` | 完整报告 |

依赖注入沿用 `apps/api/dependencies.py` 既有模式；路由注册入 `apps/api/main.py`。

### 7.3 执行与容错

- 任务创建即返回 202，前端轮询任务状态（沿用 evaluation-runs 的交互模式）。
- 逐指标完成即落库进度；进程崩溃后重启清扫把 `RUNNING` 置 `FAILED`，已完成指标的结果随任务文档保留审计。
- LLM 单指标调用超时（默认 120s）、结构化解析失败、校验重试耗尽 → 指标 `ERROR`；全部指标 `ERROR` → 任务 `FAILED`，部分成功 → 报告仍生成（`PARTIAL_SUCCEEDED`）。
- 文档超过 token 上限：按行截断并在 `limitations` 中披露截断位置（MVP 不做分块检索）。

---

## 8. 前端页面与交互设计

### 8.1 DatasetDetailPage 增加 Tab

```
数据集详情
├── 数据集版本（现表格不动）
└── 说明文档版本（新增）
      ├── 上传按钮（.md，前端预校验大小/类型）
      └── 版本表格：version | filename | size | sha256(短) | 上传时间
            | 最近报告状态 | 操作（下载 / 生成评测报告 / 删除 / 查看报告）
```

### 8.2 交互状态机

```
点击「生成质量评测报告」→ 行内状态：排队中 → 评测中（轮询 task）
  → 成功：「查看报告」跳转报告页
  → 失败：展示失败阶段与错误原因 +「重试」（新建任务）
```

### 8.3 报告页（新页面，复用边界见 §9）

新路由 + `DocumentQualityReportPage`：报告头（标准版本/评测器版本/模型/生成时间）→ 总分卡（含 scoring_policy 说明与"指南未定义权重，等权平均为平台口径"声明）→ 4 指标分卡片 → 20 项明细表（状态 tag、证据行号+摘录、依据、建议、fixability）→ 缺失内容清单 → 建议列表（按 fixability 分组）。

---

## 9. 与现有评测体系的复用边界

| 现有能力 | 复用决策 |
|---|---|
| `ArtifactStore`（local/S3） | **复用**：原始文档、解析产物、items 明细 artifact |
| `uuid7`、`_fingerprint` 模式、"列 + document JSON" ORM 模式 | **复用** |
| alembic 迁移体系 | **复用**（新增一次迁移；注意 env.py 只读 `database_url`，测试库需临时覆盖环境变量） |
| 数据质量评测链路（Contract/Plan/Run/RuleEvaluation/Issue） | **不复用**：输入（文档 vs 样本记录）、判定（语义 vs 阈值）、报告结构（明细证据 vs gate/issue）均不同构，强行统一即需求警告的"为统一制造复杂度" |
| `build_quality_report` 与 `QualityReportV1` | **不复用渲染逻辑**，但报告生成纪律照搬：只从已持久化的判定组装，不在报告层重算语义 |
| 现有报告展示组件（gate/rule/issue 语义） | **不复用**，仅复用 antd 布局级组件与页面骨架 |
| 评测任务执行器（`evaluation_runs.py` 单线程池） | **不复用实例**，隔离为独立执行器，避免分钟级 LLM 任务阻塞数据质量评测 |

数据内容评测与说明文档评测长期双轨（需求 §八 原则），汇总到"数据集综合质量视图"时按 §6.4 的迁移承诺统一。

---

## 10. ADR-0001 对齐与修订（前置条件）

### 10.1 冲突点

需求主张的 `Document Version → Document Report Task → Document Quality Report` 与 ADR-0001 的三处规定相抵：skill 私有输出必须映射平台 Metric Result（:105）、正式 Quality Report 须由共享链路产出（:89）、禁止并行 Report schema（:42-47）。本需求文档未引用该 ADR，必须显式对齐后再开发。

### 10.2 修订要点（拟新 ADR-0003）

1. 说明文档评测 MVP 采用专用流水线，不经 Contract/Plan；**用户零配置通过平台内置默认实现满足，而非绕过 canonical 模型**。
2. 结果持久化以 `MetricResultV1` 兼容语义为准（§6.4）；状态枚举、指标编码、证据结构与共享层一致，`dataset-document-evaluator` Skill 的私有输出以本契约为映射目标。
3. `DocumentQualityReport` 作为报告家族中的独立 report kind；跨维度综合评测落地时泛化 `metric_results.subject` 并完成迁移（明确列入综合评测需求的前置项）。
4. rubric 的平台 canonical 副本位于 `configs/document-evaluation/rubric/`，Skill 转为该标准的上游来源，不再独立演进评分口径。

---

## 11. 分阶段开发计划与验收标准

| 阶段 | 范围 | 验收标准 |
|---|---|---|
| **P0** | ADR-0003 修订 ADR-0001；冻结 rubric 至 `configs/document-evaluation/rubric/1.0.0.json` | ADR 评审通过；冻结文件 sha256 稳定 |
| **P1** | DocumentVersion：实体、ORM、迁移、Service、上传/列表/下载/删除 API、前端 Tab（上传+列表+下载+删除） | 集成测试：上传→列表→下载哈希一致→删除；被引用版本删除返回 409；非 .md/超限/非 UTF-8/空文件全部 422 |
| **P2** | LLM 客户端基础设施；解析器与校验器移植；DocumentEvaluationService；任务模型 + 独立执行器 + 启动清扫；报告契约 + Schema 生成 + 持久化 | 单测：校验器对 golden fixture 全绿（含篡改行号/摘录的负例）；集成测试（mock LLM）覆盖成功、部分 ERROR、校验重试耗尽、进程重启清扫；总分永远等于复算值 |
| **P3** | 一键生成交互（状态轮询、重试）、报告页、历史任务列表 | 端到端：上传真实 .md → 一键生成 → 报告含 4 指标分、20 项判定、证据行号与摘录、缺失清单、建议、总分及口径声明；重复生成保留全部历史；失败任务展示阶段与原因 |
| **P4（backlog，不进 MVP）** | evidence-assisted 模式（dataset profile 比对）、HTML/PDF 解析、`metric_results.subject` 泛化迁移、指标注册表对齐 | 由《数据集维度综合评测》需求统一规划 |

每阶段均要求：不回归现有数据质量评测链路（`tests/integration` 全绿）；新契约有对应 JSON Schema 并纳入 `generate_json_schemas.py` 产物。

---

## 12. 预计修改文件与主要风险

### 12.1 文件清单（预估）

新增：`src/qa_eval/domain/entities/document_version.py`、`src/qa_eval/contracts/document.py`、`src/qa_eval/application/services/document_version_service.py`、`src/qa_eval/application/document_evaluation/{parser,evaluator,validator,report_builder,prompts}.py`、`src/qa_eval/infrastructure/llm/`、`src/qa_eval/infrastructure/db/models/{document_version,document_report_task,document_quality_report}.py`、`migrations/versions/xxxx_document_tables.py`、`configs/document-evaluation/rubric/1.0.0.json`、`schemas/qa-eval/document-*`、`apps/api/routes/{document_versions,document_report_tasks,document_quality_reports}.py`、`apps/web/src/api/documentVersions.ts`、`apps/web/src/pages/DocumentQualityReportPage.tsx`、对应 `tests/unit` `tests/integration`。

修改：`apps/api/main.py`（路由+启动清扫）、`apps/api/dependencies.py`、`src/qa_eval/config.py`（LLM 设置）、`apps/web/src/App.tsx`（路由）、`apps/web/src/pages/DatasetDetailPage.tsx`（Tab）、`scripts/contracts/generate_json_schemas.py`（纳入新契约）、`docs/adr/`（ADR-0003）。

### 12.2 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| LLM 编造证据行号/摘录 | 报告可信度崩塌 | 逐字校验 + 带反馈重试 + `ERROR` 降级不伪装成低分（D6） |
| LLM 不可用/超时/成本 | 任务失败 | 独立执行器隔离、超时与重试上限、失败原因落库；文档内容外发需先确认数据治理策略（内网网关/模型） |
| 文档超 token 上限 | 判定遗漏 | 上传 2MB 上限 + 行截断并在 `limitations` 披露 |
| 与 ADR-0001 冲突未闭环 | 架构分叉 | P0 强制前置（§10） |
| rubric 与 Skill 口径漂移 | 双标准 | 冻结文件为唯一 canonical，Skill 引用它（ADR-0003.4） |
| 测试依赖真实 LLM | CI 不稳定 | mock LLM + golden fixtures；校验器纯函数单测 |
| 孤儿 RUNNING 任务 | 状态永久卡死 | 启动清扫置 `FAILED`，保留已完成阶段审计 |

---

## 13. 结论

用户侧终态：**选择说明文档版本 → 点一次按钮 → 得到一份可追溯的完整报告**（4 指标分、20 项逐项判定与证据、缺失清单、改进建议、程序复算的等权总分），全程不接触指标、算子与契约。工程侧以最小 LLM 接口面（4 次结构化调用）换取可控可测的流水线，以 MetricResultV1 兼容语义保住与综合评测的合并路径。开发顺序：P0 ADR 修订 → P1 版本管理 → P2 流水线 → P3 前端闭环。
