# 本地持久化迁移阶段 2 实施记录

> 执行日期：2026-11-04（Asia/Shanghai）  
> 对应计划：[`local-persistence-migration-plan.md`](local-persistence-migration-plan.md)  
> 结论：阶段 2 已完成，默认本地运行链路不再依赖 Docker 服务。

## 1. 默认本地配置

- 默认数据库：`sqlite:///data/runtime/qa-eval.sqlite3`。
- 默认 ArtifactStore：`local`，根目录 `data/artifacts`。
- `.env.example` 已移除 PostgreSQL、Redis、MinIO 必填配置。
- 外部 MySQL、S3 兼容存储、LLM、KenLM 和旧 PostgreSQL 连接均放入可选配置段。
- 本机已有 `.env` 未被覆盖；专用本地启动脚本会显式覆盖其中遗留的 PostgreSQL/S3 选择。

## 2. 本地启动脚本

新增 `scripts/start-local.sh`：

1. 固定切换到 SQLite + local ArtifactStore。
2. 创建 `data/runtime` 和 `data/artifacts/.staging`。
3. 默认执行 `uv sync --all-groups`，由 uv 创建/更新 `.venv`。
4. 自动执行 `alembic upgrade head`。
5. 以单 worker 启动 Uvicorn。

常用模式：

```bash
./scripts/start-local.sh
QA_EVAL_INIT_ONLY=1 ./scripts/start-local.sh
QA_EVAL_RELOAD=1 ./scripts/start-local.sh
QA_EVAL_SKIP_SYNC=1 QA_EVAL_INIT_ONLY=1 ./scripts/start-local.sh
```

`QA_EVAL_SKIP_SYNC=1` 只允许复用已有 `.venv`，不会访问 uv cache 或网络。

## 3. 路径与健康检查

- SQLite 相对路径和本地 Artifact 根目录都固定相对于项目根目录解析。
- `GET /api/v1/health` 现在报告：
  - 数据库方言与可写状态；
  - ArtifactStore 类型、脱敏根目录标签和可写状态。
- 健康响应不包含数据库连接串、凭据或项目外绝对路径。
- 项目外 Artifact 根目录只显示为 `<external>`。

实测响应：

```json
{
  "status": "ok",
  "service": "qa-eval-api",
  "database": {"dialect": "sqlite", "writable": true},
  "artifact_store": {
    "type": "local",
    "root": "data/artifacts",
    "writable": true
  }
}
```

## 4. 本地测试数据库

Integration fixture 现在支持两种后端：

- SQLite：每个测试使用 pytest 临时目录中的独立数据库，并由 ORM metadata 初始化。
- PostgreSQL：仍强制数据库名为 `qa_eval_test`，防止误写开发库。

因此 `.env.example` 的 SQLite 测试配置不要求启动 PostgreSQL，也不会污染正式
`data/runtime/qa-eval.sqlite3`。

## 5. 无 Docker 核心链路验证

正式本地 SQLite 已从空库升级至 `c9f4a21d8e73`，随后完成：

1. 注册文本数据集；
2. 创建 Dataset Version；
3. 上传 7 条 JSONL fixture；
4. 保存字段映射并发布版本；
5. 生成 normalized、manifest、profile 等本地 Artifact；
6. 同步指标 Registry；
7. 发布重复度质量契约；
8. 执行 Evaluation Run；
9. 生成并读取 Quality Report；
10. 关闭进程后以新的单 worker API 进程启动，再次读取数据集和报告。

验证结果：

| 项目 | 结果 |
| --- | --- |
| Alembic revision | `c9f4a21d8e73 (head)` |
| SQLite journal mode | `wal` |
| SQLite integrity check | `ok` |
| 数据集 | 1 |
| 成功评测运行 | 1 |
| 质量报告 | 1 |
| Artifact 文件 | 8 |
| API 重启后数据集读取 | 200 |
| API 重启后报告读取 | 200 |

本次 smoke 数据保留在 `data/runtime/qa-eval.sqlite3` 和 `data/artifacts/`，可直接作为本地界面验证数据；两个目录均被 Git 忽略。

## 6. 自动化验证

- Unit + contract + health：293 项全部通过。
- SQLite repository、约束和上传—评测—报告集成：21 项全部通过。
- 本阶段涉及文件 Ruff：通过。
- Shell 语法检查：通过。
- `git diff --check`：通过。

## 7. 阶段边界

- `deploy/docker-compose.yml` 尚未删除，只作为历史/可选配置保留。
- 当前本机 `.env` 尚未改写；使用 `scripts/start-local.sh` 时会可靠切换到本地模式。
- Docker 容器没有在本阶段被停止或删除，避免影响其他仍在运行的本机会话；本地启动、migration、业务执行和持久化链路均不读取这些服务。
- 旧 PostgreSQL/MinIO 数据已在阶段 0 被明确排除出迁移范围，因此阶段 3 可改为“无需执行旧数据导入”的决策记录，或仅保留通用迁移工具任务。
