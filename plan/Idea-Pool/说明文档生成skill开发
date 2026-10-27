# 说明文档生成 Skill 开发方案（已确认）

## 1. 背景与目标

当前已经有 `dataset-document-evaluator` Skill，可依据 0101—0104 指标评测数据集说明文档完整性。下一步单独设计一个说明文档生成 Skill，根据数据集证据和用户提供的业务信息生成 Markdown 说明文档，并在生成后调用评测 Skill 形成闭环。

Skill 建议命名：

```text
dataset-document-generator
```

核心目标：

- 从数据快照、manifest、数据画像和用户输入中整理可验证事实。
- 按 0101—0104 涉及的 20 个方面生成结构完整的说明文档。
- 对无法从数据推导的内容保持诚实，不虚构许可、联系人、业务目标或基准结果。
- 将“最终文档”和“事实来源/待确认项”分开保存，保证生成过程可追溯。
- 生成后调用独立评测 Skill，而不是由生成 Skill 自己给自己打分。

## 2. 与评测 Skill 的职责边界

| 能力 | `dataset-document-generator` | `dataset-document-evaluator` |
| --- | --- | --- |
| 读取数据证据 | 是 | 可选，用于交叉核验 |
| 生成或改写说明文档 | 是 | 否 |
| 判断内容完整性 | 不自行评分 | 是 |
| 输出 0101—0104 分数 | 否 | 是 |
| 记录事实来源 | 是 | 使用这些证据核验 |
| 修改已有说明文档 | 仅在用户明确请求时生成新版本 | 否 |
| 补造缺失业务事实 | 禁止 | 禁止 |

两个 Skill 保持松耦合：生成 Skill 的交付物可以独立使用；如用户要求生成并评测，生成完成后再显式运行 `dataset-document-evaluator`。

## 3. MVP 使用场景

### 3.1 从零生成

数据集已有固定快照和 manifest，但没有说明文档。Skill 基于证据生成一份新的 Markdown 文档，并列出仍需人工补充的事项。

### 3.2 基于已有文档生成新版本

用户已有说明文档，并明确要求补充或重写。Skill 读取原文和新增证据，生成新的版本文件，不覆盖原文件。

### 3.3 生成后评测

用户明确要求“生成并评测”时：

```text
数据证据 + 人工输入
  → 生成说明文档
  → 生成清单与待确认项
  → dataset-document-evaluator
  → 独立评测 JSON / Markdown 报告
```

MVP 优先跑通场景 3.1 和 3.3；场景 3.2 支持最小的新版本生成，不做复杂的逐段合并编辑。

## 4. MVP 范围

### 4.1 包含

- 输入为本地 JSONL 数据快照、JSON manifest、已有数据画像和可选人工信息。
- 输出为 Markdown 数据集说明文档。
- 覆盖 0101—0104 对应的 20 个文档方面。
- 生成确定性数据画像：规模、字段、空值、文本长度、重复等 MVP 所需统计。
- 生成结构化 `generation_manifest.json`，记录正文事实的来源和状态。
- 生成 `unresolved_items.json`，集中记录必须人工确认的事项。
- 支持 `draft` 和 `strict` 两种生成模式。
- 支持生成到新路径，不覆盖已有文档。
- 可选衔接现有评测 Skill。

### 4.2 暂不包含

- 自动连接生产数据库。
- 从互联网搜索或推断许可证、版权归属和组织联系人。
- 自动执行下游模型训练或基准测试。
- PDF、DOCX、HTML 等输出格式。
- 平台上传、发布、审批和权限系统。
- 根据评测分数循环改写直到满分。
- 自动覆盖或原地修改已有说明文档。

## 5. 关键设计原则

### 5.1 事实优先，不以满分为目标

生成文档的目标是准确、透明、可用，不是让完整性评测无条件达到 100 分。缺少真实许可证或基准结果时，应明确标记待确认，而不是生成看似完整的虚构内容。

### 5.2 每项事实必须有来源状态

生成内容中的关键事实分为四类：

| 状态 | 含义 | 是否可以写成确定事实 |
| --- | --- | --- |
| `EVIDENCE_VERIFIED` | 直接来自 manifest、快照或已验证画像 | 是 |
| `DERIVED` | 通过确定性脚本从证据计算得到 | 是，必须记录算法和输入指纹 |
| `USER_PROVIDED` | 用户明确提供的业务事实 | 是，标明由用户提供 |
| `UNRESOLVED` | 没有证据，也没有用户确认 | 否，只能标记待确认或省略 |

