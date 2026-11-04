# 本地持久化迁移阶段 0 基线记录

> 执行日期：2026-11-04（Asia/Shanghai）  
> 对应计划：[`local-persistence-migration-plan.md`](local-persistence-migration-plan.md)  
> 结论：阶段 0 已完成；旧业务数据不进入后续迁移范围。

## 1. 冻结范围与决策

- 基线时点起，不再要求迁移当前 PostgreSQL、MinIO 或本地 Artifact 中的旧业务数据。
- 用户于 2026-11-04 明确允许放弃并删除本地现有 Artifact 数据。
- 已删除 `data/artifacts/` 中 138 个文件（约 2.7 GB）以及为其临时生成的约 2.7 GB 归档，并重建空目录 `data/artifacts/.staging/`。
- PostgreSQL 逻辑备份继续保留，作为阶段 0 schema/数据基线和短期回查材料；它不是后续 SQLite 导入的强制输入。
- MinIO bucket 仅有一个 29 B 测试对象，已导出保留；未清理 MinIO volume。
- 未停止或删除 PostgreSQL、Redis、MinIO 容器和 volume。
- 未对 PostgreSQL 设置全局只读，避免阻塞后续 schema 兼容开发；后续阶段不得再假设基线时点之后新增的数据会被迁移。

执行检查时 PostgreSQL 没有非 idle 的其他应用连接。数据库中有 3 条 `RUNNING` Evaluation Run，均开始于 2026-10-29，属于历史遗留状态，不代表本次冻结时仍有活跃 worker。本阶段不静默修复这些业务状态。

## 2. 代码与运行环境基线

| 项目 | 值 |
| --- | --- |
| Git commit | `b18e3abaefba8f4f7414e503f7d896d15b25427d` |
| 工作树 | 非干净；执行时 `git status --porcelain` 共 81 项 |
| Python | 3.12.14 |
| pytest | 9.1.1 |
| PostgreSQL | 16.15 (Debian 16.15-1.pgdg13+2) |
| Alembic revision | `c9f4a21d8e73` |
| 数据库 | `qa_eval` |
| 数据库只读状态 | `transaction_read_only=off` |
| ArtifactStore | `.env` 未显式配置，使用默认 `local` |
| Artifact 根目录 | 默认 `data/artifacts`，现已清空 |
| S3/MinIO 应用配置 | 未配置 bucket，当前核心链路不使用 MinIO |
| Docker 服务 | PostgreSQL、Redis、MinIO 均为 healthy |

工作树已有大量用户改动，阶段 0 没有重置、暂存或修改这些改动。Git commit 只能标识代码基点，测试结果以执行时工作树为准。

## 3. PostgreSQL 表与行数

共 27 张 public 表：

| 表 | 行数 |
| --- | ---: |
| `agent_sessions` | 16 |
| `alembic_version` | 1 |
| `calculator_implementations` | 16 |
| `conversation_messages` | 90 |
| `dataset_manifests` | 0 |
| `dataset_profiles` | 6 |
| `dataset_versions` | 7 |
| `datasets` | 3 |
| `document_quality_reports` | 2 |
| `document_report_tasks` | 13 |
| `document_versions` | 1 |
| `evaluation_runs` | 9 |
| `issue_indices` | 36 |
| `logical_evaluation_plans` | 13 |
| `metric_calculator_bindings` | 16 |
| `metric_definitions` | 15 |
| `metric_results` | 43 |
| `physical_evaluation_plans` | 9 |
| `proposal_revisions` | 32 |
| `quality_contract_versions` | 34 |
| `quality_issues` | 183 |
| `quality_reports` | 6 |
| `rule_evaluations` | 36 |
| `scored_quality_reports` | 2 |
| `skill_invocations` | 32 |
| `stage_runs` | 46 |
| `tool_invocations` | 506 |

状态补充：

- `evaluation_runs`：`SUCCEEDED=5`、`PARTIAL_SUCCEEDED=1`、`RUNNING=3`。
- `document_report_tasks`：`FAILED=11`、`PARTIAL_SUCCEEDED=2`。
- `agent_sessions`：`ACTIVE=13`、`COMPLETED=3`。

