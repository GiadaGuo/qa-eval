# qa-eval Canonical Data-flow Models v1

- 状态：Frozen baseline
- 日期：2026-10-27
- 关联决策：[ADR-0001](../adr/0001-skill-metric-ownership-and-shared-evaluation-layers.md)、[ADR-0002](../adr/0002-adopt-canonical-data-flow-models-v1.md)
- 验证基线：`policy.jhsjk_article` 与数据质量指标 `02080103`

## 1. 目标与边界

本文定义 qa-eval 从数据集注册到质量报告的 canonical data-flow models。它们由 qa-eval 平台拥有，三个评测 Skill 只生产或消费这些对象的实例。

设计必须同时满足：

1. 当前 `jhsjk_article` 的 HTML、纯文本、标题、摘要、来源和时间字段评测。
2. 文本、图像、音频、视频和结构化内容组成的单模态或多模态样本。
3. 分类、抽取、偏好数据、单轮/多轮 SFT 等带标注数据。
4. 指标定义、实现绑定、计划、运行、重试、结果、证据、规则、问题和报告的完整追踪。
5. 高基数明细存文件/对象存储，平台数据库保存可查询的控制面与聚合事实。

本文不规定具体 ORM、队列和对象存储产品，也不把当前 Demo 文件格式直接提升为正式协议。

## 2. 当前冲突盘点

### 2.1 数据集与标准化模型

| 主题                | RD                                                           | Python domain                                                                  | Skill/真实产物                      | 冲突与风险                                | Canonical 决策                                                                                           |
| ------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------ | ----------------------------------- | ----------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Dataset 标识        | UUIDv7、owner、稳定身份                                      | UUIDv4 默认值，无 owner                                                        | 无 Dataset ID                       | 无法按租户唯一、标识规则不一致            | API 统一 UUID 字符串；生成策略为 UUIDv7；Dataset 增加 owner/namespace                                    |
| DatasetVersion 版本 | `version_no`、发布后不可变、含 manifest/raw/normalized URI | `version: str`，仅基础状态                                                   | 时间戳目录，没有 version ID         | 运行不能稳定关联数据版本                  | 采用不可变 `dataset_version_id`；人类版本号保留为 `version_label`                                    |
| Manifest 模态       | 单值 `modality`                                            | `modalities: list[str]`                                                      | 没有逻辑数据集模态                  | RD 无法表达混合模态                       | 使用去重、非空 `modalities[]`                                                                          |
| Manifest 文件       | 单个 `source.uri/sha256`                                   | `files[]`，但缺 artifact kind                                                | `file + sha256 + snapshot_format` | 无法区分 raw、normalized、reject、profile | 统一 `ArtifactRef[]`，每个引用声明 kind、format、checksum、schema                                      |
| 数据源快照          | 只描述文件                                                   | 未表达一致性                                                                   | 有事务、引擎、排序、行数            | 丢失 MySQL 快照可重复性信息               | Manifest 增加 `source_snapshot`，数据源专有细节放 `details`                                          |
| 字段映射            | 平坦目标路径                                                 | `core/metadata/time/other` 分类；另一个 adapter 使用未定义 `MappingConfig` | 02080103 只有 `sample_id/text`    | 分类式映射不能表达内容角色、标注和多模态  | 使用版本化 `FieldMappingSpec`，每条映射包含 source path、target kind、role、类型和缺失策略             |
| Sample 内容         | `content.text` 单字段                                      | `data_content[]`                                                             | 顶层 `text`                       | 三种不兼容输入                            | Canonical 使用 `contents[]`；禁止 Calculator 自动猜形状                                                |
| 内容载荷            | 仅文本                                                       | `content` 或 `uri` 两个可同时存在                                          | 仅字符串                            | 无法可靠校验 modality/payload             | 使用判别联合 `InlineTextPayload`、`AssetPayload`、`StructuredPayload`                              |
| NULL 语义           | 未明确                                                       | Adapter 把核心 NULL 转成 `""`                                                | 快照另存 `text_is_null`           | 0204/02080105 计算失真                    | ContentItem 显式 `presence=PRESENT/NULL/MISSING`；空串仍是 PRESENT                                     |
| 标注                | `annotations.label` dict                                   | 任意 `dict[str, Any]`                                                        | 无标注                              | 无法表达多任务、多标注者、偏好对和 SFT    | 使用 `Annotation[]`，带 task、target、value、provenance                                                |
| 时间                | 混在 metadata                                                | 独立 `timestamps`，允许无时区字符串                                          | 未进入快照                          | 时间语义和时区不可校验                    | 通用时间仍在 metadata/attributes；业务关键时间使用 typed `TemporalValue`，保留 source timezone/unknown |
| Source/Lineage      | 基本来源 + adapter 合并字符串                                | 结构较好，但字段不统一                                                         | 每行重复 database/table             | lineage 粒度和命名不一致                  | `source` 负责原始定位；`lineage.transforms[]` 记录适配链；大部分公共来源放 Manifest                  |
| Reject              | 规定独立文件                                                 | 无模型                                                                         | Calculator 内用 ERROR feature       | 标准化错误与质量异常混淆                  | 新增 `RejectedRecord`，标准化失败不得进入 MetricResult                                                 |

### 2.2 指标、执行与结果模型

| 主题              | RD                                       | Skill/02080103                                    | 冲突与风险                         | Canonical 决策                                                                            |
| ----------------- | ---------------------------------------- | ------------------------------------------------- | ---------------------------------- | ----------------------------------------------------------------------------------------- |
| Metric 标识       | 示例使用语义 key                         | 指南编码 `02080103`                             | 代码和语义 key 可能分叉            | `metric_key` 是稳定 key；`standard_refs[]` 保存指南编码，可直接以编码作为首批 key     |
| Metric 输出       | 单个 type/direction                      | 同时输出 exact、near、paragraph、n-gram 多个值    | Contract 不知道阈值针对哪个值      | Metric Definition 声明命名 `measurements[]`；Contract 必须引用 measurement key          |
| Calculator        | 计划 descriptor/entry point              | YAML 与 Definition 混合，Python 常量定义版本      | 无法独立注册和替换实现             | 独立 `CalculatorDescriptor`，与 Metric Definition 多对多绑定                            |
| 参数              | Contract parameters + Physical Plan 固化 | CLI 默认值，Result 未完整保存                     | 重跑不可证明配置一致               | Physical node 保存 resolved parameters；StageRun 保存其 fingerprint；Result 引用 StageRun |
| Run/Stage         | RD 有状态机和重试                        | 只有目录名/输入指纹                               | 无 run_id、attempt、时间和错误事实 | 引入 `EvaluationRun`、`StageRun`；结果必须引用二者                                    |
| MetricResult 值   | `value_num/text/json` 三选一           | 一个 JSON 中混合汇总、参数和算法元数据            | 事实、配置、统计混杂               | Result envelope + typed `observations[]`；配置留在 PhysicalPlan/StageRun                |
| Result 状态       | OK/NOT_APPLICABLE/MISSING/ERROR          | 无顶层状态；sample feature 使用 VALID/EMPTY/ERROR | 状态层级混淆                       | Result 只用 OK/NOT_APPLICABLE/MISSING/ERROR；样本处理状态属于证据 schema                  |
| 部分覆盖          | 未建模                                   | `coverage=PARTIAL...` 字符串                    | 无法汇总或比较                     | 独立 `SemanticCoverage(level, covered, excluded, limitations)`；不冒充执行状态          |
| Evidence          | 单一 `evidence_uri`                    | 两个大文件及重复的 `groups/exact_groups`        | 不知道 schema、hash、记录数        | `EvidenceArtifact[]`，每项带 artifact、kind、schema_ref、checksum、counts               |
| Rule/Issue/Report | RD 只有表字段和概念                      | Skill 不再拥有                                    | 尚无可交换模型                     | 平台定义独立对象，均引用不可变上游 ID，不复制或重算事实                                   |