AI 根据证据组织文字不产生新的事实来源。措辞润色不能把 `UNRESOLVED` 变成确定事实。

### 5.3 数据可推导与人工信息分离

原则上可从数据或 manifest 推导：

- 样本规模、文件大小、格式、字段结构和文件指纹。
- 模态候选、字段分布、空值、长度和完全重复统计。
- 样本示例（应截断和脱敏）。
- 数据来源、字段映射、导出方式和版本信息，前提是 manifest 中存在。
- 可复现的数据质量评估方法和已实际计算的数据层结果。

通常必须由人工或权威材料提供：

- 使用许可、版权和授权依据。
- 适用部门、人员、用途、传播和再分发范围。
- 技术支持联系人、邮箱、工单渠道和响应承诺。
- 业务目标、正式应用场景和禁止用途。
- 标注组织、人员资质和业务规则（数据中无法证明的部分）。
- 下游模型、固定切分、硬件环境和正式基准测试结果。
- 已落地的生产案例及其效果。

### 5.4 不混淆“没有”与“尚未获得信息”

- 数据扫描确认没有标签字段，可以写“该快照未包含标签字段”，并附统计。
- 用户没有提供许可证，不可以写“本数据集无许可证”，只能写“当前未提供许可证信息，待权利负责人确认”。
- 没有看到基准报告，不可以断言“从未开展基准测试”，只能写“当前证据中未包含基准结果”。
- manifest 不含联系人，不代表项目没有联系人。

### 5.5 原始数据保护

- 不修改数据快照、manifest 或已有说明文档。
- 样本示例默认截断，只展示说明 Schema 所需的最少内容。
- 对疑似个人信息、密钥、连接串和敏感业务字段，默认不复制进说明文档。
- 不读取或输出 `.env`、数据库口令等与文档生成无关的秘密信息。

## 6. 生成模式

### 6.1 `draft`

适合一键生成和开发阶段。

- 使用所有可验证和可推导事实生成文档。
- 对缺少的人工信息使用统一的“待确认”表达。
- 正常产出文档、生成清单和待确认项。
- 文档可以被评测为 `INSUFFICIENT`，这是预期行为，不视为生成失败。

### 6.2 `strict`

适合准备正式发布的文档。

- 生成前检查必需人工信息。
- 如果许可、技术支持方式等发布必需项仍未解决，则不产出“final”文档。
- 可以产出草稿和阻塞清单，但状态必须是 `BLOCKED_BY_MISSING_INPUT`。
- 不自动降低要求，也不把占位文字视为完成。

建议 MVP 默认使用 `draft`；`strict` 的必需人工字段清单应允许项目配置，但不能改变事实真实性约束。

## 7. 输入设计

### 7.1 建议请求结构

```json
{
  "mode": "draft",
  "dataset_id": "policy.jhsjk_article",
  "dataset_version": "20261022T085048Z",
  "snapshot_path": "data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/raw_snapshot.jsonl",
  "manifest_path": "data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/snapshot_manifest.json",
  "profile_path": null,
  "existing_document_path": null,
  "human_inputs_path": "data/dataset-quality-demo/policy.jhsjk_article_human_inputs.json",
  "output_directory": "data/dataset-quality-demo/document-generation",
  "evaluate_after_generation": true
}
```

### 7.2 输入约束

- `dataset_id` 和 `dataset_version` 必填。
- `snapshot_path`、`manifest_path`、`profile_path` 至少提供一种可用证据；MVP 推荐 manifest + snapshot。
- `existing_document_path` 只在生成新版本时提供。
- `human_inputs_path` 可选；没有时按 `UNRESOLVED` 处理，禁止猜测。
- `output_directory` 必填或由用户明确指定；不得写入不相关目录。
- `evaluate_after_generation` 默认建议为 `true`，但调用评测 Skill 是独立阶段。

### 7.3 人工输入建议结构

