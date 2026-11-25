# S0 · policy.policy SampleRecord v3 验收

**通过。业务映射待确认。** 隔离版本 `01a21137-724b-7691-8a60-4fa110654627`（`s0-v3`）已发布，321,902 条、39 个原始字段。3 条真实样本通过顺序读取、点查、分页、字段保真、派生、原文定位和重启恢复核对。本轮执行输入解析，未启动正式算子评测。

## 数据版本与物理存储

P0 审计记录的版本为 `01a1a9a4-c688-7a18-a70b-927c73ab24e0`，其历史原件路径及版本行在当前本地工件目录和运行库未找到。实际使用 `data/政务数据集/policy.policy.jsonl`。该文件与本地由 `mysql://policy/policy` 捕获的 DRAFT `01a210c5-76a0-7b68-8271-3697054895a0` 快照经 `cmp` 比较逐字节相同，与 P0 记录的字节数、记录数和字段特征一致。P0 历史原件不可用，无法直接比对其 checksum。另一个数据集 `policy.policy_documents` 在 P0 为 42,464 条、17 字段，未参与发布。

以下路径相对于仓库根目录。执行前可用磁盘 213 GiB。

| 路径 | 字节 | 用途 |
|---|---:|---|
| `data/政务数据集/policy.policy.jsonl` | 5,900,359,205 | 现有原件 |
| `data/artifacts/datasets/6918ebcb-b32b-4ff2-a09e-e0420c0f9394/01a210c5-76a0-7b68-8271-3697054895a0/raw/mysql-snapshot.jsonl` | 5,900,359,205 | 已存在的独立副本，字节一致 |
| `data/runtime/policy-s0/artifacts/raw/policy.policy.jsonl` | 5,900,359,205（共用 inode） | v3 原件硬链接，新增原件字节为零 |
| `data/runtime/policy-s0/artifacts/raw/policy.policy.jsonl.qa-index/sample-v3-jsonl-1-01a21137-724b-7691-8a60-4fa110654627-46758c72d0541a829199efd08a9574f1cdf02de84499941c417893b770388f5c/records.sqlite3` | 74,969,088 | READY SQLite 索引 |
| `data/runtime/policy-s0/artifacts/raw/policy.policy.jsonl.qa-index/sample-v3-jsonl-1-01a21137-724b-7691-8a60-4fa110654627-46758c72d0541a829199efd08a9574f1cdf02de84499941c417893b770388f5c/metadata.json` | 902 | 记录数、源及索引摘要、状态 |
| `data/runtime/policy-s0/platform.sqlite3` | 1,019,904 | 隔离版本、Manifest、映射、Registry、5 条预览缓存 |
| `data/runtime/policy-s0/publication.json` | 509 | 发布凭据 |

- 原件 SHA-256 为 `sha256:46758c72d0541a829199efd08a9574f1cdf02de84499941c417893b770388f5c`。索引构建流式解析全部记录并验证完整源摘要，点查验证行摘要。索引 SHA-256 为 `sha256:2cd903cbf0b636c2be082f40ba3252c5acfca97c92eac694aa862b73510ffabb`。
- 发布复用 `DatasetVersionService.publish_raw_version`。隔离存储在索引构建时以临时硬链接提供原件，规避默认 LocalFileSystemArtifactStore.materialize 的临时全文件复制。该适配仅用于本轮，重读可用普通本地 Store。硬链接共享 inode，后续需按不可变工件保留。
- 持久化的是原件引用、Manifest、Schema 描述、MappingRevision、索引及其元数据。SampleRecord 和 ResolvedRecord 按需生成；预览 5 条记录和进程内短字符串转换结果是缓存。索引查询会短暂物化索引文件。没有生成全量 `sample_records.jsonl`。

## 契约及 Schema

```json
{
  "dataset_version_id": "01a21137-724b-7691-8a60-4fa110654627",
  "schema_version": "3.0",
  "source_uri": "raw/policy.policy.jsonl",
  "source_sha256": "sha256:46758c72d0541a829199efd08a9574f1cdf02de84499941c417893b770388f5c",
  "schema_fingerprint": "sha256:a5c2be95a69bac7d4caed259fd16fea75afc2e2e82cf0e48f99fa66e45949b24",
  "reader": "jsonl-utf8@1"
}
```

