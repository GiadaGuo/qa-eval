# 下一阶段闭环 1.5：最小代表性指标切片验收记录

- 状态：已完成
- 验收日期：2026-10-28（Asia/Shanghai）
- 环境：本地开发 PostgreSQL `qa_eval` + 配置的本地 ArtifactStore
- Migration：`e6b5c3074f89 (head)`，单一 head

## 1. 实现结果

正式 Registry 现包含 2 个 Metric、3 个 Calculator 和 3 个 Binding：

- `0204@1.0.0` + `structural_completeness@1.0.0`：支持显式 content role 或 metadata field；分别保留 `FIELD_ABSENT`、`NULL`、`EMPTY_STRING` 和 `PRESENT`，只计算客观完整性事实，不内置合理缺失阈值。
- `02080103@1.0.0` + `exact_text_duplicate@1.0.0`：只计算 normalized whole-record exact duplicate，复用平台正式 normalization/extraction 实现；不声称覆盖 n-gram、MinHash、段落或语义重复。
- `02080103@1.0.0` + `text_duplicate_degree@2.0.0`：保留原有较宽 semantic coverage。两个实现通过独立 key、version、entrypoint、digest、参数、成本配置与 semantic coverage 区分。
- 图像候选仅位于 `tests/fixtures/registry/image_metric_candidate.json`，带 `fixture_only` 标记，未进入正式 Registry。

结果层的 Issue detector 同步收紧为仅处理 `02080103`；其他已成功计算但没有专属 detector 的 Metric 会生成空 IssueIndex 和正常 Report，不再被误判为缺失重复组证据。exact-only 的重复组 evidence 可生成 confirmed exact issues，但不会伪造 near-duplicate candidates。

## 2. 自动化质量门

| 门禁 | 结果 |
| --- | --- |
| 完整 pytest（含本地 `qa_eval_test` PostgreSQL） | `196 passed, 6 skipped`；6 项均为需显式启用的 MySQL integration |
| Ruff | 通过 |
| canonical schema drift | 通过；Calculator Descriptor schema 已同步 |
| 前端 lint | 通过 |
| 前端 production build | 通过；仅保留既有 chunk size warning |

新增回归覆盖：

- `0204` 四类 presence/missing 状态不混淆，evidence 不复制原始正文。
- selector 缺失或同时提供时返回结构化 Calculator validation error。
- exact-only 只输出两个 exact measurements。
- 固定输入上 exact-only 与完整版的 exact observations 相同。
- Registry sync/readiness、Contract、Physical Plan、Run、MetricResult、Evidence、Rule、IssueIndex 和 Report 全链路。

## 3. 真实运行冒烟

Registry Sync 结果为 `2 metrics / 3 calculators / 3 bindings`；readiness 通过。验收时记录的 Registry readiness fingerprint：

`sha256:438fea8fe1391d3f83b92df7dec22dbc9a7961c0ca613a76609c6bd1b2582659`

使用已发布 Dataset Version：

`01a17c8d-b995-7a9c-801a-73e6551bc2ab`

### 3.1 `0204` structural completeness

- Calculator digest：`sha256:6ae27c7b438bb7d41d2230419db2cae3df48ec12d1a4fefba1256f7a04413751`
- Contract Version：`01a1804d-b1fb-755c-94c7-9ab217972e55`
- Run：`01a1804d-b1ff-7726-818f-b9f68c8f432b`
- StageRun：`01a1804d-b207-7ecb-8d71-510ceaa3dc9b`
- MetricResult：`01a1804d-b599-7a49-80db-5af8328f9307`，状态 `OK`
- `complete_record_ratio = 0.9978609625668449`
- Evidence：1；Rule：`PASS`；Issue：0
- Report：`01a1804d-b5af-795f-9ffa-8b65c12506ad`，execution `SUCCEEDED`，gate `PASS`

### 3.2 `02080103` exact-only

- Calculator digest：`sha256:d453dfb0f8c022117349e76149939208c3bb42399a866dddc27f3a2ffb05ce00`
- Contract Version：`01a1804d-b5bb-77b2-abe6-42dbac79b7e2`
- Run：`01a1804d-b5bd-7f57-9926-e5c1200d2487`
- StageRun：`01a1804d-b5c2-79a6-b091-739d4ec57b90`
- MetricResult：`01a1804d-bd90-7996-853f-756e023f4535`，状态 `OK`
- `exact_affected_record_ratio = 0.005961950696677385`
- Evidence：1；Rule：`PASS`；confirmed Issue：45
- Report：`01a1804d-bda6-72b8-b3bc-84b6a1a8f455`，execution `SUCCEEDED`，gate `PASS`

### 3.3 与完整版 Calculator 的真实回归

同一 Dataset Version 新建 `text_duplicate_degree@2.0.0` Contract/Run：

- Contract Version：`01a1804e-01ea-7107-a5d3-482f1d48601f`
- Run：`01a1804e-01f0-7e2b-b977-07170dbc3863`
- StageRun：`01a1804e-01f8-7e4d-bc61-7c638130023e`
- MetricResult：`01a1804e-47c2-73c0-be1b-728cb66bc203`
- Report：`01a1804e-4a0e-7724-84bf-ab1d1a2be396`
- Run 与 Report 均为 `SUCCEEDED`
- exact-only 与完整版一致：
  - `exact_affected_record_ratio = 0.005961950696677385`
  - `exact_redundant_record_ratio = 0.0030144694533762056`

第一次 exact-only 真实预检暴露了 population exclusions 仍携带逐样本对象、与 canonical `MetricPopulation` 不兼容的问题。实现已改为聚合 exclusion count，样本 ID 仅保留在受控 evidence 中；修复后重新同步 Registry 并以全新 Contract/Run 完成上述验收，失败 Run 未被当作成功证据。

## 4. 完成结论

闭环 1.5 的完成定义全部满足：正式 Registry 具备两个可执行 Metric，`02080103` 具备两个可独立选择且语义覆盖不同的 Calculator；缺失类别、计算错误与质量结论保持分离；测试图像候选未污染正式 Registry；自动化门禁和真实 PostgreSQL/ArtifactStore 冒烟均通过。
