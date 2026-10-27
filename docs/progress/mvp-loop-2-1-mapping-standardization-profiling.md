# MVP 闭环 2.1：Field Mapping、完整标准化与独立 Dataset Profiler

- 日期：2026-10-27
- 状态：已完成
- 前置闭环：`mvp-loop-2-upload-and-profile.md`

## 修复目标

闭环 2 原实现只接受固定 `sample_id/text`，只生成 `article.body.plain`，并把少量画像统计嵌在 normalizer 中。本次修复使字段映射真正驱动 SampleRecord 2.0，并把 Dataset Profiler 拆成独立组件；Profile 仍在每个 Dataset Version 详情的 Profile 标签页展示，不增加 dataset 级“最新版本画像”按钮。

## 已完成

### Field Mapping

- 增加版本级 `PUT/GET /api/v1/dataset-versions/{version_id}/field-mapping`。
- 仅 DRAFT 版本允许修改；要求恰好一个 SAMPLE_ID 和至少一个 CONTENT。
- 映射作为 Dataset Version 草稿配置持久化，并绑定到当前上传快照；重新上传 raw artifact 会使旧映射失效。
- 增加 `GET /api/v1/dataset-versions/{version_id}/source-inspection`，全量扫描已上传 JSONL 推断字段集合、JSON 类型、缺失/NULL 情况，并返回有界样本预览。
- 字段映射页只读取当前 Dataset Version 已上传的 JSONL artifact，不再依赖实时 MySQL schema/preview。
- 数据集版本列表按“上传 JSONL → 字段映射 → 标准化并发布”展示准备进度并设置操作门禁。
- 后端同样强制先上传、后保存映射，避免绕过前端造成错误状态。
- 未配置 Mapping 的版本禁止发布。

### 完整标准化

- 上传边界接受任意 UTF-8 JSON object JSONL，不再硬编码 `sample_id/text`。
- normalizer 消费已保存的 FieldMappingSpec，支持 SAMPLE_ID、CONTENT、METADATA、SOURCE_ATTRIBUTE、TEMPORAL、PASSTHROUGH 和 IGNORE。
- 一个源记录可以生成 title、summary、HTML、plain text 等多个 ContentItem，并保留 metadata、source attributes 与 lineage。
- NULL、MISSING、空字符串继续由 SampleRecord 2.0 明确区分。

### 独立 Dataset Profiler

- 新增 `BaseDatasetProfiler`，输入为 DatasetManifest 2.0 和完整 SampleRecord 2.0 artifact。
- Profiler 在版本发布完成标准化后独立执行全量扫描；normalizer 不再生成 Profile。
- Profile 包含规模/格式/大小、模态和 role、presence/missingness、文本长度 min/max/mean/p50/p95、声明语言分布、应用上下文和 profiling metadata。
- Profile 只描述事实，不产生 PASS/FAIL。
- Profile ORM 支持同一 Dataset Version 按 profiler version/config 保留历史结果；详情页仍读取当前最新结果。

## 数据流

```text
MySQL 或其他来源 -> 导出/上传固定 JSONL snapshot
uploaded JSONL schema + preview -> FieldMappingSpec 2.0
uploaded JSONL + FieldMappingSpec -> SampleRecord 2.0 / RejectedRecord
DatasetManifest + SampleRecord artifact -> BaseDatasetProfiler -> DatasetProfile
```

MySQL 页面仅属于上游数据源连接与快照导出能力；一旦 JSONL 被上传到 Dataset Version，后续映射和发布都以该不可变版本产物为准。

## 验证

- 完整文章字段映射测试验证 `article.title`、`article.body.html`、`article.body.plain`、metadata、temporal 和 source attributes。
- Profile 测试验证独立 profiler、样本数、content roles 和长度分位数。
- contract/schema、上传 API、ArtifactStore 相关测试通过。
- React lint/build 通过。
- `DatasetVersionService` 单测：5 passed；Python Ruff 检查通过。

## 运行前迁移

```bash
uv run alembic upgrade head
```