```json
{
  "technical_support": {
    "owner_department": null,
    "contact_name": null,
    "email": null,
    "issue_tracker": null,
    "service_scope": null
  },
  "license": {
    "name": null,
    "authorization_basis": null,
    "allowed_departments": [],
    "allowed_uses": [],
    "redistribution": null,
    "restrictions": []
  },
  "application": {
    "target_users": [],
    "target_tasks": [],
    "prohibited_uses": [],
    "confirmed_cases": []
  },
  "annotation": {
    "has_annotations": null,
    "guideline_path": null,
    "quality_control": null
  },
  "benchmark": {
    "performed": null,
    "task": null,
    "split": null,
    "method_or_model": null,
    "metric": null,
    "result": null,
    "environment": null,
    "report_path": null
  }
}
```

空值只表示当前没有输入，不等同于该事实不存在。

## 8. 数据画像与确定性计算

AI 不直接凭观察编造统计值。重复使用的统计通过脚本生成结构化 profile。

### 8.1 MVP profile 内容

- 文件路径、字节数、SHA-256 和记录数。
- JSONL 解析成功/失败数。
- 顶层字段和字段出现次数。
- 字段类型分布。
- NULL、空字符串和空白字符串统计。
- 唯一 ID 数和重复 ID 数（需要配置 ID 字段）。
- 文本字符长度的最小值、最大值、均值、中位数和固定分桶。
- 完全文本重复组数和额外重复记录数。
- 截断后的代表样本。

### 8.2 性能与安全约束

- 采用流式扫描 JSONL，不将完整快照一次性载入内存。
- 统计算法和参数写入 profile。
- 代表样本默认限制长度，避免把整篇正文复制进产物。
- 对大文件扫描前显示输入文件和预计操作；不访问未指定路径。
- 近似重复、语言识别、主题推断和敏感信息识别不属于 MVP，除非已有可靠画像证据。

## 9. 文档模板

MVP 默认模板建议包含以下章节：

```text
# <数据集名称> 数据集说明

## 1. 数据集概述
### 1.1 基本信息
### 1.2 文件与字段结构
### 1.3 访问渠道与技术支持

## 2. 内容特征
### 2.1 模态与语言
### 2.2 数据分布
### 2.3 标签类别统计
### 2.4 样本示例
### 2.5 局限性

## 3. 建设过程
### 3.1 数据来源与采集方法
### 3.2 加工处理流程
### 3.3 标注规范
### 3.4 版本控制记录

## 4. 应用说明
### 4.1 使用许可
### 4.2 目标应用场景
### 4.3 评估方法
### 4.4 基准测试结果
### 4.5 典型应用案例

## 5. 待确认事项与维护
```

模板用于保证组织结构，不保证相应检查项一定满足。内容缺失时必须写明证据状态，不能为了填满章节而生成无依据事实。

## 10. 输出设计

建议固定输出：

```text
<output_directory>/
├── dataset_document.generated.md
├── dataset_profile.json
├── generation_manifest.json
├── unresolved_items.json
└── evaluation/                       # 仅 evaluate_after_generation=true
    ├── evaluation_result.json
    └── evaluation_report.md
```

### 10.1 `dataset_document.generated.md`

面向数据使用者的说明文档。正文保持可读，不把大量内部生成元数据混入正文。

### 10.2 `dataset_profile.json`

确定性脚本计算的数据画像，包括输入指纹、算法参数、统计值和错误记录。

### 10.3 `generation_manifest.json`

生成过程的 canonical 记录，建议结构：

```json
{
  "schema_version": "1.0",
  "run": {
    "run_id": "document-generation-...",
    "generated_at": "...",
    "mode": "draft"
  },
  "dataset": {
    "dataset_id": "policy.jhsjk_article",
    "dataset_version": "20261022T085048Z"
  },
  "inputs": [
    {
      "path": ".../snapshot_manifest.json",
      "sha256": "...",
      "kind": "manifest"
    }
  ],
  "output_document": {
    "path": ".../dataset_document.generated.md",
    "sha256": "..."
  },
  "facts": [
    {
      "fact_id": "dataset.row_count",
      "value": 14960,
      "status": "EVIDENCE_VERIFIED",
      "source_path": ".../snapshot_manifest.json",
      "locator": "$.row_count",
      "used_in_sections": ["1.1", "2.2"]
    },
    {
      "fact_id": "license.authorization_basis",
      "value": null,
      "status": "UNRESOLVED",
      "source_path": null,
      "locator": null,
      "used_in_sections": ["4.1"]
    }
  ],
  "status": "GENERATED_WITH_UNRESOLVED_ITEMS"
}
```

### 10.4 `unresolved_items.json`

