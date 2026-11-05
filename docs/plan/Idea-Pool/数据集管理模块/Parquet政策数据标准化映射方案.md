# Parquet 政策数据标准化映射方案

- 状态：设计草案（本轮不实现）
- 日期：2026-11-05
- 范围：MySQL 政策文本导出的 Parquet 文件→ canonical `SampleRecord 2.0`
- 非目标：不修改业务代码，不开发评测指标 Calculator，不新建平行 Sample Schema

## 1. 结论

推荐不引入 `FieldMappingSpec 3.0`，也不复用旧的分组式 `FieldMappingConfig 1.0` 扩展新功能。应继续以已被 canonical baseline、API、Dataset Version 服务和 JSONL normalizer 使用的 `qa_eval.contracts.datasets.FieldMappingSpecV2` 为唯一映射协议，只补齐读取层和归一化层能力。

最小数据流为：

```text
Parquet upload
  -> ParquetSourceReader（schema / preview / row iterator）
  -> FieldMappingSpec 2.0（Dataset Version 级、发布后不可变）
  -> JhsjkArticleNormalizer（与文件格式无关的 row normalization）
  -> SampleRecord 2.0 JSONL + RejectedRecord 1.0 JSONL
  -> DatasetManifest 2.0 + NormalizationRun 1.0
```

Parquet 是 raw artifact 的物理格式，不应改变字段语义、映射协议或 normalized artifact 的 canonical schema。

## 2. 现有实现审查

### 2.1 已有的两代模型

| 实现 | 当前用途 | 判断 |
| --- | --- | --- |
| `domain/entities/field_mapping.py` 的 `FieldMappingConfig 1.0` | 把 MySQL 列分为 sample id/core/metadata/time/other | 只能分组，不能表达 role、多目标、presence 和 canonical target，不适合继续扩展 |
| `domain/entities/sample_record.py` 的 `SampleRecord 1.1` | 旧 MySQL adapter 输出 | 会把 core `NULL` 转为空串，且与 frozen canonical 2.0 不同，不应作为新入口目标 |
| `contracts/datasets.py` 的 `FieldMappingSpecV2` | Dataset Version API、上传映射、Manifest、当前 normalizer | 已是 canonical 路径，应复用 |
| `contracts/datasets.py` 的 `SampleRecordV2` / `RejectedRecordV1` | 当前发布产物 | 已显式区分 `PRESENT`/`NULL`/`MISSING`，应作为唯一输出契约 |

`DatasetManifest` 也存在旧 `domain/entities/dataset_manifest.py` 1.0 和新 `contracts/datasets.py` 2.0 两套实现。本方案只使用 `DatasetManifestV2`，其 `field_mapping` 直接内嵌 `FieldMappingSpecV2`。

### 2.2 当前已具备的能力

- Dataset Version 只允许在 `DRAFT` 状态上传 raw file 和修改映射；重新上传会使旧映射失效；发布后不能再改。
- `FieldMappingSpecV2` 已有 `MAP/PASSTHROUGH/IGNORE`、target kind、role、modality、value type、presence policy 和 required flag。
- `SampleRecordV2.contents[]` 能同时容纳 HTML 和纯文本，并精确保留 null/missing/空串的区别。
- normalizer 已能按行产生 Sample 或 Reject，并校验 `normalized + rejected = input`。
- Manifest 已能同时引用 raw、normalized 和 rejected artifacts，lineage 已包含输入指纹与配置指纹位置。

### 2.3 与需求的差距