### 2.3 文档与代码自身的不一致

- RD Sample Record 的 `content.text` 与现有 Python `data_content[]` 不兼容。
- Python `SampleRecord.schema_version` 接受 `1.0/1.1`，但没有迁移器，也没有对应 JSON Schema。
- `GenericMappingAdapter` 引用了 `dataset_adapter.py` 中不存在的 `MappingConfig`。
- MySQL Adapter 把核心字段 `NULL` 转成空字符串，破坏缺失语义。
- RD 要求 UUIDv7，现有实体和 ORM 使用 UUIDv4。
- RD 表设计的 DatasetVersion 字段与现有 ORM 不一致：缺少 manifest URI、raw fingerprint、normalized URI。
- 现有 `snapshot_manifest.json` 是物理快照清单，不符合 Python `DatasetManifest`。
- 当前 Calculator 支持 `auto/raw/sample-record` 三种输入猜测，不适合作为平台 Calculator 边界。
- `metric_result.json` 没有 dataset version、run、stage、metric definition version、完整参数和证据引用。

## 3. 总体数据流

```text
Raw Source
  -> Dataset + DatasetVersion
  -> DatasetManifest + SourceSnapshot + ArtifactRef(raw)
  -> NormalizationRun
  -> SampleRecord[] + RejectedRecord[] + ArtifactRef(normalized/rejected)
  -> DatasetProfile
  -> QualityContractVersion
  -> LogicalEvaluationPlan
  -> PhysicalEvaluationPlan
  -> EvaluationRun
       -> StageRun[]
       -> MetricResult[] + EvidenceArtifact[]
  -> RuleEvaluation[]
  -> QualityIssue[]
  -> QualityReport
```

Definition/descriptor 是注册表输入，不是数据集流水线的前一阶段：

```text
MetricDefinition + CalculatorDescriptor + Binding
                         |
QualityContract ---------+--> Planner --> PhysicalPlan
```

## 4. 共享基础类型

### 4.1 标识、时间和版本

- 所有 ID 在 JSON 中是 UUID 字符串；平台生成新 ID 时采用 UUIDv7。
- 所有平台时间为带时区 RFC 3339，持久化为 UTC。
- 每个交换对象都有 `schema_version`；对象业务版本另设 `version` 或 `version_label`。
- 已发布 DatasetVersion、QualityContractVersion、Plan 和运行事实不可原地修改。
- JSON 对象默认拒绝未知字段；扩展信息只能进入明确的 `attributes`/`details` 容器。

### 4.2 ArtifactRef

```json
{
  "artifact_id": "uuid",
  "kind": "RAW_DATA",
  "uri": "s3://qa-eval/datasets/.../raw/source.jsonl",
  "format": "jsonl",
  "media_type": "application/x-ndjson",
  "schema_ref": "qa-eval://schemas/raw/mysql-row/1.0",
  "size_bytes": 123,
  "record_count": 14960,
  "checksum": {"algorithm": "sha256", "value": "64-lowercase-hex"}
}
```

`kind` 首批支持 `RAW_DATA`、`DATASET_MANIFEST`、`NORMALIZED_DATA`、`REJECTED_RECORDS`、`PROFILE`、`STAGE_OUTPUT`、`METRIC_EVIDENCE`、`ISSUE_INDEX`、`REPORT`。URI 可以是本地相对路径或对象存储 URI，但正式发布后必须稳定且可校验。

### 4.3 TemporalValue

业务时间必须保留源时区知识状态，禁止把无时区 MySQL `DATETIME` 擅自解释为 UTC：

```json
{
  "value": "2026-10-01T10:00:00",
  "precision": "SECOND",
  "source_timezone": null,
  "timezone_status": "UNKNOWN"
}
```

`timezone_status` 为 `KNOWN/UNKNOWN/NOT_APPLICABLE`；只有 KNOWN 才允许在标准化时转换为 UTC。日期型字段可以使用 `precision=DAY`，且 timezone status 为 NOT_APPLICABLE。

## 5. 数据集与标准化模型

### 5.1 Dataset

稳定逻辑身份，不保存某一版本的文件和映射：

```json
{
  "schema_version": "1.0",
  "dataset_id": "550e8400-e29b-41d4-a716-446655440000",
  "namespace": "default",
  "owner_id": "uuid-or-service-principal",
  "name": "policy.jhsjk_article",
  "description": "政策文章数据集",
  "created_at": "2026-10-22T08:50:48Z"
}
```

### 5.2 DatasetVersion

```json
{
  "schema_version": "1.0",
  "dataset_version_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
  "dataset_id": "550e8400-e29b-41d4-a716-446655440000",
  "version_label": "2026-10-22",
  "status": "PUBLISHED",
  "manifest_ref": {"uri": ".../manifest.json", "checksum": {"algorithm": "sha256", "value": "..."}},
  "content_fingerprint": "sha256:...",
  "created_at": "2026-10-22T08:50:48Z",
  "published_at": "2026-10-22T09:00:00Z"
}
```

状态为 `DRAFT/VALIDATING/PUBLISHED/FAILED/RETIRED`。PUBLISHED 后内容、Manifest 和 fingerprint 不可变。

### 5.3 DatasetManifest 2.0

现有两种 Manifest 草案均不兼容，正式收敛时使用 `2.0`，避免把旧 `1.x` 误认为同一协议。

```json
{
  "schema_version": "2.0",
  "dataset_id": "550e8400-e29b-41d4-a716-446655440000",
  "dataset_version_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
  "name": "policy.jhsjk_article",
  "modalities": ["text"],
  "source_snapshot": {
    "source_type": "mysql",
    "source_uri": "mysql://policy/jhsjk_article",
    "captured_at": "2026-10-22T08:50:48Z",
    "consistency": "REPEATABLE_READ_CONSISTENT_SNAPSHOT",
    "record_count": 14960,
    "details": {
      "table_engine": "InnoDB",
      "ordering": {"fields": ["id"], "direction": "ASC", "pagination": "keyset"}
    }
  },
  "artifacts": [],
  "field_mapping": {
    "schema_version": "2.0",
    "adapter": {"key": "mysql_record", "version": "2.0.0"},
    "mappings": []
  },
  "declared_context": {
    "languages": ["zh"],
    "task_types": ["generic_corpus"]
  },
  "governance": {},
  "created_at": "2026-10-22T08:50:48Z"
}
```

