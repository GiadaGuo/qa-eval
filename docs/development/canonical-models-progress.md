# Canonical Models 开发进度

最后更新：2026-10-27

## 文档用途

本文是 qa-eval canonical data-flow models 的持续开发交接记录。每批开发都应更新“已完成”“验证结果”“待完成”和“下一批计划”，避免把阶段状态散落在某个 Skill 内。

架构语义以 `docs/architecture/canonical-data-flow-models-v1.md` 和 ADR-0002 为准；本文件只记录落地进度，不替代架构决策。

## 目录与所有权

- `src/qa_eval/contracts/`：Pydantic wire contracts，是 JSON Schema 的可执行生成源。
- `schemas/qa-eval/`：生成后提交的跨语言 JSON Schema，供 Python、Web、外部 Calculator 和 artifact 校验使用。
- `src/qa_eval/domain/`：不依赖传输格式或 ORM 的领域实体、值对象和状态规则。
- `src/qa_eval/infrastructure/db/models/`：SQLAlchemy 持久化模型。
- `tests/contracts/`：Pydantic/JSON Schema 双重校验、生成一致性和契约回归测试。

共享 contract 不放在 `.agents/skills/`：三个评测 Skill 都是平台协议的消费者，不能拥有或维护平行协议。

## 第一批：基础数据交换契约

状态：已实现，待后续批次集成到 adapter/runner。

已完成：

- 新建严格 Pydantic contract 基类，默认拒绝未知字段。
- 实现 `Checksum`、`ArtifactRef 1.0`。
- 实现 `DatasetManifest 2.0`，包含 source snapshot、artifact、FieldMappingSpec 2.0 和声明上下文。
- 实现 `SampleRecord 2.0`，包含文本/asset/structured payload、多模态 content、annotation、source 和 lineage。
- 实现 `RejectedRecord 1.0`。
- 实现 `MetricResult 1.0` 与内嵌 `EvidenceArtifact`。
- 实现关键 Pydantic 语义约束：时区、presence/payload、content ID、annotation target、observation 类型、状态、population 闭合。
- 建立确定性 schema 生成器与五份提交到仓库的 JSON Schema。
- 建立有效样例、无效样例、schema 合法性和生成一致性测试。

验证命令：

```bash
uv run python scripts/contracts/generate_json_schemas.py --check
uv run pytest tests/contracts
uv run ruff check src/qa_eval/contracts scripts/contracts tests/contracts
```

2026-10-27 验证结果：

- contract 测试：19 passed。
- unit + contract 测试：全部通过。
- 完整测试：78 passed、6 skipped、8 errors；8 个 error 均为本地 PostgreSQL 测试库 `127.0.0.1:5432/qa_eval_test` 未运行导致的 integration fixture 连接失败，并非断言失败。
- 新增 Python 文件 Ruff 检查通过。
- schema `--check` 通过。

注意：JSON Schema 负责可移植的结构校验；跨字段算术、按 key 唯一等 JSON Schema 难以可靠表达的语义不变量由 Pydantic 层校验。进入领域层或持久化前必须经过 Pydantic contract 校验，不能只做 JSON Schema 校验。

## 待完成

- 把现有 `domain/entities` 中兼具 wire/领域职责的旧 Pydantic 模型拆为纯领域对象，并提供显式 mapper。
- 设计 `contracts -> domain` 和 `domain -> contracts` mapper；禁止 ORM 或 API 直接充当 canonical contract。
- 重构 SQLAlchemy ORM，使 DatasetVersion/Manifest/artifact 引用支持冻结模型，同时保持 migration 可回滚。
- 增加 `TemporalValue`、`NormalizationRun`、`DatasetProfile` 的 schema 与 contract。
- 实现旧 DatasetManifest 1.x、SampleRecord 1.x 到 canonical 版本的显式迁移器。
- 修复 MySQL adapter 的 NULL 保真，并让它只输出 `SampleRecord 2.0` 或 `RejectedRecord 1.0`。
- 将 02080103 Calculator 输入改为 role selector，输出改为 `MetricResult 1.0` 和规范化 EvidenceArtifact。
- 后续实现 registry、Contract、Plan、Run、Rule、Issue、Report contract 和持久化。

## 下一批建议

先完成标准化链路的边界重构：

