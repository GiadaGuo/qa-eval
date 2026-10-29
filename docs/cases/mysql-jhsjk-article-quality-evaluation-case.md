# MySQL `jhsjk_article` 数据集综合质量评测案例报告（V0.1）

- **案例日期**：2026-10-29（Asia/Shanghai）
- **案例状态**：阶段性 V0.1 报告；基于固定 Dataset Version、Agent Session、Contract 与 Evaluation Run 的真实产物
- **固定 Run ID**：`01a1852e-2546-73ec-8ea9-b7e6b63ca491`
- **证据清单**：[`evidence/mysql-jhsjk-article-20261029/manifest.json`](evidence/mysql-jhsjk-article-20261029/manifest.json)
- **报告摘录**：[`evidence/mysql-jhsjk-article-20261029/quality-report.json`](evidence/mysql-jhsjk-article-20261029/quality-report.json)
- **核验记录**：[`evidence/mysql-jhsjk-article-20261029/verification.md`](evidence/mysql-jhsjk-article-20261029/verification.md)

---

## 阶段性结果摘要

| 项目                      | 数值 / 说明                                                                               |
| ------------------------- | ----------------------------------------------------------------------------------------- |
| 数据集                    | `policy.jhsjk_article` 固定 JSONL 快照                                                  |
| Dataset ID / Version ID   | `d769d8a6-41c0-4690-98c7-3ef08ae3dd6d` / `01a1852e-189d-74f0-ad95-b00793cd80bc`       |
| 原始记录数                | 14,960 条                                                                                 |
| 标准化记录数 / 拒绝记录数 | 14,960 / 0                                                                                |
| 已评测指标                | 0204 结构完整性、02080103 重复内容程度                                                    |
| 0204 结构完整性           | 14,928 / 14,960 = **99.7861%**，Rule PASS                                           |
| 02080103 重复内容程度     | affected 89 / 14,928 = 0.596%；redundant 45 / 14,928 = 0.301%；Rule PASS                  |
| 发现的质量问题            | 0204：32 条空字符串证据；02080103：44 个精确重复组、45 条确认冗余副本                     |
| 已评测指标得分            | **99.59495**                                                                        |
| 有效覆盖率                | **14.2857%**（2 / 14 个指南叶子指标已评分）                                         |
| 报告 Gate                 | PASS                                                                                      |
| 关键限制                  | 仅 2 个指标完成真实执行；其余 12 个叶子指标未纳入本次 Run；UI 点击验收未执行；Ruff 未全绿 |

> **得分声明**：`99.59495` 仅代表已评测的 2 个叶子指标在平台 V1 等权口径下的结果，**不能解释为《高质量数据集指南》全指标综合质量得分**。未评测指标未被默认为 0 分，雷达图中未评测维度显示为 N/A。

---

## 1. 案例背景与目标

### 1.1 业务背景

高质量数据集是模型开发与训练的基础输入。《高质量数据集指南》将数据质量划分为格式规范性、安全规范性、标注规范性、结构完整性、内容真实性、内容一致性、类型一致性、内容干净性等维度，并在内容干净性下进一步细分文本困惑程度、知识信息密度、重复内容程度、文本完整程度、信息缺失程度、文本纯净程度、文本连贯程度等叶子指标。本案例选择真实的 MySQL `policy.jhsjk_article` 表快照，验证 `qa-eval` 平台能否依据该指标体系对真实数据集完成可解释、可追溯、可复现的综合质量评测。

### 1.2 为什么选择 `jhsjk_article`

- **真实业务数据**：来源标识为 `mysql://policy/jhsjk_article`，字段包含样本标识、文章正文、来源信息与空值标记，具有典型文本数据集特征。
- **规模适中**：14,960 条记录足以暴露重复、缺失等质量问题，又不会因数据量过大导致单次演示不可完成。
- **已有标准化链路**：平台具备 `jsonl_mapping@2.0.0` 与 `jhsjk_article_snapshot_migration@1.0.0`，可将原始快照标准化为 `SampleRecordV2`，支持 Calculator 直接消费。
- **便于复现**：快照文件、字段映射、Dataset Version、Profile fingerprint、Contract 与 Run ID 全部固定，可作为后续迭代的基线。

### 1.3 本案例验证的能力

1. 数据集接入与标准化能力（JSONL 上传、字段映射、Profile 生成）。
2. 基于指标定义的质量评测能力（Metric Definition、Calculator、Rule、Evidence）。
3. 确定性 Agent 推荐与人工确认流程（Agent Session、Skill、Tool Invocation、Proposal Revision、Quality Contract）。
4. Calculator 执行与规则判定能力（Physical Plan、StageRun、MetricResult、RuleEvaluation）。
5. 质量问题发现、证据追溯与整改建议能力（Quality Issue、IssueIndex、Evidence artifact）。
6. 综合评分与有效覆盖率展示能力（Quality Report v1.1、雷达图、得分/覆盖率并列）。

### 1.4 案例定位

本案例属于 **阶段性 MVP 验证**，不等于平台全部能力已经投产。当前仅有 2 个指标完成真实执行，报告必须同时展示“已评测得分”与“有效覆盖率”，并明确未评测指标的原因。

---

## 2. 数据集概况

### 2.1 来源与业务背景

| 项目       | 值                                                                        |
| ---------- | ------------------------------------------------------------------------- |
| 数据集名称 | `policy.jhsjk_article.case-20261029`                                    |
| 来源 URI   | `mysql://policy/jhsjk_article`                                          |
| 数据类型   | 中文文本（文章正文）                                                      |
| 任务类型   | `generic_corpus`                                                        |
| 语言声明   | `zh`                                                                    |
| 快照时间   | 2026-10-22T08:50:48Z（上传快照）                                          |
| 一致性     | `UPLOADED_SNAPSHOT`：本次按人工评审跳过 MySQL 直连，使用固定 JSONL 快照 |

### 2.2 数据范围与规模