每条待确认项至少包含：

- `item_id`
- 对应文档章节和 0101—0104 检查项
- 缺失信息说明
- 为什么无法从数据推导
- 需要谁提供
- 建议问题
- 是否阻塞 `strict` 输出

示例：

```json
{
  "item_id": "license.scope",
  "metric_item": "0104-01",
  "section": "4.1 使用许可",
  "reason": "许可适用部门和再分发范围不能从数据快照推导",
  "requested_from": "数据权利或合规负责人",
  "question": "该数据允许哪些部门、人员和用途使用，是否允许向第三方再分发？",
  "blocks_strict_output": true
}
```

## 11. 生成状态模型

| 状态 | 含义 |
| --- | --- |
| `GENERATED` | 文档已生成，没有未解决项 |
| `GENERATED_WITH_UNRESOLVED_ITEMS` | 草稿已生成，同时存在待确认项 |
| `BLOCKED_BY_MISSING_INPUT` | strict 模式缺少必需人工输入，不能生成 final 文档 |
| `ERROR` | 输入解析、画像计算或写入失败 |

`GENERATED_WITH_UNRESOLVED_ITEMS` 不是失败，也不代表对应文档可以正式发布。

## 12. 生成与评测闭环

生成 Skill 不读取评测结果后自动反复润色到满分。建议闭环为：

1. 生成文档、profile、generation manifest 和 unresolved items。
2. 调用 `dataset-document-evaluator` 完成一次独立评测。
3. 将 `AUTO_FIXABLE` 问题作为候选修复，不自动执行。
4. 将 `DATA_DERIVABLE` 问题反馈给画像阶段，补充确定性证据后再生成新版本。
5. 将 `REQUIRES_HUMAN_INPUT` 问题转换为人工问题，不允许 AI 填写答案。
6. 用户补充信息后显式生成新版本，再次独立评测。

每次重新生成都应产生新的 run ID、文档指纹和 generation manifest，不覆盖上一版本。

## 13. Skill 目录建议

```text
.agents/skills/dataset-document-generator/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── document-template.md
│   ├── input-schema.md
│   └── output-schema.md
└── scripts/
    ├── profile_jsonl.py
    └── validate_generation.py
```

职责：

- `SKILL.md`：输入选择、生成模式、事实来源规则、执行流程和禁止编造边界。
- `document-template.md`：章节结构、各章节允许使用的事实类型和占位规则。
- `input-schema.md`：生成请求、人工输入和证据输入约定。
- `output-schema.md`：profile、generation manifest 和 unresolved items 协议。
- `profile_jsonl.py`：流式、确定性生成 JSONL 数据画像。
- `validate_generation.py`：验证输入/输出指纹、事实来源、未解决项、禁止字段和路径关系；不判断文案质量。

不建议将评测 rubric 复制进生成 Skill。生成完成后的完整性判断仍由现有 `dataset-document-evaluator` 负责，避免两套规则漂移。

## 14. 建议执行流程

1. 校验用户指定的输入和输出路径。
2. 读取 manifest、已有 profile 和可选人工输入，计算文件指纹。
3. 若需要，从 JSONL 快照流式生成 profile。
4. 建立事实表，为每项事实标记来源状态和 locator。
5. 根据生成模式检查 unresolved items。
6. 按模板生成 Markdown 草稿或新版本。
7. 写入 generation manifest 和 unresolved items。
8. 运行 `validate_generation.py`，校验事实追溯和输出一致性。
9. 若用户要求评测，调用 `dataset-document-evaluator` 生成独立评测产物。
10. 返回输出路径、生成状态、待确认项和评测摘要。

## 15. MVP 测试方案

### 15.1 主测试数据

```text
data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/raw_snapshot.jsonl
data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/snapshot_manifest.json
```

现有说明文档只用于对照，不应作为从零生成模式的事实输入：

```text
data/dataset-quality-demo/policy.jhsjk_article_数据集说明.md
```

### 15.2 预期行为