| 需求 | 当前差距 | 影响 |
| --- | --- | --- |
| Parquet 上传、校验、schema/preview | API 后缀只允许 `.jsonl`；service 会将全部内容按 UTF-8 JSONL 解析 | Parquet 无法进入当前发布流程 |
| 流式处理 Parquet | 当前 inspection 和 publish 都会将 raw artifact 整体读入内存 | 大文件存在内存风险 |
| `content → raw_html + plain_text` | Spec 可放置两条同源 `MAP`，但当前 normalizer 用源字段名生成 `content_id`，会触发重复 ID；也没有 HTML 转纯文本 | 协议表达力存在，执行不完整 |
| 时间解析 | 当前 `TEMPORAL` 只包装原值，固定声明 `SECOND/UNKNOWN`，未识别 epoch 毫秒、日期、无时区 datetime 和缺失值 | 可产生语义错误的 TemporalValue |
| 枚举与一致性检查 | 当前映射和 normalizer 都未实现 | `zccjfl/level`、publisher、document number 之间冲突无记录 |
| 字段合并 | `MappingTarget.name` 只是字符串，当前实现直接存为 metadata key，同名目标会覆盖 | publisher、attachments、relations 需 adapter 实现明确的 merge |
| `presence_policy` / `required_for_normalization` | 契约已定义，normalizer 没有执行 | 配置与实际结果不一致 |
| null / `"None"` / `"无"` | JSON null 能保留，字符串目前都原样保留，但没有字段级 missing-token 规则 | 若全局将 `"无"` 清空会丢失有效业务值 |
| 源字段血缘、原值和转换告警 | lineage 只有 transform 级指纹；content attributes 为空；非致命转换异常无结构化载体 | 无法回答“这个值从哪个字段怎么来” |
| Reject 可追溯性 | 已有 Reject，但 `source_record_id` 总是 null，error code 常为 Python 异常类名，excerpt 未保留 | 运维定位和定向重试能力不足 |
| 配置指纹 | 当前是一个硬编码字符串的 hash，不是实际 mapping 的 canonical JSON hash | 映射变化可能不反映在 lineage |
| raw format 与 Manifest | Manifest 固定记录 `jsonl_upload/jsonl/application-x-ndjson` | Parquet 的 format、media type、schema ref 会被错记 |

## 3. 推荐的最小兼容方案

### 3.1 保留 FieldMappingSpec 2.0

本需求不需要新 schema version：

1. `FieldMappingSpecV2.mappings` 是规则列表，当前模型没有限制 `source_path` 唯一，因此同一源字段可以有多条 `MAP`。
2. `CONTENT` target 已有 `name/role/modality`，足以区分 `article.body.html` 和 `article.body.plain`。
3. `METADATA`、`SOURCE_ATTRIBUTE`、`TEMPORAL` 已覆盖其余目标类别。
4. 映射已作为 Dataset Version artifact refs 和 Manifest 的一部分，发布后不可变的门禁已存在。

需补充两条规则语义，但不改 JSON shape：

- 对 `CONTENT` target，`target.name` 定义为样本内稳定且唯一的 `content_id`；未填时 adapter 才回退到可确定生成的 ID。
- 重复 `source_path` 的规则按列表顺序独立执行，但不得产生重复 `content_id`。

`MappingTarget.name` 的这一用法已符合当前类型定义，只需修正 normalizer 目前忽略 name 生成 content ID 的行为。

### 3.2 两层解耦

Field Mapping 只回答：

- 从哪个源 path 取值；
- 值的 canonical kind、name、content role 和 modality；
- null/missing 如何处理，是否为标准化的必需值。

`JhsjkArticleNormalizationAdapter` 根据 target 语义负责：

- HTML 清洗与完整纯文本提取；
- epoch 毫秒、ISO datetime、date 和无时区时间解析；
- 枚举归一化与原值保留；
- publisher、document number、policy level 的合并和一致性检查；
- attachment 成对、relation 聚合；
- 产生 content attributes、normalization warnings 和 Reject。

不建议把 `html_to_text`、`epoch_ms_to_datetime` 之类可执行函数名放入 mapping。它会让配置变成一套新 DSL，并把适配器责任泄漏到用户配置层。

### 3.3 输入读取层

抽取一个轻量 `TabularSourceReader` 端口，首批实现 `JsonlSourceReader` 和 `ParquetSourceReader`，统一输出 `(source_row, Mapping[str, Any])` 迭代器、schema 与有界 preview。

Parquet reader 应：

- 用 magic bytes `PAR1` 与 PyArrow metadata 校验，不只信任文件名；
- 从 footer 获取 schema、row count、row groups，preview 只读必要批次；
- publish 时用 `iter_batches()` 逐批归一化，不将全文件读入内存；
- 将 Arrow scalar 转换为 Python/JSON-compatible 值，对 binary、decimal、timestamp 和嵌套类型明确策略；
- 把 raw artifact 记录为 `format=parquet`、`media_type=application/vnd.apache.parquet`，并在 source snapshot details 保留 Arrow schema 指纹。

### 3.4 null、伪空值和业务值

默认策略必须是保守的：