1. 增加 `TemporalValue`、`NormalizationRun` 和 `DatasetProfile` contracts/schemas。
2. 建立旧 1.x wire model 与迁移器，不在运行时猜测版本或字段。
3. 建立 mapper 层并让 MySQL adapter 通过 canonical contract 输出；对 NULL、MISSING、空字符串写回归测试。
4. 在边界稳定后再迁移 ORM，避免数据库结构反向决定 wire contract。

## 已知决策与限制

- Pydantic 是 JSON Schema 的生成源，但字段语义仍由冻结架构文档和 ADR 约束。
- 生成的 schema 不手工修改；CI 应运行 `--check` 防止漂移。
- 第一批没有删除或改名旧 `domain/entities`，以保持现有 API 和测试可运行；它们仍是待迁移旧模型。
- 第一批没有引入数据库 migration，也没有改变现有数据文件。

## 第二批：02080103 到 MetricResult 的 canonical 计算链路

状态：已跑通至 MetricResult/Evidence；尚未到 Rule、Issue 和最终 QualityReport，不能称为完整端到端跑通。正式平台 Run 集成仍待共享运行层实现。

### 已完成

- 新增 `run_02080103_canonical.py`，显式完成旧快照到 `SampleRecord 2.0` 的迁移，不再依赖运行时自动猜测数据形状。
- 全部输入记录先经过 `SampleRecordV2` 校验；标准化失败进入 `RejectedRecord 1.0`，不会伪装为指标异常。
- 将 `plain_text_content` 固定映射为 text content role `article.body.plain`。
- 保真区分 NULL 和空字符串：NULL 输出 `presence=NULL` 且省略 payload；空字符串输出 `presence=PRESENT` 和空文本 payload，二者在指标阶段均按已声明的 EMPTY_TEXT 排除策略处理。
- Calculator 新增显式 `sample-record-v2` 输入模式，并通过 role selector 读取 `inline_text`；旧 raw/1.x 模式仅为回归兼容保留。
- 生成 canonical `DatasetManifest 2.0`、`SampleRecord 2.0`、空的 `RejectedRecord 1.0` artifact、`MetricResult 1.0` 和两个 `EvidenceArtifact`。
- `MetricResult` 已包含 exact、字符 n-gram、词 n-gram、近重复候选数和段落重复 observations；近重复明确标为 candidate，不作为确认问题。
- 新增两个 02080103 专属 evidence JSON Schema，并删除新产物中重复的 `exact_groups` 别名。
- 新增 `validate_02080103_canonical.py`，验证全量 Pydantic contract、平台 JSON Schema、指标 evidence schema、artifact checksum/record count、UUIDv7 和旧数值回归。
- 更新 02080103 指标资产，声明 canonical 输入角色、observation keys、单位、语义覆盖及 evidence schema。

真实运行产物：

- `data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/canonical-v1/dataset_manifest.json`
- `data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/canonical-v1/sample_records.jsonl`
- `data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/canonical-v1/rejected_records.jsonl`
- `data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/canonical-v1/metrics/02080103-v2.0.0/metric_result.json`
- `data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/canonical-v1/validation_summary.json`

真实数据验证结果：

- source/normalized：14,960 / 14,960。
- normalization rejected：0。
- metric evaluated/excluded：14,928 / 32，数量闭合。
- exact affected：89 / 14,928 = 0.005961950696677385。
- exact redundant：45 / 14,928 = 0.0030144694533762056。
- near-duplicate candidate records：7,517；不解释为已确认问题。
- 全量 Pydantic、JSON Schema、evidence schema、checksum、record count、UUIDv7 和旧结果回归均 PASS。
- Skill 测试：16 passed。

### 遇到的卡点与处理