Reader 为 `jsonl-utf8@1`。SampleRecord JSON 顶层实际含 `id`（即点查使用的 sample_id）、`dataset_version_id`、`data` 和 `meta`。版本号在 Manifest 中。行 ID 为 UUIDv5，以 DatasetVersion UUID 为命名空间，以 `<source_sha256>:<byte_start>` 为名称。meta 记录原件 URI、SHA-256、字节起点、长度与物理行号。data 保存原始 JSON 键与类型，API 序列化直接使用行 JSON。相同内容出现在不同偏移时仍是不同记录。

Schema 复用本地同字节快照登记的全量字段描述，避免再做一次 Schema 扫描。三条验收记录逐键核对。下表按实际 JSON 类型列出全部字段。

- `string`：`iid`、`zccjfl`、`title`、`content`、`policyurl`、`href`、`organization`、`filenumber`、`isPublic`、`label`、`source`、`policyType`、`registnumber`、`xobject`、`xtheme`、`releaseunit`、`linkhref`、`zcjdbt`、`tjlj`、`tjbt`、`spjdlj`、`spjdbt`、`bmjdlj`、`bmjdt`、`qylj`、`qtjd`、`catalogid`、`level`、`issue`、`publishunit`、`type`。
- `null|string`：`pubtime`、`effectivetime`、`validity`、`status`、`attachname`、`attachurl`、`formattime`。
- `null`：`targetContent`。

## 映射、预览与恢复

映射确认通过现有 `MappingRevisionService.confirm` 写入 `data/runtime/policy-s0/platform.sqlite3` 的 `mapping_revisions` 表，键为版本 ID 和 `sha256:2045c24d7af7010e0d6b3dd543b5ceca7eeb93afc5eef9c26b39dbc782db90bf`。该修订只包含已有依据的绑定。源预览经 `inspect_uploaded_source` 返回 39 个字段和 3 条限长样本，命中 `policy.policy` 模板。模板中的业务映射建议没有一并确认。

| 逻辑语义 | 原始字段 | 表示及处理 |
|---|---|---|
| `document.title` | `title` | 原生纯文本 |
| `document.body.html` | `content` | 原始 HTML，三条样本均含标签 |
| `document.body.plain` | `content` | 通过 `html_to_plain@1.0.0` 按需从 HTML 派生 |

本快照 Schema 没有原生纯文本正文字段。码表和状态含义（`type / policyType / level / zccjfl / status / validity / isPublic`）、日期含义及单位（`pubtime / effectivetime / formattime`）、发布主体及文号优先级、URL 回退、附件配对和解读关联均待业务确认。全部原值继续可见。三条中 `targetContent` 为 NULL，`policyType` 为空串 EMPTY，未出现的字段 `plain_text_content` 为 MISSING；输入契约没有绑定时是 UNBOUND。字符串 `None` 与 `无` 保持字面值。

顺序读取使用原件物理顺序。分页连续两页各 2 条，按 ID 排序、无重复；分页顺序和原件行序各自独立。对原件第 1–3 行，点查响应逐字节等于顺序读取的响应；按 offset/length 回读原件的 JSON 与 data 逐字段相等。标题首字符以 Unicode code point 区间验得 EXACT。派生正文保留源字段与转换摘要，派生文本字符区间没有声明为原 HTML 坐标。

关闭连接、重建数据库引擎和服务实例后，重读持久化版本与映射，并再次经独立进程复验。分页 ID、3 条记录、FrozenInputPlan、各 ResolvedRecord 的值、状态与来源完全一致。FrozenInputPlan 指纹为 `sha256:f4114e8674d81a29141926c1047f83a5d71ecc74e4f849df37f350a4a9f5a652`。

## 三条实际记录及解析结果

以下由实际 `response_json()` 生成；完整保留顶层、定位信息和 39 个原始键。非空原始字符串统一脱敏，保留原始字符数；HTML 字段保留脱敏前字符数及 HTML 表示说明。空串、null、字段类型、行号、字节定位及平台生成 ID 原样保留。每段 JSON 的 `data` 对应从原件切出的完整 JSON 对象。ResolvedRecord 由仓库 Registry 中的 `text_completeness@1.1.0` 真实 Input Contract 解析，正文以同样方式脱敏，显示来源和可复现摘要。

### 原件第 1 行

