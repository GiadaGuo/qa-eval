# 本地持久化迁移阶段 3 实施记录

> 执行日期：2026-11-04（Asia/Shanghai）  
> 对应计划：[`local-persistence-migration-plan.md`](local-persistence-migration-plan.md)  
> 结论：阶段 3 已完成；仅重建影响新业务流程的 Registry，未迁移残缺历史数据。

## 1. 迁移选择原则

本阶段不以“旧库有数据”为迁移条件，而以“迁移后是否仍能形成完整、可执行的业务流程”为条件：

- 运行新评测所必需的 Registry 必须存在。
- 缺少原始/标准化 Artifact 的 Dataset Version 不迁移。
- 指向被排除 Dataset Version 的契约、计划、运行、结果和报告不迁移。
- 缺少原文 Artifact 的文档版本和文档报告不迁移。
- 依赖被排除版本的 Agent 会话、消息和调用审计不迁移。
- 可从仓库 canonical config 重建的数据优先重建，不复制可能陈旧的数据库快照或 code digest。

## 2. 源数据审计结论

旧 PostgreSQL revision 为 `c9f4a21d8e73`。

### Artifact 完整性

- Dataset/Document 记录共引用 27 个 Artifact。
- 本地引用：27。
- 当前存在：0。
- 当前缺失：27。
- S3 引用：0。

因此旧库中的 7 个 Dataset Version（其中 6 个 PUBLISHED）、1 个 Document Version
都不具备完整业务输入。只复制元数据会产生能展示但无法预览、评测、复算或读取报告的幽灵记录。

### 纳入迁移

| 表 | 源数量 | 目标数量 | 处理方式 |
| --- | ---: | ---: | --- |
| `metric_definitions` | 15 | 15 | 从 `configs/registry` 重建 |
| `calculator_implementations` | 16 | 16 | 从当前 descriptor 重建 |
| `metric_calculator_bindings` | 16 | 16 | 从当前 binding 重建 |

源/目标 Registry 业务键比较结果：`source_only=[]`、`target_only=[]`。

### 排除迁移

主要排除数据：

| 数据组 | 源数据量 | 排除原因 |
| --- | ---: | --- |
| Dataset / Version / Profile | 3 / 7 / 6 | 27 个 Artifact 引用全部缺失 |
| Quality Contract | 34 | 指向被排除 Dataset Version |
| Evaluation Run / Stage / Metric Result | 9 / 46 / 43 | 历史执行状态且输入不可复现 |
| Quality Issue / Report / Scored Report | 183 / 6 / 2 | 历史派生结果，部分引用 Artifact |
| Document Version / Task / Report | 1 / 13 / 2 | 原始文档 Artifact 缺失 |
| Agent Session / Message | 16 / 90 | 指向被排除版本的历史审计数据 |
| Skill / Tool Invocation / Proposal | 32 / 506 / 32 | 历史审计和提案，不影响新流程 |

完整逐表数量与排除原因保存在机器可读迁移报告中。

## 3. 迁移工具

新增 `scripts/migrate_business_data_to_sqlite.py`，具备：

- 默认 dry-run，只有 `--apply` 才写目标库。
- 强制源为 PostgreSQL、目标为 SQLite。
- 强制目标 revision 为 `c9f4a21d8e73`。
- apply 前强制目标所有业务表为空。
- 盘点源库逐表行数和 Artifact 可用性。
- 从 canonical config 原子重建 Registry。
- 比较源/目标 Registry 业务键。
- 执行 SQLite `integrity_check` 和 `foreign_key_check`。
- 输出 JSON 报告；报告不包含连接串或凭据。

执行方式：

```bash
# .env 中 QA_EVAL_DATABASE_URL 指向旧 PostgreSQL 时
.venv/bin/python scripts/migrate_business_data_to_sqlite.py \
  --report data/migration/stage3-dry-run.json

.venv/bin/python scripts/migrate_business_data_to_sqlite.py \
  --apply \
  --report data/migration/stage3-apply.json
```

## 4. 正式执行结果

阶段 2 的 smoke SQLite 和 8 个 smoke Artifact 已按迁移前置要求清理。目标数据库重新从
revision 0 升级到 head 后执行选择性迁移。

| 验证项 | 结果 |
| --- | --- |
| 目标迁移前业务表为空 | 通过 |
| Registry sync | 15 metrics / 16 calculators / 16 bindings |
| Registry 源/目标业务键 | 完全一致 |
| SQLite revision | `c9f4a21d8e73` |
| SQLite integrity check | `ok` |
| SQLite foreign key errors | 0 |
| 目标 Dataset / Version | 0 / 0 |
| 目标 Contract / Run / Report | 0 / 0 / 0 |
| 目标 Agent Session | 0 |
| 目标 Artifact 文件 | 0 |
| 迁移报告 success | `true` |

迁移报告：

- `data/migration/stage3-dry-run.json`  
  SHA-256：`f66a008d8afe3419b6140d24343a774460078d56566f9e7d083639cbbe0dcac6`
- `data/migration/stage3-apply.json`  
  SHA-256：`0ed6d60cf0d5eb881d6f98738215872f3930a803a95ba42723e3a44180b72a02`

报告位于被 Git 忽略的 `data/` 下，保留在本机用于阶段 4 观察和回查。

## 5. 业务验证

- 本地健康检查：SQLite 和 local ArtifactStore 均可写。
- Registry readiness：`ready=true`。
- API 指标列表：15 项。
- API 数据集列表：空，符合选择性迁移结果。
- Unit + contract + 上传—评测—报告 E2E：292 项全部通过。
- 迁移工具 Ruff 和 `git diff --check`：通过。

## 6. 回滚与边界

- 旧 PostgreSQL 未写入、未删除，仍可按阶段 0 备份回查。
- MinIO 和 Docker volume 未由本阶段删除。
- 本地 SQLite 当前是正式选择性迁移结果，不包含阶段 2 smoke 业务数据。
- Artifact 没有执行 URI 映射，因为纳入迁移的数据不引用 Artifact。
- 如未来必须恢复某个旧数据集，应先从外部备份恢复其全部 Artifact 并校验 SHA-256，再单独设计该数据集及完整依赖闭包的迁移；不能只补数据库行。