| 卡点 | 影响 | 本批处理方式 | 状态 |
| --- | --- | --- | --- |
| Calculator 通过 `auto/raw/data_content` 猜输入形状 | 可能读取错误字段，无法满足 canonical role selector | 新增显式 `sample-record-v2`，每条记录先由 `SampleRecordV2` 校验，再按 `article.body.plain` 读取 | 已解决，不改平台模型 |
| 旧快照不是 DatasetManifest/SampleRecord | 无法直接进入 canonical Calculator | 新增显式迁移 runner，校验旧 snapshot checksum，生成 Manifest、SampleRecord 和 Reject artifact | 已解决，不改平台模型 |
| `MetricResult 1.0` 要求 `run_id/stage_run_id`，但 Runner/StageRun 尚未实现 | 无法建立真实平台 referential integrity | 调用方显式传入 UUIDv7，并生成 `authority=LOCAL_VALIDATION`、`not_a_platform_run=true` 的本地上下文；禁止声称为正式 Run | 临时绕行，待平台层实现 |
| Pydantic/JSON Schema 目前只校验 UUID，不校验必须为 UUIDv7 | 第一轮本地实现曾生成合法 UUIDv5，schema 仍会通过 | 指标 runner 实现 RFC 9562 UUIDv7，并在验证器中额外断言 version=7；错误产物已移出正式输出后重跑 | 指标侧已防护；是否增强平台 contract 待统一决策 |
| EvidenceArtifact 引用了不存在的 evidence schema，旧 group 文件还有重复别名 | schema_ref 无法解析，证据结构可能漂移 | 在 Skill 中补充两份指标专属 schema，验证全量 evidence，并删除新产物 `exact_groups` 别名 | 已解决；schema 最终注册位置待统一决策 |
| MetricResult 自身不携带 Calculator 引用，规范依赖 StageRun/PhysicalPlan 间接追踪 | 当前无法用已实现模型证明 Calculator 2.0.0 的正式链路 | 在 `local_execution_context.json` 记录 calculator key/version，仅作为本地验证事实 | 未解决，待运行层实现；不建议给 MetricResult 临时加字段 |
| MetricDefinition/CalculatorDescriptor 尚无已实现 Pydantic/schema/Registry | 无法机器校验 observation key 是否已正式注册 | 先在 02080103 YAML 明确声明 keys、类型和单位，验证器做数值回归 | 未解决，待共享 Registry contract 批次 |
| NormalizationRun 尚无第一批 executable contract/schema | `normalization_run_id` 只有引用，没有可校验运行实体 | 本地上下文记录 ID；Reject 为 0 条且独立空 artifact 仍保留 | 未解决，建议下一批按既定计划实现 |

### 复现命令

```bash
uv run python .agents/skills/dataset-data-quality-evaluator/scripts/run_02080103_canonical.py \
  data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z \
  data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/canonical-v1 \
  --dataset-id 019a2a10-0000-7000-8000-000000000101 \
  --dataset-version-id 019a2a10-0000-7000-8000-000000000102 \
  --normalization-run-id 019a2a10-0000-7000-8000-000000000103 \
  --run-id 019a2a10-0000-7000-8000-000000000104 \
  --stage-run-id 019a2a10-0000-7000-8000-000000000105

uv run python .agents/skills/dataset-data-quality-evaluator/scripts/validate_02080103_canonical.py \
  data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/canonical-v1 \
  data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/metrics/02080103-v2.0.0/metric_result.json \
  --output data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/canonical-v1/validation_summary.json
```

runner 使用原子目录发布且拒绝覆盖已有输出；复现前应选择新的输出目录，不能直接覆盖历史 canonical run。

### 下一步（需要平台模型决策）

1. 实现已经冻结设计中的 `NormalizationRun` executable contract/schema。
2. 实现 MetricDefinition、CalculatorDescriptor 和 Registry contract，使 observation key、输入 role 和参数可以机器校验。
3. 实现 PhysicalPlan、EvaluationRun、StageRun 后，移除本地运行上下文，改由正式运行实体分配 ID 和提供 Calculator 追踪。
4. 决定指标专属 evidence schema 是纳入根 `schemas/qa-eval/evidence/` 注册，还是由插件包发布并由平台 registry 解析。
5. 上述决策完成前，不修改当前平台模型，也不把本次 LOCAL_VALIDATION 登记为正式运行。

## 第三批：Contract、Rule、Issue 与 Report executable contracts

状态：Pydantic/JSON Schema 已实现；规则执行器、Issue 生成器和 Report 生成器尚未实现。

已完成：