Manifest 保存声明和 artifact 引用；动态统计进入 DatasetProfile，不在 Manifest 中反复更新。

### 5.4 FieldMappingSpec 2.0

每条映射是显式规则，不再仅按 `core/metadata/time/other` 分类：

```json
{
  "source_path": "$.plain_text_content",
  "action": "MAP",
  "target": {"kind": "CONTENT", "role": "article.body.plain", "modality": "text"},
  "value_type": "string",
  "presence_policy": "PRESERVE",
  "required_for_normalization": false
}
```

`action` 支持：

- `MAP`：必须提供 target；`target.kind` 为 `SAMPLE_ID/CONTENT/ANNOTATION/METADATA/SOURCE_ATTRIBUTE/TEMPORAL`。
- `PASSTHROUGH`：明确保留原值到受控扩展容器，并记录原字段名；不能冒充已有 canonical 语义。
- `IGNORE`：用户明确不让该字段进入 SampleRecord。

前端“未配置”只是 Draft Mapping 的编辑状态，发布 FieldMappingSpec 时不允许存在；它不等于 PASSTHROUGH 或 IGNORE。只有无法形成稳定 sample ID、记录无法解析或违反 canonical schema 才进入 reject；普通内容 NULL 是可评测的数据事实。

### 5.5 SampleRecord 2.0

```json
{
  "schema_version": "2.0",
  "dataset_version_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
  "sample_id": "source-stable-id",
  "contents": [
    {
      "content_id": "body-plain",
      "modality": "text",
      "role": "article.body.plain",
      "presence": "PRESENT",
      "payload": {
        "type": "inline_text",
        "text": "文章正文",
        "format": "plain",
        "language": "zh"
      },
      "attributes": {}
    }
  ],
  "annotations": [],
  "metadata": {},
  "source": {
    "source_artifact_id": "uuid",
    "source_record_id": "...",
    "source_row": 42,
    "locator": "mysql://policy/jhsjk_article#id=...",
    "attributes": {}
  },
  "lineage": {
    "input_fingerprint": "sha256:...",
    "transforms": [
      {"key": "mysql_record", "version": "2.0.0", "config_fingerprint": "sha256:..."}
    ]
  }
}
```

#### Content payload 判别联合

- `inline_text`：`text`、`format=plain/html/markdown/code`、可选 language/encoding。
- `asset`：`uri`、`mime_type`、checksum、size_bytes，以及图像尺寸、音视频时长等技术元数据。
- `structured`：JSON-compatible `data` 和必需的 `schema_ref`。

约束：

- `content_id` 在样本内唯一。
- `presence=PRESENT` 时必须有 payload；`NULL/MISSING` 时不得伪造空 payload。
- `""` 是 PRESENT 的文本，不等同 NULL。
- `role` 是版本化语义角色，例如 `article.title`、`article.body.html`、`article.body.plain`、`sft.message`；指标通过 role selector 取输入。
- 非文本二进制不内嵌 SampleRecord，使用 `asset` URI 和 checksum。

#### jhsjk_article 映射

| 源字段                   | Canonical 位置                                        |
| ------------------------ | ----------------------------------------------------- |
| `id`                   | `sample_id`、`source.source_record_id`            |
| `article_id`           | `metadata.article_id`                               |
| `title`                | content role `article.title`                        |
| `summary`              | content role `article.summary`                      |
| `content`              | content role `article.body.html`，format `html`   |
| `plain_text_content`   | content role `article.body.plain`，format `plain` |
| `url`、`origin_name` | metadata/source attributes                            |
| `pub_date`             | `metadata.published_at` typed temporal value        |
| `create_time`          | `metadata.source_created_at` typed temporal value   |

### 5.6 Annotation

```json
{
  "annotation_id": "ann-1",
  "task": {"key": "topic_classification", "version": "1.0"},
  "targets": [{"content_id": "body-plain"}],
  "value": {"type": "categorical", "labels": ["policy"]},
  "provenance": {
    "method": "HUMAN",
    "annotator_ids": ["hashed-or-internal-id"],
    "tool": "annotation-platform",
    "created_at": "2026-10-01T10:00:00Z"
  },
  "status": "FINAL",
  "attributes": {}
}
```

`value.type` 首批允许 `categorical`、`span`、`bounding_box`、`sequence`、`preference`、`structured`。这比自由 dict 更适合 0203 标注规范性和 0304 标注准确性评测。

#### SFT 表示

SFT 消息仍使用 `contents[]`，每个 message 是一个 content item：

```json
{
  "content_id": "turn-1-user",
  "modality": "text",
  "role": "sft.message",
  "presence": "PRESENT",
  "payload": {"type": "inline_text", "text": "用户问题", "format": "plain", "language": "zh"},
  "attributes": {"turn_index": 1, "speaker": "user"}
}
```

同一轮的图片和文本使用相同 `turn_index`，通过不同 content item 表达；监督范围、loss mask、chosen/rejected 等训练标注进入 `annotations[]`。不要把整个多轮对话压成一个不可寻址字符串。

### 5.7 NormalizationRun

NormalizationRun 记录某个 DatasetVersion 的一次标准化执行；它不是 EvaluationRun，也不包含质量结论：

```json
{
  "schema_version": "1.0",
  "normalization_run_id": "uuid",
  "dataset_version_id": "uuid",
  "adapter": {
    "key": "mysql_record",
    "version": "2.0.0",
    "code_digest": "sha256:...",
    "config_fingerprint": "sha256:..."
  },
  "input_artifact_ids": ["uuid"],
  "status": "SUCCEEDED",
  "counts": {"input": 14960, "normalized": 14960, "rejected": 0},
  "output_artifact_ids": ["uuid"],
  "started_at": "2026-10-22T08:50:48Z",
  "finished_at": "2026-10-22T08:51:48Z",
  "error": null
}
```

状态为 `PENDING/RUNNING/SUCCEEDED/PARTIAL_SUCCEEDED/FAILED/CANCELED`。同一 DatasetVersion 可以因 adapter/config 升级产生多次 NormalizationRun，但只有被 DatasetVersion 发布记录选定的 immutable normalized artifact 才进入正式评测。

### 5.8 RejectedRecord

```json
{
  "schema_version": "1.0",
  "dataset_version_id": "uuid",
  "normalization_run_id": "uuid",
  "source": {"source_artifact_id": "uuid", "source_row": 42, "source_record_id": null},
  "error": {"code": "MISSING_SAMPLE_ID", "message": "...", "json_pointer": "$.id", "retryable": false},
  "redacted_excerpt": null,
  "created_at": "2026-10-22T08:50:48Z"
}
```

### 5.9 DatasetProfile

Profile 是带算法版本的事实快照，不作 PASS/FAIL：

