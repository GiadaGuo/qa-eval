# 本地持久化迁移阶段 4 切换与观察记录

> 执行日期：2026-11-04（Asia/Shanghai）  
> 对应计划：[`local-persistence-migration-plan.md`](local-persistence-migration-plan.md)  
> 结论：阶段 4 已完成；本机正式切换到 SQLite + local ArtifactStore，Docker 运行依赖已停止。

## 1. 正式切换

本机 `.env` 已切换为：

```dotenv
QA_EVAL_DATABASE_URL=sqlite:///data/runtime/qa-eval.sqlite3
QA_EVAL_TEST_DATABASE_URL=sqlite:///data/runtime/qa-eval-test.sqlite3
QA_EVAL_ARTIFACT_STORE_TYPE=local
QA_EVAL_ARTIFACT_STORE_LOCAL_ROOT=data/artifacts
```

`.env` 中其他 LLM、MySQL 等可选配置保持不变，未写入文档或日志。

不带任何临时环境覆盖执行 `alembic current`，结果为 SQLite
`c9f4a21d8e73 (head)`，证明普通命令和启动流程已经读取正式本地配置。

## 2. Docker 停用状态

已执行 `docker compose ... stop`，以下容器均为正常退出状态：

| 服务 | 状态 |
| --- | --- |
| PostgreSQL | `Exited (0)` |
| Redis | `Exited (0)` |
| MinIO | `Exited (0)` |

容器、named volume、阶段 0 PostgreSQL custom-format dump均未删除。此次停止可恢复，尚未执行最终清理。

## 3. 观察任务

在正式 `.env`、正式 SQLite 和正式 `data/artifacts` 上创建一个 7 条文本记录的数据集，连续执行三个真实 Calculator：

| Calculator | Metric | 结果 |
| --- | --- | --- |
| `text_duplicate_degree@2.0.0` | `02080103` | SUCCEEDED |
| `exact_text_duplicate@1.0.0` | `02080103` | SUCCEEDED |
| `structural_completeness@1.0.0` | `0204` | SUCCEEDED |

观察结果：

- Evaluation Run：`SUCCEEDED=3`。
- Stage Run：`SUCCEEDED=3`。
- Metric Result：`OK=3`。
- Quality Report：3 份。
- 未出现 `database is locked` 或不可恢复事务错误。
- 没有残留 `PENDING`、`RUNNING`、`FAILED` 任务。

## 4. 重启验证

关闭任务进程后，使用以下普通命令启动 API，没有传数据库或 ArtifactStore 覆盖变量：

```bash
.venv/bin/uvicorn apps.api.main:app \
  --host 127.0.0.1 --port 18000 --workers 1
```

验证结果：

- API 正常启动，启动清扫无异常。
- `/api/v1/health` 返回 `database.dialect=sqlite`、数据库与 ArtifactStore 均可写。
- 重启后仍可读取观察数据集。
- 重启后仍可读取 3 个 `SUCCEEDED` Evaluation Run。
- Docker 三服务保持停止，说明启动、查询和持久化不依赖 Docker。

## 5. SQLite 与 Artifact 一致性

| 检查项 | 结果 |
| --- | --- |
| `PRAGMA integrity_check` | `ok` |
| `PRAGMA foreign_key_check` | 0 个错误 |
| Journal mode | `wal` |
| Passive WAL checkpoint | `(busy=0, log=0, checkpointed=0)` |
| SQLite 大小 | 774,144 bytes |
| Artifact 文件 | 12 |
| Artifact 总大小 | 29,084 bytes |
| 发现的 Artifact 引用 | 12 |
| Missing Artifact | 0 |
| Orphan Artifact | 0 |

一致性扫描同时读取数据库 JSON 文档和 Dataset Manifest，因此
`raw_snapshot.jsonl` 等间接引用产物也被纳入校验。

## 6. 回归测试

- Unit + contract + health + 上传—评测—报告 E2E：294 项全部通过。
- 唯一 warning 仍为既有 Starlette `TestClient`/`httpx` 弃用提示。
- Redis 停止后上述回归和真实任务均通过，确认当前代码没有 Redis 运行依赖。

## 7. 切换后冷备份

API 停止后使用 SQLite Online Backup API 创建一致副本，并归档整个 Artifact 目录：

| 文件 | SHA-256 |
| --- | --- |
| `data/backup/stage4-20261104/qa-eval.sqlite3` | `ae34884c8f48585e67c48ffc62afb2d59ec61f796f7cee1361b0914808224513` |
| `data/backup/stage4-20261104/artifacts.tar` | `e47c4cc52ffd34e9920318ba7c09663df633428db5cbe019a552e4f17f817bb8` |

备份数据库 `integrity_check=ok`，revision 为 `c9f4a21d8e73`；备份目录约 892 KB。
该副本与源数据位于同一磁盘，只作为切换快照，不替代异盘灾备。

## 8. 回滚入口

如果观察后出现严重问题：

1. 停止本地 API，保留当前 SQLite 和 Artifact 故障现场。
2. 执行 `docker compose -f deploy/docker-compose.yml --env-file .env up -d postgres`。
3. 临时将 `QA_EVAL_DATABASE_URL` 改回旧 PostgreSQL 连接串。
4. 将 ArtifactStore 指向可用的旧存储；阶段 0 已确认旧 Artifact 不存在，因此旧数据只能用于元数据回查，不能恢复完整旧业务流程。
5. 若 Docker volume 不可用，可使用 `data/backup/stage0-20261104/postgres/qa-eval.dump` 恢复 PostgreSQL。

新本地链路的恢复优先使用 `data/backup/stage4-20261104/`，它包含可执行观察数据和完整 Artifact 闭包。

## 9. 阶段结论

阶段 4 完成标准已满足：正式配置已切换、Docker 服务已停止、连续 3 个真实任务成功、进程重启后数据仍在、无锁错误、无外键错误、无 Artifact 引用断裂，并已生成可读取的切换后备份。

后续清理 PostgreSQL/Redis/MinIO container、volume、镜像或 `deploy/docker-compose.yml` 应作为单独的最终清理操作，不在本阶段删除，以保留回滚能力。