- 实现 `QualityContractVersion 1.0`，threshold 是用户入参且没有平台默认值。
- 实现 `RuleEvaluation 1.0`，区分 PASS/FAIL/UNKNOWN/SKIPPED；禁止为 UNKNOWN/SKIPPED 伪造 observed。
- 实现带判别 subject 的 `QualityIssue 1.0`，覆盖 DATASET/GROUP/SAMPLE/PAIR/CONTENT/ANNOTATION。
- 实现 `IssueIndex 1.0`，校验 artifact kind、record count 与 confirmed/candidate 汇总闭合。
- 实现 `QualityReport 1.0`，分离 execution status、gate status、coverage、metric section 和 issue summary。
- 生成并提交五份 Draft 2020-12 JSON Schema，纳入统一生成器和 schema 漂移检查。
- 增加有效/无效 payload 双重校验与跨字段 Pydantic 不变量测试。

新增 schema：

- `schemas/qa-eval/quality-contract/1.0.schema.json`
- `schemas/qa-eval/rule-evaluation/1.0.schema.json`
- `schemas/qa-eval/quality-issue/1.0.schema.json`
- `schemas/qa-eval/issue-index/1.0.schema.json`
- `schemas/qa-eval/quality-report/1.0.schema.json`

实现细化：

- Contract v1 首批规则操作符为 `LT/LTE/GT/GTE/EQ/NE`；有序比较要求 numeric threshold。
- Contract 状态为 `DRAFT/PUBLISHED/RETIRED`。
- 文档语义要求 MetricResult 缺失或错误时生成 UNKNOWN/SKIPPED，因此实现明确规定这两种状态省略 observed；PASS/FAIL 必须携带 observed。
- JSON Schema 负责跨语言结构校验；scope/subject 一致性、时间先后、唯一 key、IssueIndex 数量闭合等继续由 Pydantic 语义层保证。

下一步：

1. 由 API/UI 接收用户的 Contract threshold、severity、on_missing 和 gate policy，发布不可变 Contract version。
2. 实现 Rule Evaluator，严格从 MetricResult observation 取值并应用用户 Contract。
3. 从 exact redundant 和 near-duplicate evidence 生成 QualityIssue/IssueIndex。
4. 实现 QualityReport generator，并对引用一致性和汇总数量做端到端测试。
5. 在上述步骤完成前，02080103 的状态仍是“计算链路跑通”，不是“最终报告跑通”。

## 第四批：Rule、IssueIndex 与 QualityReport 执行器

状态：执行器已实现并通过测试；已使用 Skill 提供的 MVP 建议 Contract 对真实 02080103 产物生成 LOCAL_VALIDATION QualityReport。

已完成：

- 在 `src/qa_eval/application/evaluation/` 实现共享 Rule Evaluator 和 QualityReport Generator。
- Rule Evaluator 从用户 Contract item 读取 operator/threshold，严格按 measurement key 读取 MetricResult observation；Calculator 不参与质量判定。
- MetricResult 为 MISSING/ERROR 时按用户 `on_missing` 产生 UNKNOWN/SKIPPED，不产生 FAIL；NOT_APPLICABLE 固定产生 SKIPPED。
- Gate 汇总同时考虑 Rule 状态、Contract item severity、`fail_on_severities` 和用户 `unknown_policy`。
- 在 02080103 Skill 中实现 evidence detector：exact redundant 生成 CONFIRMED Issue，near duplicate member 生成 CANDIDATE Issue。
- Issue detector 默认 severity 已进入指标资产：confirmed=WARNING、candidate=INFO；不与 Contract gate severity 混用。
- 生成 `issues.jsonl`、`IssueIndex 1.0`、`rule_evaluations.jsonl` 和最终 `QualityReport 1.0`。
- Report observations 只摘录 MetricResult；Issue 汇总只来自已经生成并提交的 Issues/IssueIndex，不重新计算指标。
- 消费 DUPLICATE_GROUPS evidence 前强制校验 checksum，拒绝被篡改的证据。
- 新增共享 UUIDv7 生成器，执行器生成的 Rule/Issue/Index/Report ID 均使用 UUIDv7。
- 执行状态必须由调用者显式传入，Report Generator 不从质量 PASS/FAIL 推导运行状态。

执行入口：

```bash
uv run python .agents/skills/dataset-data-quality-evaluator/scripts/complete_02080103_evaluation.py \
  <canonical-run-dir> \
  <user-quality-contract.json> \
  <new-output-dir> \
  --execution-status SUCCEEDED \
  --execution-authority LOCAL_VALIDATION
```

重要边界：