```json
{
  "schema_version": "1.0",
  "profile_id": "uuid",
  "dataset_version_id": "uuid",
  "profiler": {"key": "base_profile", "version": "1.0.0", "config_fingerprint": "sha256:..."},
  "status": "OK",
  "summary": {"source_record_count": 14960, "normalized_record_count": 14960, "rejected_record_count": 0},
  "field_profiles": [],
  "artifacts": [],
  "created_at": "2026-10-22T09:00:00Z"
}
```

## 6. 指标注册模型

### 6.1 MetricDefinition 1.0

```json
{
  "schema_version": "1.0",
  "metric_key": "02080103",
  "version": "1.0.0",
  "name": "重复内容程度",
  "dimension": "DATA_QUALITY",
  "parent_metric_key": "0208",
  "standard_refs": [{"standard": "高质量数据集指南", "code": "02080103"}],
  "supported_scopes": ["DATASET"],
  "applicability": {"modalities": ["text"], "required_roles": ["article.body.plain"]},
  "measurements": [
    {"key": "exact_affected_record_ratio", "value_type": "NUMBER", "unit": "ratio", "minimum": 0, "maximum": 1, "direction": "LOWER_IS_BETTER"},
    {"key": "exact_redundant_record_ratio", "value_type": "NUMBER", "unit": "ratio", "minimum": 0, "maximum": 1, "direction": "LOWER_IS_BETTER"},
    {"key": "near_duplicate_record_ratio", "value_type": "NUMBER", "unit": "ratio", "minimum": 0, "maximum": 1, "direction": "LOWER_IS_BETTER"}
  ],
  "persistence_policy": "AGGREGATE_AND_EVIDENCE",
  "definition": {"requirement": "...", "limitations": []}
}
```

Metric Definition 不包含算法阈值、LSH 参数或质量合格阈值。

### 6.2 CalculatorDescriptor 1.0

```json
{
  "schema_version": "1.0",
  "calculator_key": "text_duplicate_degree",
  "version": "2.0.0",
  "entrypoint": "qa_eval.calculators:text_duplicate_degree",
  "supported_metrics": [{"metric_key": "02080103", "metric_version_range": ">=1.0.0,<2.0.0"}],
  "input_contract": {"sample_record_schema": "2.0", "role_selectors": ["article.body.plain"]},
  "parameter_schema_ref": "qa-eval://schemas/calculators/text-duplicate-degree/2.0.0/parameters",
  "output_schema_ref": "qa-eval://schemas/metric-result/1.0",
  "deterministic": true,
  "capabilities": {"modalities": ["text"], "scopes": ["DATASET"], "produces_evidence": true},
  "code_digest": "sha256:..."
}
```

Metric/Calculator Binding 单独保存优先级、兼容约束和启用状态。

## 7. 契约、计划与运行模型

### 7.1 QualityContractVersion

Contract version 是发布后不可变的完整对象；每个 item 必须选择 measurement，避免 02080103 多观测值歧义：

```json
{
  "schema_version": "1.0",
  "contract_id": "uuid",
  "contract_version_id": "uuid",
  "version_label": "1.0.0",
  "name": "policy-article-baseline",
  "status": "PUBLISHED",
  "applicability": {"modalities": ["text"], "task_types": ["generic_corpus"]},
  "items": [
    {
      "key": "exact_duplicate_gate",
      "metric": {"key": "02080103", "version": "1.0.0", "measurement": "exact_redundant_record_ratio"},
      "scope": "DATASET",
      "calculation": {"parameters": {}, "implementation_constraints": {"deterministic": true}},
      "rule": {"operator": "LTE", "threshold": 0.01},
      "severity": "ERROR",
      "on_missing": "UNKNOWN"
    }
  ],
  "gate": {"unknown_policy": "FAIL", "fail_on_severities": ["ERROR", "CRITICAL"]},
  "fingerprint": "sha256:...",
  "published_at": "2026-10-27T00:00:00Z"
}
```

Contract 发布前必须校验 metric/measurement 存在、参数合法、规则类型与 measurement 相容、依赖无环，并至少存在一个满足实现约束的 Calculator。发布后只能创建新版本，不能原地修改。

`QualityContractVersion 1.0` 的阈值是用户创建 Contract version 时提供的必填入参，不属于 Metric Definition 或 Calculator 默认配置。首批阈值规则支持 `LT/LTE/GT/GTE/EQ/NE`；其中有序比较必须使用数值 threshold。Contract 状态为 `DRAFT/PUBLISHED/RETIRED`，PUBLISHED 必须包含带时区的 `published_at`，DRAFT 不得提前写入发布时间。

### 7.2 LogicalEvaluationPlan

Logical Plan 保存“算什么”：它把已发布 Contract 展开成与执行后端无关的 DAG，固化指标/特征版本、规范化后的计算参数、评测对象和依赖，但不选择 Calculator、batch size 或运行资源。

```json
{
  "schema_version": "1.0",
  "logical_plan_id": "uuid",
  "dataset_version_id": "uuid",
  "contract_version_id": "uuid",
  "compiler": {
    "key": "qa-eval-logical-planner",
    "version": "1.0.0"
  },
  "nodes": [
    {
      "node_id": "source-normalized-records",
      "node_type": "SOURCE",
      "depends_on": [],
      "critical": true,
      "spec": {
        "dataset_version_id": "uuid",
        "artifact_kind": "NORMALIZED_DATA",
        "sample_record_schema": "2.0"
      }
    },
    {
      "node_id": "metric-02080103",
      "node_type": "METRIC",
      "depends_on": ["source-normalized-records"],
      "critical": true,
      "spec": {
        "metric": {"key": "02080103", "version": "1.0.0"},
        "measurements": [
          "exact_affected_record_ratio",
          "exact_redundant_record_ratio",
          "near_duplicate_record_ratio"
        ],
        "scope": "DATASET",
        "subject_selector": {
          "content_roles": ["article.body.plain"]
        },
        "calculation_parameters": {
          "char_ngram_size": 3,
          "char_ngram_min_frequency": 2,
          "word_ngram_size": 2,
          "minhash_shingle_size": 5,
          "minhash_permutations": 64,
          "lsh_bands": 16,
          "near_duplicate_threshold": 0.8
        },
        "contract_item_keys": ["exact_duplicate_gate"]
      }
    },
    {
      "node_id": "rule-exact-duplicate-gate",
      "node_type": "RULE",
      "depends_on": ["metric-02080103"],
      "critical": true,
      "spec": {
        "contract_item_key": "exact_duplicate_gate",
        "metric_node_id": "metric-02080103",
        "measurement": "exact_redundant_record_ratio"
      }
    }
  ],
  "outputs": [
    {"node_id": "metric-02080103", "output_kind": "METRIC_RESULT"},
    {"node_id": "rule-exact-duplicate-gate", "output_kind": "RULE_EVALUATION"}
  ],
  "semantic_fingerprint": "sha256:...",
  "created_at": "2026-10-27T00:00:00Z"
}
```

#### LogicalNode

所有节点共享：