```json
{
  "id": "36cc81a5-f97a-5856-84bc-67afe4ac36f7",
  "dataset_version_id": "01a21137-724b-7691-8a60-4fa110654627",
  "meta": {
    "artifact_uri": "raw/policy.policy.jsonl",
    "artifact_sha256": "sha256:46758c72d0541a829199efd08a9574f1cdf02de84499941c417893b770388f5c",
    "byte_start": 0,
    "byte_length": 3810,
    "line_number": 1
  },
  "data": {
    "iid": "[脱敏：32 字符]",
    "zccjfl": "[脱敏：1 字符]",
    "title": "[脱敏：51 字符]",
    "content": "[脱敏：HTML，2298 字符]",
    "policyurl": "[脱敏：67 字符]",
    "pubtime": "[脱敏：13 字符]",
    "effectivetime": null,
    "href": "",
    "organization": "[脱敏：10 字符]",
    "filenumber": "[脱敏：14 字符]",
    "isPublic": "[脱敏：4 字符]",
    "label": "",
    "source": "[脱敏：3 字符]",
    "policyType": "",
    "registnumber": "",
    "xobject": "[脱敏：3 字符]",
    "xtheme": "[脱敏：7 字符]",
    "releaseunit": "",
    "validity": "[脱敏：1 字符]",
    "linkhref": "",
    "zcjdbt": "",
    "tjlj": "",
    "tjbt": "",
    "spjdlj": "",
    "spjdbt": "",
    "bmjdlj": "",
    "bmjdt": "",
    "qylj": "",
    "qtjd": "",
    "catalogid": "[脱敏：2 字符]",
    "level": "[脱敏：1 字符]",
    "status": "[脱敏：1 字符]",
    "attachname": "",
    "attachurl": "",
    "issue": "[脱敏：14 字符]",
    "publishunit": "[脱敏：10 字符]",
    "type": "[脱敏：2 字符]",
    "targetContent": null,
    "formattime": "[脱敏：19 字符]"
  }
}
```

```json
{
  "sample_id": "36cc81a5-f97a-5856-84bc-67afe4ac36f7",
  "values": {
    "body": {
      "status": "PRESENT",
      "value": "[脱敏：210 字符]",
      "origin": {
        "source_sha256": "sha256:46758c72d0541a829199efd08a9574f1cdf02de84499941c417893b770388f5c",
        "byte_start": 0,
        "field_tokens": [
          "content"
        ],
        "transform": "html_to_plain@1.0.0",
        "transform_digest": "sha256:b7908fd0537ba7cb653e10851521e00fe4f552ebbd28fe57da77c3815c63631a",
        "text_sha256": "sha256:9a28a05e4ba6dc4beb554477cb942640a248ae602c27c95575e017e7b306452a"
      }
    }
  }
}
```

### 原件第 2 行

```json
{
  "id": "225b1f0f-b38f-5704-8d0f-f25a418e8173",
  "dataset_version_id": "01a21137-724b-7691-8a60-4fa110654627",
  "meta": {
    "artifact_uri": "raw/policy.policy.jsonl",
    "artifact_sha256": "sha256:46758c72d0541a829199efd08a9574f1cdf02de84499941c417893b770388f5c",
    "byte_start": 3810,
    "byte_length": 2359,
    "line_number": 2
  },
  "data": {
    "iid": "[脱敏：32 字符]",
    "zccjfl": "[脱敏：1 字符]",
    "title": "[脱敏：34 字符]",
    "content": "[脱敏：HTML，920 字符]",
    "policyurl": "[脱敏：62 字符]",
    "pubtime": "[脱敏：13 字符]",
    "effectivetime": null,
    "href": "",
    "organization": "[脱敏：6 字符]",
    "filenumber": "[脱敏：14 字符]",
    "isPublic": "[脱敏：4 字符]",
    "label": "",
    "source": "[脱敏：3 字符]",
    "policyType": "",
    "registnumber": "",
    "xobject": "[脱敏：7 字符]",
    "xtheme": "",
    "releaseunit": "[脱敏：15 字符]",
    "validity": "[脱敏：1 字符]",
    "linkhref": "",
    "zcjdbt": "",
    "tjlj": "",
    "tjbt": "",
    "spjdlj": "",
    "spjdbt": "",
    "bmjdlj": "",
    "bmjdt": "",
    "qylj": "",
    "qtjd": "",
    "catalogid": "[脱敏：2 字符]",
    "level": "[脱敏：1 字符]",
    "status": null,
    "attachname": "",
    "attachurl": "",
    "issue": "[脱敏：14 字符]",
    "publishunit": "[脱敏：6 字符]",
    "type": "[脱敏：2 字符]",
    "targetContent": null,
    "formattime": "[脱敏：19 字符]"
  }
}
```