| 源值 | 含义 | 默认处理 |
| --- | --- | --- |
| Arrow/JSON null | 真正 null | content 写 `presence=NULL` 且不写 payload；metadata 保留 null |
| 字段不存在 | missing | content 写 `presence=MISSING`；按 presence policy 决定保留或 reject |
| `""` | 存在的空文本 | `presence=PRESENT`，payload text 为空串 |
| `"None"` | 伪空值候选 | 只对明确配置的字段做 missing-token 转换，同时在 source values/warnings 保留原值 |
| `"无"` | 可能是业务有效值 | 始终当作有效字符串，不转 null |

由于 `FieldMappingSpecV2` 尚无字段级 missing-token 参数，首期应将 MySQL 政策数据的 token 规则放在版本化 adapter 配置中，并纳入 `config_fingerprint`。只有当多个 adapter 都出现同类需求时，再评估向 FieldMappingSpec 增加通用参数并升级 schema，本轮不提前设计。

### 3.5 血缘、原值、告警与 Reject

- 每个 derived content 在 `attributes` 记录 `source_paths`、`transform`、`transform_version`。
- 发生转换的值在 `metadata.normalization.source_values` 保留有界原值；未映射字段通过 `PASSTHROUGH` 进入 `metadata.extra`。为避免无限复制大 HTML，`content` 的原值由 `article.body.html` 本身保留，不在 metadata 中重复。
- 可恢复异常（例如主备 publisher 不一致）记录到 `metadata.normalization.warnings[]`，不丢样本。
- 无法形成 sample id、required 字段 missing、输入行无法解析或结果违反 canonical schema 时产生 `RejectedRecordV1`。
- Reject 使用稳定业务 error code，如 `MISSING_SAMPLE_ID`、`INVALID_EPOCH_MILLISECONDS`、`CONTENT_NOT_TEXT`、`CANONICAL_VALIDATION_FAILED`；尽可能记录 `source_record_id`、`source_row`、`json_pointer` 和经脱敏的 excerpt。

## 4. 完整 FieldMappingSpec 2.0 示例

下例是当前 `FieldMappingSpecV2` 可校验的 JSON shape。其中 `content` 通过两条规则表达一对多；`target.name` 在 CONTENT 规则中作为 `content_id`。点分隔的 metadata name 是 adapter 的语义输入，adapter 负责组装嵌套对象，不应把它原样写成带点的 JSON key。

```json
{
  "schema_version": "2.0",
  "adapter": {
    "key": "jhsjk_policy_article",
    "version": "2.0.0"
  },
  "mappings": [
    {
      "source_path": "$.iid",
      "action": "MAP",
      "target": {"kind": "SAMPLE_ID", "name": "sample_id"},
      "value_type": "string",
      "presence_policy": "REJECT_MISSING",
      "required_for_normalization": true
    },
    {
      "source_path": "$.title",
      "action": "MAP",
      "target": {"kind": "CONTENT", "name": "title", "role": "article.title", "modality": "text"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.content",
      "action": "MAP",
      "target": {"kind": "CONTENT", "name": "body-html", "role": "article.body.html", "modality": "text"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.content",
      "action": "MAP",
      "target": {"kind": "CONTENT", "name": "body-plain", "role": "article.body.plain", "modality": "text"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.policyurl",
      "action": "MAP",
      "target": {"kind": "SOURCE_ATTRIBUTE", "name": "primary_url"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.href",
      "action": "MAP",
      "target": {"kind": "SOURCE_ATTRIBUTE", "name": "fallback_url.href"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.linkhref",
      "action": "MAP",
      "target": {"kind": "SOURCE_ATTRIBUTE", "name": "fallback_url.linkhref"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.pubtime",
      "action": "MAP",
      "target": {"kind": "TEMPORAL", "name": "published_at"},
      "value_type": "epoch_milliseconds_or_datetime",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.effectivetime",
      "action": "MAP",
      "target": {"kind": "TEMPORAL", "name": "effective_at"},
      "value_type": "epoch_milliseconds_or_date",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.formattime",
      "action": "MAP",
      "target": {"kind": "TEMPORAL", "name": "formatted_at"},
      "value_type": "epoch_milliseconds_or_datetime",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.organization",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "publisher.organization"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.publishunit",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "publisher.publishunit"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.filenumber",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "document_number.filenumber"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.issue",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "document_number.issue"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.zccjfl",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "policy_level.zccjfl"},
      "value_type": "policy_level_code_or_label",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.level",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "policy_level.level"},
      "value_type": "policy_level_code_or_label",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.validity",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "business.validity"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.status",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "business.status"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.isPublic",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "business.is_public"},
      "value_type": "boolean_or_code",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.attachname",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "attachments.name"},
      "value_type": "string_or_list",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.attachurl",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "attachments.url"},
      "value_type": "string_or_list",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.zcjdbt",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "relations.interpretation_title"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.tjlj",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "relations.recommended_url"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.spjdlj",
      "action": "MAP",
      "target": {"kind": "METADATA", "name": "relations.video_interpretation_url"},
      "value_type": "string",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    },
    {
      "source_path": "$.source_table_specific_field",
      "action": "PASSTHROUGH",
      "value_type": "any",
      "presence_policy": "PRESERVE",
      "required_for_normalization": false
    }
  ]
}
```