| 项目                    | 数值                                                                           |
| ----------------------- | ------------------------------------------------------------------------------ |
| 原始记录数              | 14,960                                                                         |
| 标准化记录数            | 14,960                                                                         |
| 拒绝记录数              | 0                                                                              |
| 原始快照 SHA-256        | `dcc198fc35811a22d0d045177621876cfb5733296326e446d3c31154c142e59f`           |
| 标准化 artifact SHA-256 | `176caf18d2275214b0dadf956fb9b1862e0e04c55de9f256fe11d3685c54a7b3`           |
| 拒绝 artifact SHA-256   | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`（空文件） |
| Profile fingerprint     | `sha256:1f2e10fdd0c432de25da7ae9a93daf335b04bed305a2e6805682b903ce36d57c`    |

### 2.3 字段结构与映射

原始 JSONL 字段：`sample_id`、`text`、`source`、`text_is_null`。

| 源字段           | 目标类型         | 目标角色 / 名称        | 策略               | 说明                                   |
| ---------------- | ---------------- | ---------------------- | ------------------ | -------------------------------------- |
| `sample_id`    | SAMPLE_ID        | —                     | `REJECT_MISSING` | 样本唯一标识，缺失则拒绝               |
| `text`         | CONTENT          | `article.body.plain` | `PRESERVE`       | 文章正文纯文本；保留 `null`/空串差异 |
| `source`       | SOURCE_ATTRIBUTE | `source`             | `PRESERVE`       | 来源元数据（数据库、表、字段）         |
| `text_is_null` | METADATA         | `text_is_null`       | `PRESERVE`       | 原始空值标记                           |

映射版本：`jsonl_mapping@2.0.0`。标准化过程未拒绝任何记录；空字符串样本仍保留在 `SampleRecordV2` 中，由 0204 计算器作为 `EMPTY_STRING` 缺失类别识别。

### 2.4 Dataset Profile

Profile ID：`01a1852e-1d2d-72b3-b5ab-3ecac145be05`；Profiler：`base_dataset_profiler@1.0.0`；扫描方式：`FULL_SCAN`。

| 画像项                          | 数值                                                            |
| ------------------------------- | --------------------------------------------------------------- |
| 内容模态                        | `text`                                                        |
| 内容角色                        | `article.body.plain`（14,960 条）                             |
| 文本长度 min / max              | 0 / 36,397 字符                                                 |
| 文本长度 mean / p50 / p95       | 1,442.9 / 807 / 4,791 字符                                      |
| `article.body.plain` 缺失统计 | `NULL`: 0；`EMPTY`: 32；`MISSING`: 0；`PRESENT`: 14,960 |
| 语言分布                        | `zh`: 100%                                                    |

> 注：Profile 中的 `present_count` 包含空字符串样本；0204 计算时会将空字符串从“完整”中剔除，因此完整记录数为 14,928。

### 2.5 数据接入异常及 Reject 情况

本次标准化无拒绝记录。0204 识别出的 32 条 `EMPTY_STRING` 属于**内容缺失事实**，不是标准化阶段的拒绝。02080103 在执行前将这 32 条样本排除（`EMPTY_OR_NULL_TEXT`），其分母为 14,928 而非 14,960。

---

## 3. 评测依据与指南指标体系

### 3.1 指南维度与叶子指标

依据为项目中注册的 Metric Definition（`configs/registry/metrics/`），对应《高质量数据集指南》0201–0208 及文本子指标。

| 编号     | 指标名称     | 父指标 | 指南维度          | 测量方向           |
| -------- | ------------ | ------ | ----------------- | ------------------ |
| 0201     | 格式规范性   | —     | 数据质量          | 越高越好           |
| 0202     | 安全规范性   | —     | 数据质量          | 越高越好           |
| 0203     | 标注规范性   | —     | 数据质量          | 越高越好           |
| 0204     | 结构完整性   | —     | 数据质量          | 越高越好           |
| 0205     | 内容真实性   | —     | 数据质量          | 越高越好           |
| 0206     | 内容一致性   | —     | 数据质量          | 越高越好           |
| 0207     | 类型一致性   | —     | 数据质量          | 越高越好           |
| 0208     | 内容干净性   | —     | 数据质量          | 越高越好（父指标） |
| 02080101 | 文本困惑程度 | 0208   | 内容干净性 / 文本 | 越低越好           |
| 02080102 | 知识信息密度 | 0208   | 内容干净性 / 文本 | 越高越好           |
| 02080103 | 重复内容程度 | 0208   | 内容干净性 / 文本 | 越低越好           |
| 02080104 | 文本完整程度 | 0208   | 内容干净性 / 文本 | 越低越好           |
| 02080105 | 信息缺失程度 | 0208   | 内容干净性 / 文本 | 越低越好           |
| 02080106 | 文本纯净程度 | 0208   | 内容干净性 / 文本 | 越低越好           |
| 02080107 | 文本连贯程度 | 0208   | 内容干净性 / 文本 | 越高越好           |

### 3.2 指标权重

指南未提供可直接用于数值聚合的跨指标权重。平台 V1 采用**指南叶子指标等权**口径（`GUIDE_WEIGHT_OR_EQUAL_LEAF_FALLBACK_V1`），并在报告中显式声明。0208 父指标不独立计分，仅由其叶子指标聚合；父子指标不重复计权。

### 3.3 当前 Calculator 实现情况与本次 Run 实际执行状态

| 编号     | Calculator 注册状态                                                                      | 候选状态（本次 Proposal 快照）                      | 本次 Run 状态                       | 说明                     |
| -------- | ---------------------------------------------------------------------------------------- | --------------------------------------------------- | ----------------------------------- | ------------------------ |
| 0201     | `format_compliance@1.0.0` AVAILABLE                                                    | APPLICABLE，但无 DEMO_POLICY_V1 阈值 → NEEDS_INPUT | **NOT_EVALUATED**             | 可实现，缺业务阈值       |
| 0202     | `content_safety_compliance@1.0.0` UNAVAILABLE                                          | UNAVAILABLE                                         | **NOT_EVALUATED**             | 占位注册，无实现代码     |
| 0203     | `annotation_compliance@1.0.0` UNAVAILABLE                                              | UNAVAILABLE                                         | **NOT_EVALUATED**             | 无标注字段，且实现占位   |
| 0204     | `structural_completeness@1.0.0` AVAILABLE                                              | APPLICABLE                                          | **EVALUATED / OK / PASS**     | 真实执行                 |
| 0205     | `content_authenticity@1.0.0` UNAVAILABLE                                               | UNAVAILABLE                                         | **NOT_EVALUATED**             | 需外部证据/模型，未实现  |
| 0206     | `content_consistency@1.0.0` UNAVAILABLE                                                | UNAVAILABLE                                         | **NOT_EVALUATED**             | 需字段关系或语义模型     |
| 0207     | `dataset_type_consistency@1.0.0` UNAVAILABLE                                           | UNAVAILABLE                                         | **NOT_EVALUATED**             | 需数据集类型声明与分类器 |
| 0208     | `content_cleanliness@1.0.0` UNAVAILABLE（父指标占位）                                  | UNAVAILABLE                                         | **NOT_EVALUATED**（派生展示） | 由子指标聚合，不独立执行 |
| 02080101 | `text_perplexity@1.0.0` AVAILABLE                                                      | APPLICABLE，但无阈值 → NEEDS_INPUT                 | **NOT_EVALUATED**             | 可实现，缺业务阈值       |
| 02080102 | `knowledge_information_density@1.0.0` AVAILABLE                                        | APPLICABLE，但无阈值 → NEEDS_INPUT                 | **NOT_EVALUATED**             | 可实现，缺业务阈值       |
| 02080103 | `exact_text_duplicate@1.0.0` AVAILABLE；另有 `text_duplicate_degree@2.0.0` AVAILABLE | APPLICABLE，选定 exact                              | **EVALUATED / OK / PASS**     | 真实执行                 |
| 02080104 | `text_completeness@1.0.0` AVAILABLE                                                    | APPLICABLE，但无阈值 → NEEDS_INPUT                 | **NOT_EVALUATED**             | 可实现，缺业务阈值       |
| 02080105 | `information_missing_degree@1.0.0` AVAILABLE                                           | APPLICABLE，但无阈值 → NEEDS_INPUT                 | **NOT_EVALUATED**             | 可实现，缺业务阈值       |
| 02080106 | `text_purity@1.0.0` AVAILABLE                                                          | APPLICABLE，但无阈值 → NEEDS_INPUT                 | **NOT_EVALUATED**             | 可实现，缺业务阈值       |
| 02080107 | `text_coherence@1.0.0` AVAILABLE                                                       | APPLICABLE，但无阈值 → NEEDS_INPUT                 | **NOT_EVALUATED**             | 可实现，缺业务阈值       |

> 重要区分：
>
> - **AVAILABLE 但未执行**（0201、02080101、02080102、02080104–02080107）：Calculator 已存在，但因 `DEMO_POLICY_V1` 未配置推荐阈值，Agent 推荐时将其标记为 `NEEDS_INPUT`，未纳入 Contract。
> - **UNAVAILABLE 未执行**（0202、0203、0205、0206、0207、0208 父指标）：Calculator 为占位注册，无执行入口或代码实现。
> - **NOT_EVALUATED 不等于 FAIL，也不等于 0 分**；报告中未评测维度显示为 N/A。

### 3.4 阈值来源

本次 Contract 使用的阈值来自 `DEMO_POLICY_V1`，**不是指南规定的权威阈值**：

| 指标     | 测量                            | 规则 | 阈值 | 来源               |
| -------- | ------------------------------- | ---- | ---- | ------------------ |
| 0204     | `complete_record_ratio`       | GTE  | 0.95 | `DEMO_POLICY_V1` |
| 02080103 | `exact_affected_record_ratio` | LTE  | 0.05 | `DEMO_POLICY_V1` |

---

## 4. 平台评测流程与可追溯链路

### 4.1 端到端链路

```text
Dataset d769d8a6-41c0-4690-98c7-3ef08ae3dd6d
  → DatasetVersion 01a1852e-189d-74f0-ad95-b00793cd80bc
  → DatasetProfile 01a1852e-1d2d-72b3-b5ab-3ecac145be05
  → AgentSession 01a1852e-1d4c-743f-9d68-ab9b27b7789c
  → ProposalRevision 01a1852e-1db5-7885-aafb-a9efa0ca1ee5
  → ContractVersion 01a1852e-251f-72fe-8f28-504307408481
  → LogicalPlan 01a1852e-2544-7c2d-be1c-f31950ab4a71
  → PhysicalPlan 01a1852e-2546-7924-abe8-1d775ee3f1f7
  → Run 01a1852e-2546-73ec-8ea9-b7e6b63ca491
  → StageRun / MetricResult / RuleEvaluation / IssueIndex
  → QualityReport 01a1852e-2ea8-713e-9733-555d6563048a