- `node_id`：Plan 内稳定且唯一；重编译相同规范化语义时应稳定生成。
- `node_type`：`SOURCE/FEATURE/METRIC/AGGREGATE/RULE/ISSUE/REPORT`。
- `depends_on`：前置 node ID；数组顺序规范化后参与 fingerprint。
- `critical`：节点最终失败是否导致 Run 失败；不表达质量规则的 severity。
- `spec`：由 `node_type` 判别的节点配置。

各类 `spec` 的最小语义：

| node_type | 必需内容 |
|---|---|
| `SOURCE` | dataset version、artifact kind、输入 schema |
| `FEATURE` | feature key/version、subject selector、规范化参数 |
| `METRIC` | metric key/version、measurement、scope、subject selector、规范化参数、contract item |
| `AGGREGATE` | 上游 measurement、group keys、聚合语义 |
| `RULE` | contract item、上游 metric node、measurement |
| `ISSUE` | detector/rule 来源、issue category 和 scope |
| `REPORT` | 要汇总的 node/output，以及 report schema version |

Logical Plan 必须满足：node ID 唯一、依赖存在、DAG 无环、Contract item 可追溯、Metric/measurement 已注册、参数符合 Metric Definition、subject selector 能由 DatasetManifest/SampleRecord 满足。

`semantic_fingerprint` 对排除 ID 和时间戳后的 canonical semantic spec 计算 SHA-256，包括 dataset/contract version、compiler version、所有节点语义、规范化参数、依赖和输出声明。相同语义输入应生成相同 fingerprint。

### 7.3 PhysicalEvaluationPlan

Physical Plan 保存“怎么执行”：物理编译器为逻辑节点绑定具体实现，划分可调度 stage，并固化 Calculator、代码 digest、完整参数、输入输出 schema、执行环境、资源、batch、重试和缓存策略。它发布后不可变。

```json
{
  "schema_version": "1.0",
  "physical_plan_id": "uuid",
  "logical_plan_id": "uuid",
  "dataset_version_id": "uuid",
  "contract_version_id": "uuid",
  "compiler": {
    "key": "qa-eval-physical-planner",
    "version": "1.0.0"
  },
  "executor": {
    "type": "LOCAL",
    "environment": {
      "python_version": "3.12",
      "platform_image": null,
      "dependency_lock_digest": "sha256:..."
    }
  },
  "stages": [
    {
      "stage_id": "stage-source-scan",
      "logical_node_ids": ["source-normalized-records"],
      "depends_on": [],
      "critical": true,
      "runner": {"type": "SOURCE_SCAN", "version": "1.0.0"},
      "input_bindings": [
        {
          "name": "normalized_records",
          "source": {"type": "DATASET_ARTIFACT", "artifact_kind": "NORMALIZED_DATA"},
          "schema_ref": "qa-eval://schemas/sample-record/2.0"
        }
      ],
      "output_contracts": [
        {
          "name": "sample_batches",
          "kind": "STAGE_OUTPUT",
          "schema_ref": "qa-eval://schemas/sample-record/2.0"
        }
      ],
      "execution": {"batch_size": 1000, "resources": {"cpu": 1, "memory_mb": 512, "device": "cpu"}},
      "retry_policy": {"max_attempts": 3, "backoff": "EXPONENTIAL"},
      "cache_policy": {"enabled": true, "requires_deterministic_implementation": true}
    },
    {
      "stage_id": "stage-02080103",
      "logical_node_ids": ["metric-02080103"],
      "depends_on": ["stage-source-scan"],
      "critical": true,
      "runner": {"type": "CALCULATOR", "version": "1.0.0"},
      "implementation": {
        "calculator_key": "text_duplicate_degree",
        "calculator_version": "2.0.0",
        "entrypoint": "qa_eval.calculators:text_duplicate_degree",
        "code_digest": "sha256:...",
        "deterministic": true
      },
      "resolved_parameters": {
        "content_role": "article.body.plain",
        "char_ngram_size": 3,
        "char_ngram_min_frequency": 2,
        "word_ngram_size": 2,
        "minhash_shingle_size": 5,
        "minhash_permutations": 64,
        "lsh_bands": 16,
        "near_duplicate_threshold": 0.8
      },
      "input_bindings": [
        {
          "name": "samples",
          "source": {"type": "STAGE_OUTPUT", "stage_id": "stage-source-scan", "output": "sample_batches"},
          "schema_ref": "qa-eval://schemas/sample-record/2.0"
        }
      ],
      "output_contracts": [
        {"name": "metric_result", "kind": "METRIC_RESULT", "schema_ref": "qa-eval://schemas/metric-result/1.0"},
        {"name": "sample_features", "kind": "METRIC_EVIDENCE", "schema_ref": "qa-eval://schemas/evidence/02080103-sample-features/2.0.0"},
        {"name": "duplicate_groups", "kind": "METRIC_EVIDENCE", "schema_ref": "qa-eval://schemas/evidence/02080103-duplicate-groups/2.0.0"}
      ],
      "execution": {"batch_size": 1000, "resources": {"cpu": 1, "memory_mb": 2048, "device": "cpu"}},
      "retry_policy": {"max_attempts": 3, "backoff": "EXPONENTIAL"},
      "cache_policy": {"enabled": true, "requires_deterministic_implementation": true}
    },
    {
      "stage_id": "stage-rule-exact-duplicate-gate",
      "logical_node_ids": ["rule-exact-duplicate-gate"],
      "depends_on": ["stage-02080103"],
      "critical": true,
      "runner": {"type": "RULE_EVALUATOR", "version": "1.0.0"},
      "resolved_parameters": {
        "contract_item_key": "exact_duplicate_gate",
        "measurement": "exact_redundant_record_ratio",
        "operator": "LTE",
        "expected": 0.01,
        "on_missing": "UNKNOWN"
      },
      "input_bindings": [
        {
          "name": "metric_result",
          "source": {"type": "STAGE_OUTPUT", "stage_id": "stage-02080103", "output": "metric_result"},
          "schema_ref": "qa-eval://schemas/metric-result/1.0"
        }
      ],
      "output_contracts": [
        {"name": "rule_evaluation", "kind": "STAGE_OUTPUT", "schema_ref": "qa-eval://schemas/rule-evaluation/1.0"}
      ],
      "execution": {"batch_size": 1, "resources": {"cpu": 1, "memory_mb": 128, "device": "cpu"}},
      "retry_policy": {"max_attempts": 1, "backoff": "NONE"},
      "cache_policy": {"enabled": true, "requires_deterministic_implementation": true}
    }
  ],
  "semantic_fingerprint": "sha256:...",
  "execution_fingerprint": "sha256:...",
  "created_at": "2026-10-27T00:00:00Z"
}
```

#### PhysicalStage