- CLI 不提供默认 threshold；Contract 缺少 threshold 会在 Pydantic/JSON Schema 校验阶段失败。
- 当前是单指标 02080103 executor；若 Contract 包含其他指标 item，会明确拒绝，而不是把它们静默计为已评测。
- Skill 提供的 `exact_redundant_record_ratio <= 0.01` 是政策文章通用语料的 MVP 建议基线，不是指南强制值或系统默认值；用户可修改 request 后发布新版本。
- `execution_status` 已禁止从 gate 推导，但在 EvaluationRun executable contract 完成前仍由上层调用者提供；接入正式 Runner 后应只从 EvaluationRun 读取。
- Contract fingerprint 的 canonical 计算/复核算法仍未冻结；当前执行器校验其格式和 immutable contract 内容，但尚不能独立重算 fingerprint。需要单独决策后实现，不能临时猜测。

测试覆盖：

- 用户 threshold 变化可以使同一 MetricResult 分别 PASS/FAIL。
- MISSING、ERROR、NOT_APPLICABLE 不会误判为质量 FAIL。
- exact/near evidence 能生成数量闭合的 CONFIRMED/CANDIDATE Issues。
- IssueIndex checksum、record count 和汇总闭合。
- 最终 QualityReport 通过 Pydantic 和 JSON Schema。
- evidence 被篡改时执行器拒绝继续生成报告。

下一步：

1. 用户根据具体用途确认或修改建议 threshold，再发布新的不可变 Contract version。
2. 实现平台 API/UI，使其提交与 Skill 相同的 ContractPublishRequest。
3. 后续将单指标 executor 收敛进正式 Planner/Runner，而不是继续在 Skill 脚本中扩展跨指标编排。

## 第五批：MVP ContractPublishRequest 与真实最终报告

状态：已完成。

为了避免用户直接构造 UUID、fingerprint、状态和发布时间，Skill 增加了非 canonical 的命令入参 `ContractPublishRequest 1.0`。它只包含用户应负责的业务配置：

- `name`、`version_label`；
- 可选的既有 `contract_id`，用于发布同一逻辑 Contract 的新版本；
- modalities 和 task types；
- metric、measurement、operator、threshold、severity、on_missing；
- gate 的 unknown policy 和 fail severities。

Publisher 负责：

- 仅接受 02080103 v1.0.0 已声明的 measurement；
- 生成 UUIDv7 `contract_id/contract_version_id`；
- 固定状态为 PUBLISHED 并填写带时区发布时间；
- 对 applicability、items 和 gate 的规范化语义计算 SHA-256 fingerprint；
- 输出 canonical `QualityContractVersion 1.0`。

MVP 建议 request：

- `.agents/skills/dataset-data-quality-evaluator/assets/contracts/02080103-mvp-baseline.request.json`
- measurement：`exact_redundant_record_ratio`
- rule：`LTE 0.01`
- severity：ERROR
- on_missing：UNKNOWN
- unknown_policy：FAIL
- fail_on_severities：ERROR、CRITICAL

1% 表示每个完全重复组保留一个代表样本后，冗余记录不超过有效文本的 1%。这是当前政策文章通用语料的建议起点，不是指南要求；业务方可以通过修改 request 发布 0.5%、2% 等新版本。

真实发布结果：

- Contract：`data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/canonical-v1/contracts/02080103-mvp-baseline-v1.0.0.json`
- Contract version ID：`01a17bcc-44e8-7ff2-8a19-3e8b3e58c55f`
- fingerprint：`sha256:b13da59f64397f0fc5aeca1620bf36e4639f8a57a448dcef7e6e101794a71911`
- 实测 exact redundant ratio：0.0030144694533762056，小于 0.01，Rule 和 gate 均为 PASS。
- CONFIRMED Issues：45。
- CANDIDATE Issues：7,517。
- 最终 QualityReport：`data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/canonical-v1/quality/02080103-mvp-baseline-v1.0.0/quality_report.json`
- 最终全量验证：Pydantic、JSON Schema、checksum、record count、引用一致性全部 PASS。
- execution authority：LOCAL_VALIDATION；该报告数据模型完整，但不冒充已持久化的平台正式 Run。

未来平台界面应提交同一 request JSON；后端调用相同 publisher，不能允许客户端直接指定 fingerprint、PUBLISHED 状态、发布时间或 contract version ID。