```

### 4.2 各环节输入、处理与输出

| 环节                       | 输入                              | 处理                                   | 输出                          | 固定 ID / Artifact                                                                                   |
| -------------------------- | --------------------------------- | -------------------------------------- | ----------------------------- | ---------------------------------------------------------------------------------------------------- |
| Dataset Version            | 原始 JSONL 快照、字段映射         | 上传、标准化、发布                     | `PUBLISHED` Dataset Version | `01a1852e-189d-74f0-ad95-b00793cd80bc`                                                             |
| Dataset Profile            | 标准化 artifact                   | 全量扫描画像                           | Profile 文档与 fingerprint    | `01a1852e-1d2d-72b3-b5ab-3ecac145be05`                                                             |
| Agent Session              | Dataset Version、用户消息         | 创建/恢复会话，加载 Data Quality Skill | Session 与消息记录            | `01a1852e-1d4c-743f-9d68-ab9b27b7789c`                                                             |
| Proposal Revision          | Profile、Registry、Applicability  | 确定性推荐、校验                       | Proposal + coverage 快照      | `01a1852e-1db5-7885-aafb-a9efa0ca1ee5`                                                             |
| Quality Contract           | 人工确认的 Proposal               | 原子发布多 item Contract               | PUBLISHED Contract            | `01a1852e-251f-72fe-8f28-504307408481`                                                             |
| Logical Plan               | Contract、Dataset Version         | 编译为 metric 操作序列                 | Logical Plan                  | `01a1852e-2544-7c2d-be1c-f31950ab4a71`                                                             |
| Physical Plan / Run        | Logical Plan、normalized artifact | 创建 Stage、线程池执行                 | Run + StageRun                | Run `01a1852e-2546-73ec-8ea9-b7e6b63ca491`                                                         |
| Metric Result              | normalized artifact、Calculator   | 逐指标计算、生成 Evidence              | MetricResult                  | `01a1852e-274f-7b9a-8ab0-bf032eed5f0e`（0204）`01a1852e-2e83-7052-b420-635c4e4e0cf6`（02080103） |
| Rule Evaluation            | MetricResult、Contract rule       | 规则判定                               | RuleEvaluation                | 2 条 PASS                                                                                            |
| Quality Issue / IssueIndex | Duplicate Evidence                | 去重、确认冗余副本                     | 45 条 CONFIRMED Issue         | IssueIndex `01a1852e-2ea5-7cb0-b7a9-bc775909c551`                                                  |
| Quality Report             | 上述全部结果                      | 聚合得分、覆盖率、雷达图、建议         | QualityReport v1.1            | `01a1852e-2ea8-713e-9733-555d6563048a`                                                             |

### 4.3 数据一致性核对

- Run 的 `input_fingerprint` = 原始快照 SHA-256 = `dcc198fc...e59f`。
- Report、Run、Physical Plan、Contract 均引用同一 Dataset Version。
- Report 与 Run 均引用同一 Contract Version。
- Physical Plan 仅含 0204、02080103 两个 stage；数据库中仅存在对应 2 个 `MetricResult(status=OK)`。

### 4.4 当前架构边界

- V1 Agent Runtime 使用**进程内线程池**后台执行，具备真实 Session/Skill/Tool 调用轨迹，但尚未具备持久队列、租约、多进程恢复、真正取消与完整预算/重试。
- 当前 Run 已生成 Logical Plan 与 Physical Plan，但依赖 DAG 与 Stage 失败传播仍为 V1 顺序语义，未实现生产级工作流引擎。

---

## 5. Agent 推荐、人工调整与契约确认

### 5.1 确定性 Agent 推荐机制

Agent Session `01a1852e-1d4c-743f-9d68-ab9b27b7789c` 加载 `data-quality@1.0.0`，按用户意图“为当前 policy.jhsjk_article 固定版本推荐综合质量评测方案”依次调用：

1. `get_dataset_profile`
2. `discover_metrics`
3. `recommend_evaluation_proposal`
4. `create_quality_contract`
5. `create_evaluation_run`
6. `get_evaluation_status`
7. `get_quality_report`

推荐服务为**确定性规则**：遍历数据质量指标，调用 `ApplicabilityService` 评估每个 Calculator Binding；对 `APPLICABLE` 候选按确定性、成本、Calculator key/version 排序；默认只将 `APPLICABLE` 项放入 Proposal。无 `DEMO_POLICY_V1` 阈值的 AVAILABLE 指标进入 `NEEDS_INPUT`，不加入 Proposal。

### 5.2 本次推荐结果

推荐 Tool 返回的覆盖快照显示：

- 15 个数据质量指标、10 个可用 Calculator 实现；
- 仅 0204、02080103 进入 Proposal；
- 0201、02080101、02080102、02080104、02080105、02080106、02080107 为 `NEEDS_INPUT`（缺阈值）；
- 0202、0203、0205、0206、0207、0208 父指标为 `UNAVAILABLE`（实现占位）。

### 5.3 人工调整与契约确认

| 项目                | 内容                                                                                  |
| ------------------- | ------------------------------------------------------------------------------------- |
| 人工调整            | **无**；采用确定性推荐的 2 个 items 与 `DEMO_POLICY_V1` 阈值                  |
| 确认方式            | 通过 `POST /agent-sessions/{id}/contract-confirmations` 提交确认事件                |
| Contract 发布时间   | `2026-10-29T01:02:36.319432Z`                                                       |
| Contract Version ID | `01a1852e-251f-72fe-8f28-504307408481`                                              |
| Contract items      | 0204（`structural_completeness@1.0.0`）、02080103（`exact_text_duplicate@1.0.0`） |

### 5.4 系统能力边界与本次实际操作的区分

- **系统支持**：指标选择、阈值配置、规则校验、原子发布多 item Contract。
- **本次实际操作**：未调整任何指标或阈值，直接确认推荐结果。
- **浏览器 UI 点击验收**：`NOT_EXECUTED`。本次通过本地 HTTP API 调用与工作台相同的端点完成真实写入和执行，但没有浏览器实际点击记录或截图。

### 5.5 Agent 响应模式说明

已配置的 `kimi-k2.6` Provider 拒绝 `temperature=0.1`（网关提示该模型只接受 1）。两次调用失败后，Runtime 按设计进入 `DETERMINISTIC_FALLBACK / LLMTransportError`。推荐和执行结果来自确定性 Tool，不受模型失败影响；Provider 参数兼容性是进入生产化前需修复的明确缺口。

---

## 6. Contract、Run、Calculator 与真实指标结果

### 6.1 Contract 与 Run 概况

| 项目                | 值                                                                 |
| ------------------- | ------------------------------------------------------------------ |
| Contract Version ID | `01a1852e-251f-72fe-8f28-504307408481`                           |
| Contract 名称       | `policy.jhsjk_article 综合质量案例 20261029`                     |
| Contract 版本标签   | `case-20261029-v1`                                               |
| Contract items      | 2 个                                                               |
| Run ID              | `01a1852e-2546-73ec-8ea9-b7e6b63ca491`                           |
| Physical Plan ID    | `01a1852e-2546-7924-abe8-1d775ee3f1f7`                           |
| Run 状态            | `SUCCEEDED`                                                      |
| 执行起止            | `2026-10-29T01:02:36.384982Z` → `2026-10-29T01:02:38.731570Z` |

### 6.2 指标计算结果汇总

| 指标     | Calculator                        | 测量                                       | 分子 / 分母     | 值           | Rule          | 状态       |
| -------- | --------------------------------- | ------------------------------------------ | --------------- | ------------ | ------------- | ---------- |
| 0204     | `structural_completeness@1.0.0` | `complete_record_ratio`                  | 14,928 / 14,960 | 0.9978609626 | GTE 0.95 PASS | OK         |
| 02080103 | `exact_text_duplicate@1.0.0`    | `exact_affected_record_ratio`            | 89 / 14,928     | 0.0059619507 | LTE 0.05 PASS | OK         |
| 02080103 | —                                | `exact_redundant_record_ratio`（诊断项） | 45 / 14,928     | 0.0030144695 | —            | diagnostic |

### 6.3 0204 结构完整性

#### 6.3.1 指标定义与业务含义

数据填充完整，不包含缺失值，或缺失值处于用户认可的合理范围。本次测量 `complete_record_ratio`，即 `article.body.plain` 非空且非空字符串的记录占比。

#### 6.3.2 Calculator 实现

`StructuralCompletenessCalculator@1.0.0` 遍历标准化 `SampleRecordV2`，按 `content_role=article.body.plain` 统计：

- `PRESENT`：字段存在且文本非空；
- `EMPTY_STRING`：字段存在但文本为空或仅空白；
- `NULL`：字段标记为 NULL；
- `FIELD_ABSENT`：字段不存在。

输出 `complete_record_ratio = PRESENT / (PRESENT + EMPTY_STRING + NULL + FIELD_ABSENT)`。

#### 6.3.3 输入数据与参数

- 输入 artifact：`sample_records.jsonl`（14,960 条，SHA-256 `176caf...a7b3`）。
- 参数：`content_role=article.body.plain`。
- 分母：14,960（与标准化记录数一致，无排除）。

#### 6.3.4 结果解释

- 完整记录：14,928；缺失/空字符串：32；比值 ≈ 99.7861%。
- 32 条证据为 `EMPTY_STRING`，artifact SHA-256 `a14a2db43f65f6f45810ae07657ab0c028d4916cf5d9fe4bc5df232b3653ebe6`。
- Rule `GTE 0.95` 判定为 PASS。
- 0204 **未生成 Quality Issue**：当前实现仅输出缺失证据，未将缺失记录提升为 Issue。

#### 6.3.5 局限

Calculator 仅报告缺失事实；“合理缺失范围”需由业务规则在 Contract 中表达，本次阈值 `0.95` 为 Demo 口径。

### 6.4 02080103 重复内容程度

#### 6.4.1 指标定义与业务含义

衡量文档、段落、句子之间存在重复内容的程度。本次执行的是**规范化整记录精确重复**子测量，不覆盖语义重复、段落级重复或 n-gram 重复。

#### 6.4.2 Calculator 实现

`ExactTextDuplicateCalculator@1.0.0`：

1. 读取 `sample_records.jsonl`；
2. 提取 `article.body.plain`；
3. 对文本做 `NFKC` 归一化并压缩连续空白；
4. 计算归一化文本 SHA-256；
5. 按 hash 分组，组大小 > 1 即为精确重复组；
6. 排除空/NULL 文本后再计算比例。

#### 6.4.3 输入数据与参数

- 输入 artifact：`sample_records.jsonl`。
- 参数：`content_role=article.body.plain`。
- 预处理排除：32 条 `EMPTY_OR_NULL_TEXT`（与 0204 空字符串样本一致）。
- 有效分母：14,928。

#### 6.4.4 结果解释

| 统计口径                 | 数值                         | 计算                          |
| ------------------------ | ---------------------------- | ----------------------------- |
| 重复组数                 | 44                           | hash 分组后大小 > 1 的组      |
| 受影响记录数（affected） | 89                           | 所有重复组内记录总数          |
| 冗余记录数（redundant）  | 45                           | 每组保留 1 条代表后剩余副本数 |
| 受影响比例               | 0.0059619507                 | 89 / 14,928                   |
| 冗余比例                 | 0.0030144695                 | 45 / 14,928                   |
| 重复组大小分布           | 43 组 × 2 条 + 1 组 × 3 条 | —                            |

Rule `LTE 0.05` 判定为 PASS。44 个重复组生成 1 个 `DUPLICATE_GROUPS` Evidence artifact（SHA-256 `5e152948c1a2f74a1e2b111b9e69bb2542e55fab83599873886c661c53ae5fce`）。

#### 6.4.5 统计口径区分

- **重复记录数（affected）**：所有落在重复组中的记录，包含每组保留的代表样本。
- **冗余记录数（redundant）**：在重复组中可安全删除的副本数，等于 affected − 重复组数。
- **异常记录数**：本报告中未使用该口径；0204 的 32 条空字符串与 02080103 的 89 条重复记录不应混用。

#### 6.4.6 Issue 生成

- 45 条冗余样本被确认为 `EXACT_DUPLICATE_REDUNDANT`。
- Issue 状态：`OPEN`；determination：`CONFIRMED`；severity：`WARNING`。
- IssueIndex ID：`01a1852e-2ea5-7cb0-b7a9-bc775909c551`；记录数 45；artifact SHA-256 `372b8b8913fb4527b4b6777c5d7d10fbe56dcf15c49ad4c8e625b2959954d66e`。

#### 6.4.7 局限

仅识别归一化后完全相同的整记录；不识别：

- 语义重复；
- 段落/句子级重复；
- 字符 n-gram / 词 n-gram 重复；
- MinHash 近重复候选。

---

## 7. 已评测得分、有效覆盖率与雷达图分析

### 7.1 评分公式与归一化

平台 V1 评分逻辑（`report_generator.py`）：

- 仅对 `MetricResult.status == OK` 的**叶子指标**进行评分。
- 比例型测量且方向为 `HIGHER_IS_BETTER` 或 `LOWER_IS_BETTER`、取值范围 `[0,1]` 时：
  - `HIGHER_IS_BETTER`：`score = 100 × value`；
  - `LOWER_IS_BETTER`：`score = 100 × (1 − value)`。
- 0208 父指标不参与评分，仅由其叶子指标聚合。
- 指南未提供权重时，**等权**聚合。

计算公式：

```text
有效评测覆盖率 C = Σ(i∈E) w_i / Σ(i∈A) w_i
已评测维度得分 D_d = Σ(i∈E_d) w_i·s_i / Σ(i∈E_d) w_i
已评测综合得分 S = Σ(i∈E) w_i·s_i / Σ(i∈E) w_i
```

其中：

- `E`：成功且可评分的叶子指标集合；
- `A`：指南适用叶子指标集合（本次为全部 14 个叶子指标，无 `NOT_APPLICABLE`）；
- `w_i`：叶子权重（V1 等权，w_i = 1）。

### 7.2 本次计算

| 指标     | 归一化方向                                         | 原始值       | 归一化得分（0–100） |
| -------- | -------------------------------------------------- | ------------ | -------------------- |
| 0204     | HIGHER_IS_BETTER                                   | 0.9978609626 | 99.7861              |
| 02080103 | LOWER_IS_BETTER（`exact_affected_record_ratio`） | 0.0059619507 | 99.4038              |

等权平均：

```text
S = (99.7861 + 99.4038) / 2 = 99.59495
C = 2 / 14 = 14.2857%
```

### 7.3 雷达图数据

| 维度            | 得分    | 覆盖率   | 说明                               |
| --------------- | ------- | -------- | ---------------------------------- |
| 0201 格式规范性 | N/A     | 0%       | 未评测                             |
| 0202 安全规范性 | N/A     | 0%       | 未评测                             |
| 0203 标注规范性 | N/A     | 0%       | 未评测                             |
| 0204 结构完整性 | 99.7861 | 100%     | 已评测                             |
| 0205 内容真实性 | N/A     | 0%       | 未评测                             |
| 0206 内容一致性 | N/A     | 0%       | 未评测                             |
| 0207 类型一致性 | N/A     | 0%       | 未评测                             |
| 0208 内容干净性 | 99.4038 | 14.2857% | 仅由 02080103 聚合；父子不重复计权 |

雷达图 Mermaid 示意：

> Mermaid 原生 radar 不支持显式 N/A；上图将未评测维度以 0 占位，但实际报告前端以 N/A 样式展示，**不将 0 分与真实 0 分混淆**。

可复现脚本：[`evidence/mysql-jhsjk-article-20261029/radar-v0.1.py`](evidence/mysql-jhsjk-article-20261029/radar-v0.1.py)（读取固定 `quality-report.json`，输出雷达图 SVG）。

### 7.4 得分与覆盖率的解释约束

- `99.59495` **不等于**指南全指标综合质量得分。
- 未评测指标没有被默认为 0 分。
- `ERROR`（执行失败）与 `FAIL`（规则未通过）语义分离；本次无 ERROR/FAIL，但该性质由实现与测试保证。
- 0208 父指标与 02080103 子指标未重复计权。
- 当前结论仅在 14.2857% 的有效覆盖范围内成立。

---

## 8. 主要质量问题与证据样本

### 8.1 问题汇总

| 指标     | 问题类型              | 数量 | 判定规则                                | 证据 / IssueIndex             |
| -------- | --------------------- | ---- | --------------------------------------- | ----------------------------- |
| 0204     | `EMPTY_STRING` 缺失 | 32   | `article.body.plain` 为空字符串       | Evidence artifact（32 条）    |
| 02080103 | 精确重复冗余          | 45   | 归一化整记录完全重复，每组保留 1 条代表 | IssueIndex（45 条 CONFIRMED） |

### 8.2 0204 空字符串样本证据

Evidence 文件每条记录包含：

```json
{
  "missing_category": "EMPTY_STRING",
  "sample_id": "<sample_id_hash>",
  "selector": "article.body.plain",
  "selector_kind": "CONTENT_ROLE"
}
```

示例样本 ID（已脱敏）：`145beb383237cee3d8bd2f71656266b3`、`19d8c6847a6795e308ba60ff257414e1`。

由于正文可能涉及新闻内容，本报告不复制文章原文；可通过 `(dataset_version_id, sample_id)` 在 `sample_records.jsonl` 中定位原始记录。

### 8.3 02080103 精确重复样本证据

重复组 Evidence 示例：

```json
{
  "group_id": "sha256:0075a0cbe78ef9ffe038125b5044f620ee6367b1c0be2c1922c68edbae76c1c6",
  "normalized_sha256": "0075a0cbe78ef9ffe038125b5044f620ee6367b1c0be2c1922c68edbae76c1c6",
  "record_count": 2,
  "representative_sample_id": "9d5f7596e79bd9d2d66304cfc10d9fbc",
  "sample_ids": ["9d5f7596e79bd9d2d66304cfc10d9fbc", "d21939e553b616c735dd9a3e618d3f63"]
}
```

Issue 示例：

```json
{
  "issue_id": "01a1852e-2e9a-7002-aca7-8802b4c7c6f7",
  "code": "EXACT_DUPLICATE_REDUNDANT",
  "severity": "WARNING",
  "determination": "CONFIRMED",
  "status": "OPEN",
  "subject": {"type": "SAMPLE", "sample_id": "e82a74b69b66af12e052b3b09ad080aa"},
  "related_subjects": [{"subject": {"type": "SAMPLE", "sample_id": "2207ac979a67dceabba3429184514bd9"}, "relation": "DUPLICATE_OF"}]
}
```

### 8.4 问题对数据使用的潜在影响

- **空字符串正文**：模型训练/推理时若直接消费 `article.body.plain`，32 条空记录会浪费批次、污染 loss 统计，或在 embedding/分类任务中引入无意义样本。
- **精确重复**：89 条重复记录会放大特定内容的统计权重，冗余副本（45 条）可安全去重以降低存储与训练偏差。

---

## 9. 数据整改建议及优先级

| 优先级         | 对应问题               | 产生原因 / 待验证假设                        | 整改措施                                                                                                                        | 建议验证方式                                                                        |
| -------------- | ---------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| **高**   | 02080103 45 条冗余副本 | 可能为同一文章多次入库、版本未合并或抓取重复 | 按重复组 `group_id` 审核业务语义；确认无版本差异后，保留代表样本、删除或归档冗余副本                                          | 抽样核对组内样本的源表主键/抓取时间；去重后重新发布 Dataset Version 并复跑 02080103 |
| **中**   | 0204 32 条空字符串     | 可能为抓取失败、正文解析失败或合法空值       | 区分空值原因：修复抓取/解析链路，或对合法空值建立业务白名单；修复后发布新 Dataset Version                                       | 通过 `source` 字段定位原始 MySQL 行；人工抽查空值样本                             |
| **后续** | 12 个未评测指标        | 缺阈值、缺业务输入或 Calculator 占位         | 为 0201/02080101/02080102/02080104–02080107 配置 Demo/业务阈值并纳入 Contract；为 0202/0203/0205–0207/0208 补齐实现或业务声明 | 逐指标新增 Run，对比覆盖率与问题发现能力                                            |

> 说明：表中“产生原因”为**待验证假设**，不是已查明的根因。整改前需通过原始源表字段、抓取日志或业务规则进一步确认。

---

## 10. 未评测指标、当前能力与局限性

### 10.1 未评测指标分类

#### A. AVAILABLE 但本次未纳入 Contract（缺 `DEMO_POLICY_V1` 阈值）

| 指标     | Calculator                              | 未执行原因 | 进入 V0.2 所需动作         |
| -------- | --------------------------------------- | ---------- | -------------------------- |
| 0201     | `format_compliance@1.0.0`             | 缺阈值     | 配置格式规则阈值           |
| 02080101 | `text_perplexity@1.0.0`               | 缺阈值     | 配置困惑度阈值             |
| 02080102 | `knowledge_information_density@1.0.0` | 缺阈值     | 配置知识密度阈值           |
| 02080104 | `text_completeness@1.0.0`             | 缺阈值     | 配置长度/截断规则阈值      |
| 02080105 | `information_missing_degree@1.0.0`    | 缺阈值     | 配置关键元素缺失阈值       |
| 02080106 | `text_purity@1.0.0`                   | 缺阈值     | 配置广告/乱码/特殊符号阈值 |
| 02080107 | `text_coherence@1.0.0`                | 缺阈值     | 配置连贯性阈值             |

#### B. UNAVAILABLE（Calculator 占位或未实现）

| 指标                  | 当前状态        | 未来实现路径                           |
| --------------------- | --------------- | -------------------------------------- |
| 0202 安全规范性       | Calculator 占位 | 规则 + 安全模型/语义复核               |
| 0203 标注规范性       | Calculator 占位 | 当前无标注字段，若后续有标注则实现     |
| 0205 内容真实性       | Calculator 占位 | 血缘/外部证据 + 事实核查模型           |
| 0206 内容一致性       | Calculator 占位 | 字段关系 + embedding / 语义判断        |
| 0207 类型一致性       | Calculator 占位 | 数据集类型声明 + 分类器                |
| 0208 内容干净性（父） | Calculator 占位 | 由 02080101–02080107 聚合，不独立执行 |

### 10.2 当前能力边界

- **Agent Runtime**：V1 已实现真实 Session/Skill/Tool 调用、确定性 fallback、人工确认门禁，但缺少持久队列、租约、真正取消、完整预算/重试与多进程恢复。
- **Logical Plan**：已生成 canonical Logical Plan，但依赖 DAG 与失败传播仍为 V1 顺序语义。
- **Quality Issue**：仅 02080103 生成 Issue；0204 缺失证据未提升为 Issue。
- **Provider 兼容性**：`kimi-k2.6` 因 temperature 参数不兼容导致 LLM 路径失败，本次依赖确定性 fallback。
- **前端 UI 验收**：浏览器点击流程 `NOT_EXECUTED`。

### 10.3 有效覆盖率对结论的限制

本次仅 2/14 个指南叶子指标完成评分。因此：

- 可以认为：**在已评测范围内，结构完整性与精确重复程度满足 Demo 阈值**。
- **不能得出**：数据集整体符合《高质量数据集指南》全部要求；格式规范、安全规范、标注规范、真实性、一致性、类型一致性、文本困惑/完整/信息缺失/纯净/连贯等维度均未评测。

---

## 11. 可复现命令、测试结果与案例总结

### 11.1 固定标识与 Artifact

| 项目                        | 值                                                                                  |
| --------------------------- | ----------------------------------------------------------------------------------- |
| Dataset ID                  | `d769d8a6-41c0-4690-98c7-3ef08ae3dd6d`                                            |
| Dataset Version ID          | `01a1852e-189d-74f0-ad95-b00793cd80bc`                                            |
| Profile ID                  | `01a1852e-1d2d-72b3-b5ab-3ecac145be05`                                            |
| Agent Session ID            | `01a1852e-1d4c-743f-9d68-ab9b27b7789c`                                            |
| Proposal Revision ID        | `01a1852e-1db5-7885-aafb-a9efa0ca1ee5`                                            |
| Contract ID / Version ID    | `01a1852e-251f-7584-adab-afe297b99e3f` / `01a1852e-251f-72fe-8f28-504307408481` |
| Logical Plan ID             | `01a1852e-2544-7c2d-be1c-f31950ab4a71`                                            |
| Physical Plan ID            | `01a1852e-2546-7924-abe8-1d775ee3f1f7`                                            |
| Run ID                      | `01a1852e-2546-73ec-8ea9-b7e6b63ca491`                                            |
| Report ID                   | `01a1852e-2ea8-713e-9733-555d6563048a`                                            |
| 原始快照 SHA-256            | `dcc198fc35811a22d0d045177621876cfb5733296326e446d3c31154c142e59f`                |
| 0204 Evidence SHA-256       | `a14a2db43f65f6f45810ae07657ab0c028d4916cf5d9fe4bc5df232b3653ebe6`                |
| 02080103 Evidence SHA-256   | `5e152948c1a2f74a1e2b111b9e69bb2542e55fab83599873886c661c53ae5fce`                |
| 02080103 IssueIndex SHA-256 | `372b8b8913fb4527b4b6777c5d7d10fbe56dcf15c49ad4c8e625b2959954d66e`                |
| Source revision             | `bc684e229e9e372b6b50d5a60dc61f5729f60a5b`                                        |

### 11.2 真实执行命令

本次通过本地 HTTP API 完成端到端执行，等价于前端 AgentEvaluationWorkspace 调用的端点：

```bash
# 1. 启动 PostgreSQL（若未启动）
docker compose --env-file .env -f deploy/docker-compose.yml up -d postgres