实际发布的 mapping 必须覆盖 schema inspection 发现的每个源字段：有 canonical 语义的使用 `MAP`，需保留但无稳定语义的使用 `PASSTHROUGH`，经用户明确决定不进入 SampleRecord 的使用 `IGNORE`。上例中的 `source_table_specific_field` 是“其他字段”模板，实际保存时应展开为每个真实字段一条规则，不支持通配符。

## 5. SampleRecord 2.0 示例

下例体现：HTML 与纯文本并存；时间类型保留时区知识状态；主备字段保留原值并生成一致性 warning；其他源字段进入 `metadata.extra`。示例 UUID 和 hash 仅用于展示 shape。

```json
{
  "schema_version": "2.0",
  "dataset_version_id": "018f6f2e-8d5c-7a21-9d65-4ea395da6427",
  "sample_id": "1000123456",
  "contents": [
    {
      "content_id": "title",
      "modality": "text",
      "role": "article.title",
      "presence": "PRESENT",
      "payload": {
        "type": "inline_text",
        "text": "关于促进中小企业高质量发展的通知",
        "format": "plain",
        "language": "zh"
      },
      "attributes": {"source_paths": ["$.title"]}
    },
    {
      "content_id": "body-html",
      "modality": "text",
      "role": "article.body.html",
      "presence": "PRESENT",
      "payload": {
        "type": "inline_text",
        "text": "<p>为进一步促进中小企业发展，<br>现将有关事项通知如下。</p>",
        "format": "html",
        "language": "zh"
      },
      "attributes": {"source_paths": ["$.content"], "transform": "identity"}
    },
    {
      "content_id": "body-plain",
      "modality": "text",
      "role": "article.body.plain",
      "presence": "PRESENT",
      "payload": {
        "type": "inline_text",
        "text": "为进一步促进中小企业发展，\n现将有关事项通知如下。",
        "format": "plain",
        "language": "zh"
      },
      "attributes": {
        "source_paths": ["$.content"],
        "transform": "html_to_complete_plain_text",
        "transform_version": "1.0.0"
      }
    }
  ],
  "annotations": [],
  "metadata": {
    "temporal": {
      "published_at": {
        "value": "2026-10-01T08:30:00+08:00",
        "precision": "SECOND",
        "source_timezone": "Asia/Shanghai",
        "timezone_status": "KNOWN"
      },
      "effective_at": {
        "value": "2026-11-01",
        "precision": "DAY",
        "source_timezone": null,
        "timezone_status": "NOT_APPLICABLE"
      },
      "formatted_at": null
    },
    "publisher": {
      "value": "市工业和信息化局",
      "organization": "市工业和信息化局",
      "publishunit": "市经济和信息化局"
    },
    "document_number": {
      "value": "A市工信发〔2026〕12号",
      "filenumber": "A市工信发〔2026〕12号",
      "issue": "12"
    },
    "policy_level": {
      "value": "MUNICIPAL",
      "zccjfl": "2",
      "level": "市级"
    },
    "business": {
      "validity": "有效",
      "status": "1",
      "is_public": true
    },
    "attachments": [
      {"name": "申报指南.pdf", "url": "https://example.gov.cn/files/guide.pdf"}
    ],
    "relations": [
      {"type": "POLICY_INTERPRETATION", "title": "《通知》政策解读", "url": "https://example.gov.cn/interpretation/1000123456"},
      {"type": "VIDEO_INTERPRETATION", "url": "https://example.gov.cn/video/1000123456"}
    ],
    "normalization": {
      "source_values": {
        "pubtime": 1790814600000,
        "isPublic": "1"
      },
      "warnings": [
        {
          "code": "PUBLISHER_INCONSISTENT",
          "source_paths": ["$.organization", "$.publishunit"],
          "message": "organization 与 publishunit 不一致，已按主字段优先级选择 organization"
        }
      ]
    },
    "extra": {
      "source_table_specific_field": "原始业务值"
    }
  },
  "source": {
    "source_artifact_id": "018f6f2e-8d5c-7b14-a083-eccbd412ef54",
    "source_record_id": "1000123456",
    "source_row": 42,
    "locator": "parquet://policy/jhsjk_article#iid=1000123456",
    "attributes": {
      "primary_url": "https://example.gov.cn/policy/1000123456",
      "fallback_urls": [
        "https://example.gov.cn/detail/1000123456",
        "https://archive.example.gov.cn/1000123456"
      ]
    }
  },
  "lineage": {
    "input_fingerprint": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "transforms": [
      {
        "key": "jhsjk_policy_article",
        "version": "2.0.0",
        "config_fingerprint": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      }
    ]
  }
}
```