```json
{
  "sample_id": "225b1f0f-b38f-5704-8d0f-f25a418e8173",
  "values": {
    "body": {
      "status": "PRESENT",
      "value": "[脱敏：229 字符]",
      "origin": {
        "source_sha256": "sha256:46758c72d0541a829199efd08a9574f1cdf02de84499941c417893b770388f5c",
        "byte_start": 3810,
        "field_tokens": [
          "content"
        ],
        "transform": "html_to_plain@1.0.0",
        "transform_digest": "sha256:b7908fd0537ba7cb653e10851521e00fe4f552ebbd28fe57da77c3815c63631a",
        "text_sha256": "sha256:da218542facf5222bcda5356a310e55931e913ba9172545fa953f7eaf0bdacfb"
      }
    }
  }
}
```

### 原件第 3 行

```json
{
  "id": "7df67b37-436b-5448-a837-066738dbbb6c",
  "dataset_version_id": "01a21137-724b-7691-8a60-4fa110654627",
  "meta": {
    "artifact_uri": "raw/policy.policy.jsonl",
    "artifact_sha256": "sha256:46758c72d0541a829199efd08a9574f1cdf02de84499941c417893b770388f5c",
    "byte_start": 6169,
    "byte_length": 2711,
    "line_number": 3
  },
  "data": {
    "iid": "[脱敏：32 字符]",
    "zccjfl": "[脱敏：1 字符]",
    "title": "[脱敏：30 字符]",
    "content": "[脱敏：HTML，1016 字符]",
    "policyurl": "[脱敏：60 字符]",
    "pubtime": "[脱敏：13 字符]",
    "effectivetime": null,
    "href": "",
    "organization": "[脱敏：6 字符]",
    "filenumber": "[脱敏：13 字符]",
    "isPublic": "[脱敏：4 字符]",
    "label": "",
    "source": "[脱敏：3 字符]",
    "policyType": "",
    "registnumber": "",
    "xobject": "[脱敏：7 字符]",
    "xtheme": "[脱敏：7 字符]",
    "releaseunit": "[脱敏：15 字符]",
    "validity": "[脱敏：1 字符]",
    "linkhref": "",
    "zcjdbt": "",
    "tjlj": "",
    "tjbt": "",
    "spjdlj": "",
    "spjdbt": "",
    "bmjdlj": "",
    "bmjdt": "",
    "qylj": "",
    "qtjd": "",
    "catalogid": "[脱敏：2 字符]",
    "level": "[脱敏：1 字符]",
    "status": "[脱敏：1 字符]",
    "attachname": "",
    "attachurl": "",
    "issue": "[脱敏：13 字符]",
    "publishunit": "[脱敏：6 字符]",
    "type": "[脱敏：2 字符]",
    "targetContent": null,
    "formattime": "[脱敏：19 字符]"
  }
}
```

```json
{
  "sample_id": "7df67b37-436b-5448-a837-066738dbbb6c",
  "values": {
    "body": {
      "status": "PRESENT",
      "value": "[脱敏：319 字符]",
      "origin": {
        "source_sha256": "sha256:46758c72d0541a829199efd08a9574f1cdf02de84499941c417893b770388f5c",
        "byte_start": 6169,
        "field_tokens": [
          "content"
        ],
        "transform": "html_to_plain@1.0.0",
        "transform_digest": "sha256:b7908fd0537ba7cb653e10851521e00fe4f552ebbd28fe57da77c3815c63631a",
        "text_sha256": "sha256:b9d15baecee692549465c36346bf5b6a29cf8f4e4c3595a7bf96ec1c16adf28c"
      }
    }
  }
}
```


## 输入覆盖与测试

[输入覆盖矩阵](S0-input-coverage.csv) 来自隔离 SQLite 经实际 Registry 同步后的 36 条指标版本 / 算子版本绑定，包含 0208 文本子指标。现有运行库只读核对为 35 条，差异是仓库中的 `02080104 / text_completeness@1.1.0`。矩阵区分启用、占位、禁用和 v3 Resolver 实测；旧版需求来自描述符和 P0 已核对的执行代码。样本的物理字段及派生值旁证不能替代旧契约适配。没有 v3 逻辑绑定的输入、记录值状态、未提供或未核验的独立输入以及不适用的算子分别标记。未做业务规则、全文质量或性能验收。

定向 Reader、Resolver 和 v3 发布集成测试 **11 通过**，1 条现有 Starlette TestClient 弃用提示。本次真实数据验收另走隔离发布、源预览、索引分页与点查、原文定位和重建服务的 Resolver 路径。无需改动平台代码。未连接远程数据库、调用模型、改写历史工件或使用生产默认发布入口。下一阶段以用户确认 SampleRecord 和语义映射为准。
