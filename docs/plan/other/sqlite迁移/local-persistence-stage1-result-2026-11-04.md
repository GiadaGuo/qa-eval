# 本地持久化迁移阶段 1 实施记录

> 执行日期：2026-11-04（Asia/Shanghai）  
> 对应计划：[`local-persistence-migration-plan.md`](local-persistence-migration-plan.md)  
> 结论：阶段 1 已完成，数据库层可同时运行于 SQLite 和 PostgreSQL。

## 1. 已完成改造

### Engine 与 Session

- 新增可注入 URL 的 `create_database_engine()` 和 `create_session_factory()`。
- 相对 SQLite 文件路径固定相对于项目根目录解析，不依赖启动命令的工作目录。
- SQLite 文件的父目录会在连接前自动创建。
- SQLite 连接统一启用：
  - `PRAGMA foreign_keys=ON`
  - `PRAGMA journal_mode=WAL`
  - `PRAGMA busy_timeout=30000`
  - `check_same_thread=False`
- PostgreSQL 继续使用 `pool_pre_ping=True`。

### ORM 类型

- 模型不再直接依赖 `postgresql.UUID` 和 `JSONB`。
- UUID 使用 SQLAlchemy 通用 `Uuid(as_uuid=True)`，SQLite 保存为可往返的 UUID 字符表示，PostgreSQL 继续使用原生 UUID。
- JSON 使用方言 variant：SQLite 为 JSON，PostgreSQL 保持 JSONB。
- Python 层 UUID、dict 和 list 类型及 API 契约未改变。

### Alembic

- SQLite migration 使用 batch mode。
- 所有历史 migration 的 `sa.UUID()` 已改为可移植的 `sa.Uuid()`。
- PostgreSQL JSONB 在 SQLite migration 中编译为 JSON。
- PostgreSQL `now()` 默认值改为可移植的 `sa.func.now()`。
- 需要新增/删除约束或修改列的 migration 已改为 batch table 操作。
- 补齐历史唯一约束名称，使 SQLite batch migration 可以稳定定位约束。
- 空 SQLite 已从 revision 0 完整升级到 `c9f4a21d8e73`。

## 2. 验证结果

### SQLite

- 空库 `alembic upgrade head`：通过。
- `alembic current`：`c9f4a21d8e73 (head)`。
- `alembic check`：`No new upgrade operations detected`。
- 表数量：27。
- `PRAGMA integrity_check`：`ok`。
- `PRAGMA foreign_key_check`：无错误。
- migration 后 journal mode：`wal`。
- UUID + JSON ORM 写入、提交、重新读取：通过。
- 外键违规写入会抛出 `IntegrityError`：通过。

### PostgreSQL

- 现有库 `alembic upgrade head`：通过，无待执行 revision。
- `alembic current`：`c9f4a21d8e73 (head)`。
- Repository/数据库约束相关 integration：22 项全部通过。
- PostgreSQL `alembic check` 仍报告一项阶段 0 已存在的 schema drift：数据库中的 `quality_reports.run_id` 是“唯一约束 + 非唯一索引”，当前 ORM 表达为唯一索引。该差异不影响 SQLite 阶段 1 验收，本阶段未擅自修改现有 PostgreSQL 业务表；后续若继续长期维护 PostgreSQL，应新增独立 migration 收敛。

### 自动化测试

- Unit + contract：291 项全部通过。
- SQLite/Agent 针对性回归：15 项通过，1 项已知长耗时用例主动排除。
- PostgreSQL 数据库集成回归：22 项全部通过。
- Ruff：数据库、migration 和新增测试范围全部通过。
- `git diff --check`：通过。

## 3. 新增回归覆盖

`tests/unit/test_database_compatibility.py` 覆盖：

1. 相对 SQLite URL 相对于指定项目根目录解析。
2. WAL、busy timeout、foreign keys 和父目录创建。
3. 外键约束实际生效。
4. 空 SQLite 从 0 migration 到 head。
5. schema 完整性、外键完整性、表数量和 Alembic revision。
6. migration 后通过 ORM 往返读写 UUID 和 JSON。

## 4. 阶段边界

- 默认配置仍指向 PostgreSQL；切换默认 SQLite、`.env.example` 和本地启动链路属于阶段 2。
- 本阶段没有创建正式的 `data/runtime/qa-eval.sqlite3`，测试全部使用临时 SQLite 文件。
- 本阶段没有停止或删除 PostgreSQL、Redis、MinIO 容器。
- 本阶段没有修改现有 PostgreSQL 数据内容或修复阶段 0 记录的历史任务状态。