# 2. 数据库迁移到 head
uv run alembic upgrade head
QA_EVAL_DATABASE_URL="$QA_EVAL_TEST_DATABASE_URL" uv run alembic upgrade head

# 3. 运行后端测试
uv run pytest -q

# 4. 运行 schema drift 检查
uv run python scripts/contracts/generate_json_schemas.py --check

# 5. 前端 lint / build
npm --prefix apps/web run lint
npm --prefix apps/web run build

# 6. Ruff 检查（当前未通过）
uv run ruff check .
```

> 本案例不依赖重新执行 Run；固定 Run ID `01a1852e-2546-73ec-8ea9-b7e6b63ca491` 的产物是报告的唯一数据来源。

### 11.3 测试结果与质量门

| 检查项                 | 状态                   | 备注                                                             |
| ---------------------- | ---------------------- | ---------------------------------------------------------------- |
| Alembic head           | 通过                   | 开发库与测试库均位于 `e84d9b73a6f1`                            |
| pytest                 | 通过                   | 371 passed，6 skipped，1 warning                                 |
| schema drift           | 通过                   | canonical schema 与代码一致                                      |
| 前端 lint              | 通过                   | —                                                               |
| 前端 build             | 通过                   | 含 bundle-size warning                                           |
| Ruff                   | **未通过**       | 22 项 findings，来自当前未提交的文本困惑度/纯度与 Skill 脚本改动 |
| UI 自动化/人工点击验收 | **NOT_EXECUTED** | 未进行浏览器实际点击验收                                         |

6 个 skipped pytest 均为需 `RUN_MYSQL_INTEGRATION=1` 的 MySQL 直连测试；本案例按方案使用固定 JSONL 快照，不将其记为本次 Run 阻塞项。

### 11.4 案例总结

1. **本次平台成功验证了什么？**

   - 固定 `jhsjk_article` Dataset Version 可完成从 Agent Session → Proposal → Contract → Logical/Physical Plan → Run → MetricResult/Rule/Issue → Quality Report 的完整真实链路。
   - 0204 与 02080103 两个 Calculator 在真实 14,960 条记录上正确执行并生成可追溯 Evidence。
   - 报告能够诚实表达覆盖缺口：已评测得分 99.59495 与有效覆盖率 14.2857% 并列，未评测维度显示 N/A。
2. **真实数据发现了哪些问题？**

   - 32 条 `article.body.plain` 为空字符串；
   - 44 个精确重复组，涉及 89 条记录，其中 45 条为可去重冗余副本。
3. **当前评测结果能支持什么结论？**

   - 在已评测的 2 个叶子指标范围内，数据集满足 Demo 阈值（0204 PASS、02080103 PASS）。
   - 平台已具备端到端真实执行、证据追溯与阶段性报告生成的能力。
4. **哪些结论由于覆盖率不足暂时不能得出？**

   - 不能得出“数据集整体符合《高质量数据集指南》全部要求”的结论。
   - 格式规范、安全规范、标注规范、真实性、一致性、类型一致性以及内容干净性的其他子指标均未评测。
5. **后续扩充指标后如何更新案例？**

   - 以新的 Dataset Version / Contract Version / Run ID 执行，保留 V0.1 固定 Run 作为基线。
   - 新增指标后，按新 Run 的 `quality-report.json` 更新第 6–8 章结果、第 7 章覆盖率与雷达图、第 10 章未评测列表。
   - 报告版本号递增为 V0.2、V0.3 等，并在摘要中对比历次 Run 的得分、覆盖率与问题变化。

---

## 附录 A：可追溯证据索引

| Artifact 类型             | URI / ID                                                                               | SHA-256 / 摘要      | 记录数    |
| ------------------------- | -------------------------------------------------------------------------------------- | ------------------- | --------- |
| 原始快照                  | `data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/raw_snapshot.jsonl` | `dcc198fc...e59f` | 14,960    |
| 标准化 artifact           | `datasets/.../sample_records.jsonl`                                                  | `176caf...a7b3`   | 14,960    |
| 拒绝记录 artifact         | `datasets/.../rejected_records.jsonl`                                                | `e3b0c4...b855`   | 0         |
| Dataset Manifest          | `datasets/.../dataset_manifest.json`                                                 | —                  | —        |
| 0204 Missingness Evidence | `evaluation-runs/{run_id}/{stage}/missingness.jsonl`                                 | `a14a2d...ebe6`   | 32        |
| 02080103 Duplicate Groups | `evaluation-runs/{run_id}/{stage}/exact_duplicate_groups.json`                       | `5e1529...5fce`   | 44 groups |
| 0204 IssueIndex           | `01a1852e-2e98-7d0c-b089-8d0130b7190f`                                               | `e3b0c4...b855`   | 0         |
| 02080103 IssueIndex       | `01a1852e-2ea5-7cb0-b7a9-bc775909c551`                                               | `372b8b...d66e`   | 45        |
| Quality Report            | `01a1852e-2ea8-713e-9733-555d6563048a`                                               | —                  | —        |

---

## 附录 B：报告真实性审查

- [X] 所有数值均能追溯到固定 Run `01a1852e-2546-73ec-8ea9-b7e6b63ca491` 的 `quality-report.json`、数据库记录或 artifact。
- [X] 指标状态区分了 EVALUATED、AVAILABLE 但 NEEDS_INPUT、UNAVAILABLE 三类未执行原因。
- [X] 0204 的 32 条缺失证据、02080103 的 44 个重复组与 45 条 Issue 均有 Evidence / IssueIndex 支撑。
- [X] 已评测得分 99.59495 与有效覆盖率 14.2857% 的计算公式、分母、权重和归一化规则已在第 7 章说明。
- [X] 未评测指标未被误记为 FAIL 或 0 分；雷达图未评测维度为 N/A。
- [X] 报告未夸大平台实现程度：Agent Runtime V1、UI 验收、Ruff 未通过、12 个未评测指标均已披露。