- `stage_id` 是运行调度单位；一个 stage 可以实现一个或多个可安全融合的 logical node。
- `logical_node_ids` 保留物理执行到逻辑语义的反向追踪。
- `depends_on` 只能引用同一 Physical Plan 中的 stage ID，并且必须无环。
- `implementation` 只在需要插件实现的 stage 出现；Calculator 的 key/version/code digest 必须与 Registry 快照一致。
- `resolved_parameters` 是合并 Metric Definition 默认值、Contract 配置和 Planner 决策后的完整参数，不依赖运行时隐式默认值。
- `input_bindings` 显式声明数据集 artifact 或上游 stage output，禁止 Calculator 根据文件名猜输入类型。
- `output_contracts` 声明结果/证据名称、kind 和 schema；StageRun 只能提交这里声明的输出。
- `execution`、`retry_policy`、`cache_policy` 是声明式策略，不包含队列消息、租约和 Worker 私有状态。

#### Fingerprint

- `semantic_fingerprint` 表示结果可比性边界：包含 Logical Plan fingerprint、Calculator key/version、code digest、模型/Prompt/Rubric、影响数值的完整参数和输入输出 schema。
- `execution_fingerprint` 在 semantic fingerprint 基础上增加 executor/environment、batch、资源、重试和其他执行策略。
- 两种 fingerprint 都排除 plan ID、创建时间和非语义展示字段，使用排序后的 canonical JSON 计算 SHA-256。
- 只有 semantic fingerprint 相同，结果才可作为同一计算语义比较或安全复用；execution fingerprint 相同用于定位完全相同的运行配置。

暂不进入 canonical Physical Plan 的内容：Celery/Redis 消息、Worker 租约、心跳、队列名、Spark/Ray 分片内部结构、GPU 调度算法和 Planner 成本模型。这些属于执行器实现，可以演进而不改变 Plan 协议。

### 7.4 EvaluationRun

```json
{
  "schema_version": "1.0",
  "run_id": "uuid",
  "physical_plan_id": "uuid",
  "dataset_version_id": "uuid",
  "contract_version_id": "uuid",
  "trigger": {"type": "MANUAL", "actor_id": "...", "idempotency_key": "..."},
  "status": "RUNNING",
  "input_fingerprint": "sha256:...",
  "started_at": "...",
  "finished_at": null,
  "error": null
}
```

运行状态：`PENDING/RUNNING/PARTIAL_SUCCEEDED/SUCCEEDED/FAILED/CANCEL_REQUESTED/CANCELED`。它不表达数据质量是否 PASS。

### 7.5 StageRun

```json
{
  "schema_version": "1.0",
  "stage_run_id": "uuid",
  "run_id": "uuid",
  "physical_plan_id": "uuid",
  "stage_id": "stage-02080103",
  "logical_node_ids": ["metric-02080103"],
  "attempt": 1,
  "status": "SUCCEEDED",
  "calculator": {"key": "text_duplicate_degree", "version": "2.0.0", "code_digest": "sha256:..."},
  "resolved_parameters": {},
  "input_fingerprint": "sha256:...",
  "output_artifacts": [],
  "stats": {},
  "started_at": "...",
  "finished_at": "...",
  "error": null
}
```

StageRun 引用物理 `stage_id`；`logical_node_ids` 用于语义追踪，不能以单一 `node_id` 代替调度单位。重试创建新的 attempt，不覆盖旧 StageRun；`(run_id, stage_id, attempt)` 唯一。

## 8. MetricResult 与证据

### 8.1 MetricResult 1.0

```json
{
  "schema_version": "1.0",
  "metric_result_id": "uuid",
  "run_id": "uuid",
  "stage_run_id": "uuid",
  "dataset_version_id": "uuid",
  "metric": {"key": "02080103", "version": "1.0.0"},
  "subject": {"scope": "DATASET", "id": "dataset-version-uuid"},
  "status": "OK",
  "observations": [
    {"key": "exact_affected_record_ratio", "value": 0.005961950696677385, "value_type": "NUMBER", "unit": "ratio", "numerator": 89, "denominator": 14928},
    {"key": "exact_redundant_record_ratio", "value": 0.0030144694533762056, "value_type": "NUMBER", "unit": "ratio", "numerator": 45, "denominator": 14928}
  ],
  "population": {"input_count": 14960, "evaluated_count": 14928, "excluded_count": 32, "exclusions": [{"reason": "EMPTY_TEXT", "count": 32}]},
  "semantic_coverage": {
    "level": "PARTIAL",
    "covered": ["normalized exact duplicate", "n-gram repetition", "MinHash candidate", "paragraph hash"],
    "excluded": ["semantic duplicate", "sentence semantic duplicate"],
    "limitations": []
  },
  "evidence": [],
  "created_at": "..."
}
```

状态定义：

- `OK`：执行成功且具有合法 observation。
- `NOT_APPLICABLE`：指标语义不适用于该对象，无 observation。
- `MISSING`：适用但必要输入或有效分母缺失，无 observation。
- `ERROR`：执行失败或结果不可验证，无质量值。

`PARTIAL` 是语义覆盖级别，不是执行状态。`NOT_EVALUATED` 是报告覆盖状态，表示没有产生 MetricResult，不创建伪结果。

Observation value 使用由 `value_type` 判别的 number/string/boolean/object；每个 key 必须在 Metric Definition 中声明。分子分母适用时必须一并保存。

### 8.2 EvidenceArtifact

```json
{
  "evidence_id": "uuid",
  "kind": "SAMPLE_FEATURES",
  "schema_ref": "qa-eval://schemas/evidence/02080103-sample-features/2.0.0",
  "artifact": {},
  "subject_scope": "SAMPLE",
  "summary": {"record_count": 14960, "error_count": 0},
  "redaction": {"contains_raw_content": false, "policy": "hashes-and-identifiers-only"}
}
```

当前文件迁移：

| 当前文件                  | Canonical 归属                                                   |
| ------------------------- | ---------------------------------------------------------------- |
| `metric_result.json`    | 转换为 MetricResult envelope 和 observations                     |
| `sample_features.jsonl` | `EvidenceArtifact(kind=SAMPLE_FEATURES)`，生产建议迁移 Parquet |
| `duplicate_groups.json` | 拆为 exact/near/paragraph evidence，移除重复的 `groups` 别名   |
| CLI 参数和算法版本        | PhysicalPlan + StageRun，不重复塞入每个 MetricResult             |

## 9. Rule、Issue 与 Report

### 9.1 RuleEvaluation

RuleEvaluation 是 Contract 对某个 MetricResult observation 的确定性判定事实，不承载样本问题清单。

```json
{
  "schema_version": "1.0",
  "rule_evaluation_id": "uuid",
  "run_id": "uuid",
  "dataset_version_id": "uuid",
  "contract_version_id": "uuid",
  "contract_item_key": "exact_duplicate_gate",
  "metric_result_id": "uuid",
  "metric": {
    "key": "02080103",
    "version": "1.0.0",
    "measurement": "exact_redundant_record_ratio"
  },
  "subject": {"scope": "DATASET", "id": "dataset-version-uuid"},
  "status": "PASS",
  "observed": {
    "value": 0.0030144694533762056,
    "value_type": "NUMBER",
    "unit": "ratio"
  },
  "rule": {"operator": "LTE", "expected": 0.01},
  "reason_code": "THRESHOLD_SATISFIED",
  "evaluated_at": "2026-10-27T00:00:00Z"
}
```