## 4. 备份清单与验证

备份目录为 `data/backup/stage0-20261104/`，该目录被项目的 `data/` ignore 规则排除，不进入 Git。

| 文件 | 用途 | SHA-256 |
| --- | --- | --- |
| `postgres/qa-eval.dump` | PostgreSQL custom-format 逻辑备份 | `d41bfcccedc4733664a288e084e9cf9635868e429e48c8ee3e6dfc9ab1864ed0` |
| `minio/qa-eval-test.txt` | MinIO bucket 唯一测试对象 | `777169163f816c534ab925c26c4c2fbfc72b9321ccdbebf0ade3b6f36932d90b` |
| `pytest-unit-contract.xml` | unit + contract JUnit 结果 | `423d33ff5104c421e0c0f7307d0ef448ad10e2fa3a0db38b2b4b3406d0b28f47` |
| `pytest-integration-agent-session-remainder.xml` | Agent Session 非卡点用例结果 | `5fd6d8f337e4e8bc63becade8947717d27f7138e9d81ea2461a73ffeb56ac06b` |
| `pytest-integration-remainder.xml` | 其他 integration 结果 | `54f3a78a0dbabaaabf699b110b8aec71c13b24b96a99090d7eb4c5b97eb4d3d6` |

验证结果：

- `pg_restore --list` 成功读取 PostgreSQL dump，识别到 197 个 TOC entry；dump 与源库均为 PostgreSQL 16.15。
- MinIO 导出对象大小和 SHA-256 已记录。
- 本地 Artifact 备份由用户明确豁免，临时归档已随原数据一并删除。
- 备份目录当前约 2.4 MB。

该备份位于源项目同一磁盘，只能作为迁移基线和短期回查副本，不能替代异盘灾备。

## 5. 测试基线

### 5.1 Unit + contract

```text
288 passed, 1 warning, 1.67s
```

唯一 warning 为 Starlette `TestClient` 关于 `httpx` 的弃用提示。

### 5.2 Integration

122 个用例的唯一用例口径汇总：

| 结果 | 数量 |
| --- | ---: |
| 通过 | 114 |
| 失败 | 1 |
| 卡住并人工终止 | 1 |
| 条件跳过 | 6 |

明确失败：

- `tests/integration/test_evaluation_run_service.py::test_formal_run_executes_calculator_and_persists_metric_result`
- 断言期望 `issue_total == 3`，实际为 `4`。

明确卡点：

- `tests/integration/test_agent_session_api.py::test_agent_message_runs_skill_and_real_tools_then_restores_trace`
- 完整 integration 首次运行时，前三项通过后卡在该用例超过 6 分钟，进程被终止。
- 同文件排除该用例后，其余 8 项均通过，说明卡点已定位到单个用例。

条件跳过：

- `tests/integration/test_mysql_datasource.py` 的 6 项真实 MySQL 测试因未设置 `RUN_MYSQL_INTEGRATION=1` 跳过。

其余 integration 分组结果为 `106 passed, 1 failed, 6 skipped`；Agent Session 非卡点分组为 `8 passed, 1 deselected`。首次完整运行中前三项的通过结果包含在后续 8 项重跑结果内，汇总时未重复计数。

## 6. 阶段 0 完成判定

- [x] 已记录 Alembic revision、PostgreSQL 表清单和逐表行数。
- [x] PostgreSQL 已生成逻辑备份并验证可由 `pg_restore` 读取。
- [x] MinIO 内容已盘点并导出。
- [x] 本地 Artifact 经用户确认放弃，已清理并记录豁免，不再作为迁移输入。
- [x] Unit、contract、integration 基线均有明确结果；失败、跳过和卡点已记录。
- [x] 已确认基线时点没有活跃数据库写连接。
- [x] 已明确冻结边界：基线之后的旧存储数据不承诺进入 SQLite，不开展新旧系统双写。

阶段 1 可以开始。阶段 1 应使用临时 SQLite/测试数据库推进，不应依赖或修改上述 PostgreSQL dump。