注：`SampleRecordV2.metadata` 是明确的 JSON 扩展容器，上述 publisher/attachments/relations/normalization 均不是新的顶层 schema，不会与 canonical SampleRecord 平行分叉。它们的 shape 应在 adapter 版本中稳定化并通过测试固化。

## 6. 后续需修改的文件

| 文件 | 建议修改 |
| --- | --- |
| `src/qa_eval/application/services/dataset_version_service.py` | 上传时识别 JSONL/Parquet；记录 format/media type/schema fingerprint；inspection 和 publish 通过 reader 迭代，不整文件载入；保留重传使 mapping 失效的现有行为 |
| `apps/api/routes/dataset_versions.py` | 允许 `.parquet`，但以内容校验为准；错误返回稳定的 422 信息 |
| `src/qa_eval/application/normalization/jhsjk_article_normalizer.py` | 拆出文件无关的 row adapter；执行 presence policy；使用 target name 作为 content ID；支持一对多；实现 HTML、时间、枚举、合并、告警、血缘和稳定 Reject code；按真实 mapping JSON 计算 config fingerprint |
| `src/qa_eval/application/normalization/` 下新 reader 模块 | 实现 `JsonlSourceReader` / `ParquetSourceReader`；若项目希望端口稳定，可将 protocol 放入 `domain/ports` |
| `src/qa_eval/contracts/datasets.py` | 原则上不改 shape；只补注释/验证：CONTENT name 为 content ID、mapping 必须恰有一个 SAMPLE_ID、content target name 不重复。若为兼容已存草稿不宜改 Pydantic 校验，可先在 service 发布门禁校验 |
| `schemas/qa-eval/field-mapping/2.0.schema.json` | 从合同生成的 schema 与 Pydantic 约束保持一致；如仅补 description 不改 schema version |
| `apps/web/src/api/datasetVersions.ts` 及相关上传/映射页 | 放开 Parquet 选择、展示物理类型与 preview；允许同一源字段映射到多个 content role；展示未配置字段门禁 |
| `pyproject.toml` / lockfile | 确认 PyArrow 为运行时依赖而不只是导出脚本的偶然依赖 |
| `tests/unit/test_dataset_version_service.py` | 增加 Parquet 上传、验证、inspection、重传使 mapping 失效、发布门禁测试 |
| `tests/unit/` 下新 reader/normalizer 测试 | 覆盖 Arrow 类型、批量迭代、一对多、null 语义、HTML/时间/枚举/合并、Reject |
| `tests/integration/test_e2e_baseline.py` 或新 Parquet E2E | 使用小型固定 Parquet fixture 验证整个发布闭环与 artifacts |
| `docs/architecture/canonical-data-flow-models-v1.md` | 不改 frozen 核心决策；实现完成后只增加 Parquet raw format 和 target name 执行语义的澄清或用 ADR 记录 |

`domain/entities/field_mapping.py`、`domain/entities/sample_record.py`、`infrastructure/mysql/record_adapter.py` 不应继续承接这一新功能。它们是旧 1.x 路径；后续应单独计划迁移/废弃，不与 Parquet 支持绑定实施。

## 7. 建议实现步骤