约束：

- `status` 仅为 `PASS/FAIL/UNKNOWN/SKIPPED`。
- `observed` 必须原样引用 MetricResult 中指定 measurement 的类型、值和单位，Rule Evaluator 不重新计算指标。
- MetricResult 为 `ERROR/MISSING` 时不得产生 FAIL；按 Contract 的 `on_missing` 生成 UNKNOWN 或 SKIPPED。
- `reason_code` 使用稳定枚举，如 `THRESHOLD_SATISFIED`、`THRESHOLD_VIOLATED`、`METRIC_MISSING`、`METRIC_ERROR`、`NOT_APPLICABLE`。
- Rule PASS 不表示没有样本级 Issue；聚合比例达标时仍可存在具体问题样本。
- `PASS/FAIL` 必须携带从 MetricResult 原样摘录的 `observed`；`UNKNOWN/SKIPPED` 不携带 `observed`，避免为缺失或错误结果伪造观测值。

### 9.2 QualityIssue

QualityIssue 表达一个可定位、可审核、可去重的质量问题。它可以来自失败规则，也可以直接来自 Calculator/Detector evidence；整体 Rule PASS 时仍允许存在 Issue。

```json
{
  "schema_version": "1.0",
  "issue_id": "uuid",
  "run_id": "uuid",
  "dataset_version_id": "uuid",
  "source": {
    "metric_result_id": "uuid",
    "metric_key": "02080103",
    "metric_version": "1.0.0",
    "measurement": "exact_redundant_record_ratio",
    "rule_evaluation_id": null
  },
  "detector": {"key": "exact_duplicate_redundant_sample", "version": "1.0.0"},
  "category": "DUPLICATE_CONTENT",
  "code": "EXACT_DUPLICATE_REDUNDANT",
  "determination": "CONFIRMED",
  "severity": "WARNING",
  "scope": "SAMPLE",
  "subject": {"type": "SAMPLE", "sample_id": "duplicate-sample-id"},
  "related_subjects": [
    {
      "relation": "DUPLICATE_OF",
      "subject": {"type": "SAMPLE", "sample_id": "representative-sample-id"}
    }
  ],
  "message": "该样本与重复组代表样本的规范化正文完全相同。",
  "evidence": [
    {
      "evidence_id": "uuid",
      "locator": {"group_id": "sha256:...", "normalized_sha256": "..."}
    }
  ],
  "dedup_key": "sha256:...",
  "status": "OPEN",
  "created_at": "2026-10-27T00:00:00Z",
  "updated_at": "2026-10-27T00:00:00Z"
}
```

#### Issue 来源和判定

- `determination=CONFIRMED`：确定性规则或已经校准、批准的判定流程确认不符合。
- `determination=CANDIDATE`：近重复召回、低置信度模型等只说明值得复核，不得显示为确定不符合。
- 人工复核结果不修改 `determination` 的原始计算事实；通过追加 Feedback Record 改变 Issue 生命周期或形成新的审核事实。
- `severity` 为 `INFO/WARNING/ERROR/CRITICAL`，通常继承 detector 默认值或关联 Contract item；它不等于 determination。
- `status` 为 `OPEN/ACKNOWLEDGED/RESOLVED/DISMISSED`，属于问题生命周期，不等于计算或规则状态。

#### IssueSubject

`scope` 与 `subject.type` 一致，支持：

| scope | subject 最小字段 | 用途 |
|---|---|---|
| `DATASET` | dataset version ID | 数据集整体问题 |
| `GROUP` | group ID、member count | 重复组、聚类或分组异常 |
| `SAMPLE` | sample ID | 缺失、格式错误、冗余样本等 |
| `PAIR` | pair ID、left/right sample ID | 近重复对、跨样本矛盾 |
| `CONTENT` | sample ID、content ID | 单个图片、消息、正文或音频片段问题 |
| `ANNOTATION` | sample ID、annotation ID | 标签冲突、标注格式或准确性问题 |

每个 Issue 必须能够通过 subject 定位到 DatasetVersion 内的对象。`related_subjects` 表达代表样本、重复对象、冲突标注等关系，不能用无结构 message 代替。

#### 高基数 IssueIndex

Report 不内嵌全部 Issue。Issue Detector 同时生成可分页查询的索引 artifact：

```json
{
  "schema_version": "1.0",
  "issue_index_id": "uuid",
  "run_id": "uuid",
  "dataset_version_id": "uuid",
  "artifact": {
    "artifact_id": "uuid",
    "kind": "ISSUE_INDEX",
    "uri": "runs/uuid/issues/part-000.parquet",
    "format": "parquet",
    "media_type": "application/vnd.apache.parquet",
    "schema_ref": "qa-eval://schemas/quality-issue/1.0",
    "size_bytes": 123,
    "record_count": 7562,
    "checksum": {"algorithm": "sha256", "value": "64-lowercase-hex"}
  },
  "supported_filters": ["metric_key", "code", "determination", "severity", "scope", "sample_id"],
  "summary": {"confirmed_count": 45, "candidate_count": 7517}
}
```

MVP 可将 `CONFIRMED` Issue 和查询索引字段存 PostgreSQL，将大规模 candidate/pair/group 明细及证据存 Parquet；无论存储介质如何，单条记录都遵守同一个 QualityIssue schema。

### 9.3 QualityReport

QualityReport 是一次 Run 的不可变汇总快照。它引用而不复制上游事实，并按指标提供 Issue 汇总和下钻入口。

```json
{
  "schema_version": "1.0",
  "report_id": "uuid",
  "run_id": "uuid",
  "dataset_version_id": "uuid",
  "contract_version_id": "uuid",
  "generator": {"key": "qa-eval-report", "version": "1.0.0"},
  "execution_status": "SUCCEEDED",
  "gate_status": "PASS",
  "coverage": {"evaluated": 1, "not_applicable": 0, "missing": 0, "error": 0, "not_evaluated": 30},
  "dimensions": [
    {"key": "DATA_QUALITY", "status": "PARTIAL_COVERAGE", "evaluated_metric_count": 1, "total_metric_count": 31}
  ],
  "metrics": [
    {
      "metric": {"key": "02080103", "version": "1.0.0", "name": "重复内容程度"},
      "metric_result_id": "uuid",
      "status": "OK",
      "observations": [
        {"key": "exact_affected_record_ratio", "value": 0.005961950696677385, "unit": "ratio"},
        {"key": "exact_redundant_record_ratio", "value": 0.0030144694533762056, "unit": "ratio"}
      ],
      "rule_evaluations": [
        {"rule_evaluation_id": "uuid", "contract_item_key": "exact_duplicate_gate", "status": "PASS"}
      ],
      "issue_summary": {
        "confirmed_count": 45,
        "candidate_count": 7517,
        "by_code": {"EXACT_DUPLICATE_REDUNDANT": 45, "NEAR_DUPLICATE_CANDIDATE": 7517},
        "by_scope": {"SAMPLE": 7562}
      },
      "issue_index_id": "uuid",
      "evidence_ids": ["uuid"]
    }
  ],
  "issue_summary": {"confirmed_count": 45, "candidate_count": 7517, "open_count": 7562},
  "rendered_artifact_ids": ["uuid"],
  "generated_at": "2026-10-27T00:00:00Z"
}
```