- profile 中记录数和 SHA-256 与 manifest 一致。
- 使用流式方式处理 14,960 条 JSONL，不修改快照。
- 生成文档包含完整章节结构、真实统计和截断样本。
- 没有人工输入时，技术支持、正式许可和下游基准结果进入 unresolved items。
- 不把“证据中未发现许可证”改写为“数据集无许可证”。
- `draft` 模式成功输出 `GENERATED_WITH_UNRESOLVED_ITEMS`。
- `strict` 模式在必需人工字段缺失时输出 `BLOCKED_BY_MISSING_INPUT`，不冒充 final 文档。
- generation manifest 中每个关键事实都有状态和来源定位。
- 输入和输出文件均记录 SHA-256。
- 生成后能够被 `dataset-document-evaluator` 正常读取和评测。
- 评测不足项可以追溯到对应 unresolved item，而不是被无依据自动修复。
- 原始快照、manifest 和已有说明文档没有变化。

### 15.3 负向测试

- JSONL 存在解析错误。
- manifest 行数或指纹与快照不一致。
- ID 字段或文本字段配置错误。
- 人工输入 JSON 格式错误。
- 人工输入与 manifest 事实冲突。
- 输出路径与输入文件相同，存在覆盖风险。
- strict 模式缺少许可或支持信息。
- 样本包含超长文本或疑似敏感字段。
- generation manifest 包含没有来源状态的关键事实。

所有错误应与未解决业务信息区分：技术执行错误使用 `ERROR`，缺少人工事实使用 `UNRESOLVED` 或 `BLOCKED_BY_MISSING_INPUT`。

## 16. MVP 验收标准

- Skill 能使用主测试数据生成一份新的 Markdown 文档。
- 数据规模、格式、字段、分布和示例均来自可追溯证据或确定性计算。
- 所有不可推导的内容均进入 unresolved items，没有虚构事实。
- generation manifest 可定位每项关键事实的来源、输入指纹和使用章节。
- `validate_generation.py` 能识别无来源事实、指纹不一致和覆盖风险。
- 生成文档通过现有评测 Skill 跑通 0101—0104 评测。
- 输出路径稳定，重复运行不覆盖旧版本。
- Skill 结构通过官方快速校验，脚本通过成功与失败路径测试。

## 17. 已确认的产品决策

### 17.1 无人工输入时的正文表达

已确认：在 `draft` 模式下保留对应章节，并明确写“待权利/业务负责人确认”，同时在 `unresolved_items.json` 中记录，不完全省略章节。占位内容不得伪装为已满足事实。

### 17.2 strict 模式的默认阻塞项

已确认默认阻塞项至少包括：

- 0101-05 技术支持方式。
- 0104-01 使用许可。

目标应用场景必须有用户、配置或项目文档依据，但不作为所有数据集的 strict 默认阻塞项。典型案例不作为默认阻塞项。基准测试仅在数据集声明用于模型研发、模型选型、性能比较或对外发布相关性能结论时成为阻塞项。

### 17.3 目标应用场景的来源

已确认采用严格口径：只有用户请求、配置文件或项目说明中明确表达时，目标应用场景才能标为 `USER_PROVIDED`。仅从目录名、表名、数据内容或常见用途猜测时保持 `UNRESOLVED`，不得写成确定用途。

### 17.4 数据画像范围

已确认 MVP 只内置基础 JSONL profile，不做语言识别、主题分类、近似重复和敏感信息模型检测，也不默认生成语言分布或发布时间分布。后续只有在存在可靠字段、确定性算法和明确统计口径时再扩展。

### 17.5 输出版本命名

已确认使用带时间戳的目录：

```text
data/dataset-quality-demo/document-generation/<run_timestamp>/
```

文档使用固定文件名 `dataset_document.generated.md`，由目录区分版本，避免覆盖。时间戳采用 UTC 的 `YYYYMMDDTHHMMSSZ` 格式；同一秒发生命名冲突时停止并报告，不覆盖已有目录。

## 18. 正式开发顺序

1. 创建 `.agents/skills/dataset-document-generator` Skill 骨架和 UI 元数据。
2. 固化文档模板、输入协议和输出协议。
3. 实现流式 `profile_jsonl.py`，并验证主测试快照的真实统计。
4. 实现 `validate_generation.py`，覆盖事实来源、指纹、路径覆盖和 strict 阻塞规则。
5. 以无人工输入的 `draft` 模式生成第一版真实文档及配套产物。
6. 调用 `dataset-document-evaluator` 完成独立评测，核对不足项与 unresolved items 的映射。
7. 验证 `strict`、输入冲突、坏 JSONL、指纹不一致和输出目录冲突等失败路径。
8. 运行 Skill 官方结构校验和脚本测试，提交产物路径与验收摘要。