1. **固化契约语义**：为 `FieldMappingSpecV2` 补文档和发布门禁，确定唯一 SAMPLE_ID、至少一个 CONTENT、CONTENT name 唯一、所有源字段已 MAP/PASSTHROUGH/IGNORE。
2. **引入 source reader**：先将当前 JSONL 读取迁入统一 reader，保证现有测试不变；再增加 Parquet reader 和内容校验。
3. **拆分 row normalization**：把“读一行”与“转一行”分开，使 JSONL 和 Parquet 共用同一政策数据 adapter。
4. **实现语义转换**：按 HTML、temporal、publisher/document number/policy level、attachments/relations 顺序实现；每一类先固化 unit test 和 warning/reject 策略。
5. **改造 artifact/manifest**：使 raw format、media type、source type、schema fingerprint、record count 反映真实输入，config fingerprint 由 mapping + adapter config 的 canonical JSON 生成。
6. **打通 API 与前端**：增加 Parquet 上传/preview，支持一对多 mapping 编辑，保持 Dataset Version 不可变门禁。
7. **E2E 验收**：用同一批逻辑记录的 JSONL 和 Parquet fixture 发布两个版本，比较 SampleRecord 语义等价（允许 artifact ID/fingerprint/locator 不同）。

## 8. 测试与验收标准

### 8.1 上传与读取

- 合法 Parquet 可上传，返回 `validated_format=parquet`、文件 hash、size、row count 和 schema 指纹。
- 仅改后缀、footer 破损、加密/不支持 codec、零行或不支持列类型的文件在上传/校验阶段给出明确 422，不进入发布。
- inspection 返回全 schema 统计和有界 preview；长文本截断只影响 preview，不影响归一化。
- 大文件通过 batch/row-group 迭代处理，测试可用一个多 row-group fixture 证明没有调用全表 `read_table().to_pylist()`。

### 8.2 映射与归一化

- 同一 `content` 稳定产生唯一 ID 的 `article.body.html` 和 `article.body.plain`；纯文本保留段落/换行，不包含 HTML 标签。
- JSON/Arrow null、missing、`""`、`"None"`、`"无"` 五种值的结果分别符合第 3.4 节，不发生全局伪空值替换。
- epoch 毫秒、ISO datetime、date、无时区 datetime 均有覆盖；只有来源时区明确时才转 UTC/带 offset，否则保留 `timezone_status=UNKNOWN`。
- publisher、document number、policy level 在一致时生成归一值，不一致时保留两个原值并生成稳定 warning code。
- attachment name/url 数量不一致、relation 只有标题或只有 URL 时有明确可测的合并策略，不静默丢值。
- 所有源字段均可在 semantic target、`metadata.extra` 或显式 `IGNORE` 决策中找到；发布 mapping 不存在“未配置”状态。

### 8.3 Reject、血缘与闭环

- 缺失/空 sample id、required missing、不可解析的必需时间、canonical validation failure 产生 Reject，不进入 SampleRecord。
- 每个 Reject 尽可能带 source row、record id、稳定 error code、json pointer 和脱敏 excerpt，不吞掉异常。
- 每个 derived content 都能追溯到 source path 和 transform version；lineage 的 config fingerprint 随 mapping 或 adapter config 的任一变化而变化。
- 任意输入都满足 `input = normalized + rejected`；Manifest artifact record counts、NormalizationRun counts 与实际 JSONL 行数一致。
- Manifest raw artifact 正确声明 Parquet format/media type/checksum，normalized/rejected 仍分别符合 `SampleRecord 2.0` / `RejectedRecord 1.0` schema。
- Dataset Version 在 `PUBLISHED` 后无法替换 raw file 或 mapping；DRAFT 重传 raw file 后旧 mapping 被清除，必须重新确认。
- 同一逻辑数据的 JSONL 和 Parquet 输入产生语义等价的 SampleRecord，不因 raw 文件格式分叉 canonical 结果。

## 9. 不建议的方案

- 不在 `SampleRecord` 顶层新增政策专用字段；政策业务结构进入 `metadata`。
- 不为 Parquet 创建 `ParquetSampleRecord`；Parquet 只是 raw transport/storage format。
- 不让 Field Mapping 执行 HTML/时间/枚举函数；它仅声明语义，adapter 执行转换。
- 不让 Calculator 猜测 raw Parquet 形状；Calculator 仍只消费 canonical SampleRecord 或明确的 stage output。
- 不在本次改造中同时删除 1.x 旧模型；清理旧路径需单独迁移计划，避免放大 Parquet 功能的实施面。