约束：

- `execution_status` 来自 EvaluationRun；`gate_status` 来自 Contract gate 汇总，二者不得互相推导。
- `coverage.not_evaluated` 表示计划范围中未产生 MetricResult 的指标，不创建伪 MetricResult。
- 每个 metric section 必须引用 MetricResult；observation 是用于展示的不可变摘录，必须与原结果逐值一致。
- `issue_summary` 按 `determination/code/scope/status` 从已提交的 IssueIndex 汇总，不重新运行 detector。
- Report 不嵌入全部 sample ID 或原始文本。UI 通过 `issue_index_id` 分页筛选 Issue，再按权限读取对应 SampleRecord 预览。
- Rule PASS 与 `confirmed_count > 0` 可以同时成立；这表示聚合质量门槛已满足，但仍存在可定位问题样本。
- JSON 是 canonical report；HTML/Markdown 只能渲染该 JSON，不重新计算指标、规则或 Issue。

### 9.4 02080103 MVP 下钻语义

当前真实结果应按以下方式展示，避免混淆“受影响”“应移除”和“候选”：

| 当前事实 | MVP 语义 | Issue |
|---|---|---|
| 89 条 exact duplicate affected records | 重复组全部受影响成员 | 可作为 group evidence 和影响计数 |
| 45 条 exact redundant records | 每组保留一个代表后的冗余样本 | 每条生成 `CONFIRMED / EXACT_DUPLICATE_REDUNDANT / SAMPLE` Issue |
| 7,517 条 near-duplicate records | MinHash + LSH 召回结果 | 生成 `CANDIDATE` Issue，不直接称为不符合 |
| 32 条 empty records | 02080103 无法评测的排除记录 | 不生成重复内容 Issue；交由结构/信息缺失指标处理 |

指标详情页默认展示 `determination=CONFIRMED` 的不符合样本，并提供“待复核候选”独立筛选。对于每条 exact redundant Issue，至少展示 sample ID、代表 sample ID、group ID、normalized hash 和可授权获取的内容预览。

## 10. 存储映射

### 10.1 PostgreSQL 控制面

保存 Dataset、DatasetVersion、Manifest 元数据、Registry、Contract、Plan、Run、StageRun、聚合 MetricResult、RuleEvaluation、QualityIssue 和 Report 索引。

MetricResult 推荐使用“结果 envelope + observations”结构。v0 可在 envelope 中以校验过的 JSONB 保存 observations；在需要跨指标高频查询时再无损拆到 `metric_observations` 表。不要继续使用三个互斥 value 列表达多 measurement 指标。

### 10.2 文件/对象存储数据面

- raw、normalized、rejected、profile 完整产物
- 样本级/样本对/分组级特征和证据
- stage 中间产物
- canonical report JSON 和渲染视图

所有提交遵循临时写入、checksum 校验、最终 URI 提交；数据库只引用已提交 artifact。

## 11. 必须成立的不变量

1. 一个 SampleRecord 只属于一个 DatasetVersion，`(dataset_version_id, sample_id)` 唯一。
2. PUBLISHED DatasetVersion 的 Manifest、原始和标准化 artifact 不可原地替换。
3. NULL、MISSING、空字符串和 normalization reject 四者不可互换。
4. Calculator 只消费 descriptor 声明的 canonical schema/roles，不自动猜输入形状。
5. 每个 MetricResult 都可追溯到 DatasetVersion、Run、StageRun、Metric Definition 和 Calculator。
6. 每个 observation 都由 Metric Definition 声明，Contract 规则引用明确的 measurement key。
7. 计算状态、语义覆盖、规则状态、运行状态和报告 gate 状态使用不同字段。
8. ERROR/MISSING 不得转换成数值 0 或规则 FAIL。
9. 高基数证据具有 schema、checksum、record count 和敏感信息策略。
10. 历史结果和人工反馈采用追加式记录，不修改既有计算事实。
11. QualityIssue 可以在 Rule PASS 时存在；Issue 生成不得依赖规则必须失败。
12. Report 不嵌入完整高基数问题明细，只保存可核验汇总和 IssueIndex 引用。

## 12. 场景验收

### 12.1 jhsjk_article + 02080103

- HTML 与纯文本分别可寻址，Calculator 明确选择 `article.body.plain`。
- 原始 NULL 不再转换成空字符串。
- 14,960 条输入、14,928 条有效文本和 32 条排除记录能在 population 中闭合。
- exact/near/paragraph/n-gram 以不同 measurement/evidence 表达。
- Result 能追溯到现有快照 checksum、Calculator 2.0.0 和完整参数。
- 45 条 exact redundant records 可从 02080103 指标详情下钻为 CONFIRMED Issue。
- 近重复召回与确定不符合分开展示，32 条空文本不误报为重复 Issue。

### 12.2 图文多模态样本

- 一条样本可同时包含文本 content 和一个或多个 image asset。
- 图片 checksum、MIME、尺寸属于 asset payload；caption/alt text 是独立 text content。
- 跨模态一致性指标能通过 content ID 精确声明输入对。

### 12.3 带标注 SFT

- 多轮 user/assistant/system 消息保持顺序、speaker 和独立 content ID。
- 单轮混合文本/图片可以用 turn index 关联。
- loss mask、类别、偏好 chosen/rejected、人工审核信息进入 typed annotations。
- 标注规范性、内容一致性和模型应用指标可以复用同一 SampleRecord，不要求重新定义数据格式。

## 13. 实施顺序

1. 依据 ADR-0002 将本冻结基线实现为版本化 JSON Schema，后续不兼容变更通过新 ADR 和 schema major version 处理。
2. 在 `schemas/` 建立 ArtifactRef、DatasetManifest 2.0、SampleRecord 2.0、RejectedRecord 和 MetricResult 1.0 JSON Schema。
3. 重构 Python domain：领域实体、wire contracts 和 ORM 分层；生成 JSON Schema 一致性测试。
4. 实现旧 SampleRecord 1.x、现有 snapshot manifest 到新协议的显式迁移器；禁止运行时自动猜测。
5. 修复 MySQL Adapter 的 NULL 保真，并完整标准化 `jhsjk_article`。
6. 拆分 02080103 MetricDefinition 与 CalculatorDescriptor，适配 canonical SampleRecord。
7. 用现有真实产物生成 canonical MetricResult/Evidence，并做数量、比例、checksum 回归测试。
8. 再实现 Contract、Plan、Run、Rule、Issue 和 Report 的平台持久化。

在步骤 1–7 完成前，不建议继续横向增加数据质量指标；否则每个指标都会固化一套新的私有输入输出格式。
