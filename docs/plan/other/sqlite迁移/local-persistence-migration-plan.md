# QA Eval 本地持久化迁移计划

> 状态：待实施  
> 编写日期：2026-11-04  
> 目标：本地开发和单机使用不再依赖 Docker、PostgreSQL、Redis、MinIO；采用 SQLite + 本地文件系统完成持久化。

## 1. 背景与结论

当前 `deploy/docker-compose.yml` 实际编排了 PostgreSQL、Redis 和 MinIO。MySQL **不在该 Compose 中**，它是 QA Eval 通过只读 Reader 访问的外部业务数据源，不能和平台自身的持久化数据库混为一谈。

代码现状如下：

- PostgreSQL 保存平台元数据、任务状态、契约、计划、评测结果和报告索引等结构化数据。
- ArtifactStore 已支持 `local` 和 `s3` 两种实现，并且默认就是 `local`，根目录为 `data/artifacts`。
- MinIO 只服务于可选的 S3 ArtifactStore，本地模式可以直接不启动 MinIO。
- 未发现业务代码对 Redis 的调用；当前后台任务由进程内 `ThreadPoolExecutor` 执行，Redis 目前不是运行依赖。
- MySQL Reader 用于探查和读取外部数据源；只有使用 MySQL 数据源相关功能时才需要配置 MySQL。
- ORM 模型和历史 Alembic migration 大量使用 PostgreSQL 专属的 `JSONB`、`postgresql.UUID`，不能只把连接串改成 `sqlite:///...`。

推荐的本地持久化目标是：

| 数据类别 | 目标存储 | 建议位置 |
| --- | --- | --- |
| 平台关系数据 | SQLite（WAL） | `data/runtime/qa-eval.sqlite3` |
| 上传原文件、标准化数据、证据和报告 | 本地 ArtifactStore | `data/artifacts/` |
| 临时写入 | ArtifactStore staging | `data/artifacts/.staging/` |
| 外部 MySQL | 可选、只读数据源 | 不纳入本地持久化目录 |
| Redis、MinIO、PostgreSQL | 退出本地最小运行链路 | 不再要求启动 |

该方案参考 `dataana-v5` 的本地化思路：运行数据集中在一个目录、SQLite 开启 WAL、启用外键、事务短且不跨异步等待、停止服务后可整目录备份。实现层面不照搬其裸 `sqlite3` SQL，而是保留本项目现有的 SQLAlchemy、Repository、依赖注入和 Alembic。

## 2. 目标与非目标

### 2.1 目标

1. 新环境仅安装 Python/Node 依赖即可运行，不要求 Docker Desktop 或本机数据库服务。
2. 默认配置使用 SQLite 和本地 ArtifactStore。
3. 保留现有业务实体、API 契约、UUID 值和 JSON 文档结构。
4. 可将当前 PostgreSQL 数据一次性迁移到 SQLite，并校验记录数、主外键和 Artifact 引用。
5. 支持简单、可验证的备份与恢复：停止服务后复制 `data/runtime/` 和 `data/artifacts/`。
6. 单机、单 API 进程下，现有评测和说明文档后台任务可稳定运行。

### 2.2 非目标

- 不把外部 MySQL 业务库迁入 SQLite。
- 本阶段不支持多主机共享同一个 SQLite 文件。
- 本阶段不支持多个 API 进程并发写库，也不承诺高并发任务吞吐。
- 不在第一阶段删除 S3/PostgreSQL 适配代码；先将其降级为非默认、可选能力，稳定后再决定是否清理。
- 不将大体积样本、Parquet、原始文档或报告正文二进制直接塞进 SQLite；继续由 ArtifactStore 管理。

## 3. 目标目录与配置

建议统一到已有且已被 `.gitignore` 忽略的 `data/`：

```text
data/
├── runtime/
│   └── qa-eval.sqlite3
├── artifacts/
│   ├── .staging/
│   └── datasets/...
└── backup/
    └── 2026-11-04T120000+0800/
        ├── qa-eval.sqlite3
        ├── artifacts/...
        └── manifest.json
```

默认配置建议改为：

```dotenv
QA_EVAL_DATABASE_URL=sqlite:///data/runtime/qa-eval.sqlite3
QA_EVAL_TEST_DATABASE_URL=sqlite:///data/runtime/qa-eval-test.sqlite3
QA_EVAL_ARTIFACT_STORE_TYPE=local
QA_EVAL_ARTIFACT_STORE_LOCAL_ROOT=data/artifacts
```

路径应以项目根目录解析，而不是依赖启动命令的当前目录。测试库优先使用 pytest 临时目录，避免测试运行污染 `data/runtime`。

## 4. 技术设计

### 4.1 数据库连接

在数据库 Engine 工厂中按 URL 方言配置 SQLite：

- 创建数据库文件前确保父目录存在。
- 设置 `connect_args={"check_same_thread": false, "timeout": 30}`，允许当前线程池任务使用各自 Session，并给短暂锁竞争留出等待时间。
- 每个 SQLite 连接启用 `PRAGMA foreign_keys=ON`。
- 初始化时启用 `PRAGMA journal_mode=WAL`。
- 建议设置 `PRAGMA busy_timeout=30000`；可选设置 `PRAGMA synchronous=NORMAL`，但需在测试故障恢复后再确定。
- 保持“一个工作单元一个 Session、短事务、异常回滚”，禁止跨网络请求或 LLM `await` 长时间持有写事务。
- PostgreSQL 连接继续沿用当前 pool 配置；SQLite 不套用无意义的服务端连接池参数。

不要在 import `session.py` 时永久固化无法替换的全局配置。Engine/Session 工厂应至少允许测试传入 URL，避免依赖清缓存和模块重载。

### 4.2 ORM 类型兼容

将模型中的 PostgreSQL 专属类型替换为 SQLAlchemy 通用类型：

- `postgresql.UUID(as_uuid=True)` → `sqlalchemy.Uuid(as_uuid=True)`。
- `JSONB` → `sqlalchemy.JSON`；如果仍需保留 PostgreSQL 的 JSONB 能力，可使用 `JSON().with_variant(JSONB(), "postgresql")`。
- 保持 Python 层 UUID 和 dict/list 的类型不变，避免 API 和领域契约变化。

在替换前逐表确认是否存在 JSONB 运算符查询或 JSON 字段索引。当前若只是整份 JSON 文档读写，SQLite JSON 文本表示即可；依赖 `@>`、`->>` 等 PostgreSQL 运算符的查询必须改为方言无关查询或显式分支。

### 4.3 Alembic 策略

现有 migration 含 `postgresql.JSONB`，且有 SQLite 不直接支持的 `ALTER COLUMN`，不能原样用于全新 SQLite 库。

本地化阶段建议采用“可移植 migration 链”而不是运行时 `Base.metadata.create_all()`：

1. 把历史 migration 中的 UUID/JSON 声明改为可移植类型。
2. 在 `migrations/env.py` 对 SQLite 开启 `render_as_batch=True`，用 batch table rebuild 处理列变更。
3. 将 PostgreSQL 专属原生 SQL拆成方言分支，或者改成 SQLAlchemy 数据更新。
4. 用空 SQLite 文件从 revision 0 执行到 `head`，作为 CI 必测路径。
5. PostgreSQL migration 回归仍至少跑一次，防止为了 SQLite 破坏旧库升级能力。

如果历史链改造成本过高，可建立明确版本化的 SQLite baseline migration，但必须记录 baseline 对应的 PostgreSQL revision，并测试后续 revision 能同时升级两种库。不要以无版本的 `create_all()` 代替数据库版本管理。

### 4.4 并发和任务约束

SQLite 支持多读单写。当前两个进程内线程池会产生并发写入，因此本地模式需要明确约束：

- API 使用单进程启动，不使用多个 Uvicorn/Gunicorn worker。
- 每个后台 worker 创建独立 Session，不在线程之间共享 Session。
- 保留当前任务启动清扫，将重启前的孤儿 `RUNNING` 状态置为失败或中断。
- 第一版将评测写任务总并发限制为 1；说明文档评测即使保留计算并发，也应将最终数据库写入缩短并串行化。
- 对 `database is locked` 只允许有限、带退避的事务级重试；不能盲目重放包含 Artifact 写入或外部调用的整个业务流程。
- 增加并发集成测试，覆盖“创建任务—状态更新—结果落库—查询”以及两个任务竞争写入。

若后续需要多进程、多用户高并发或远程共享数据库，应将其视为切回 PostgreSQL 的触发条件，而不是继续堆叠 SQLite 锁规避逻辑。

### 4.5 Artifact 一致性

继续使用现有 `LocalFileSystemArtifactStore`，但实施时需补齐以下规则：

- 文件先写 `.staging`，校验 SHA-256 后再原子移动到最终路径。
- 数据库只保存相对于 Artifact 根目录的 URI，禁止写入机器相关的绝对路径。
- 创建流程采用“文件提交成功后写元数据”；数据库失败时执行补偿删除或记录可清理的孤儿文件。
- 删除流程先完成数据库约束检查，再删除文件；失败时必须可重试并留下日志。
- 启动或维护命令可扫描数据库引用与磁盘文件，报告 missing/orphan artifact，不默认自动删除。

SQLite 事务不能与文件系统组成真正的原子事务，因此校验和、幂等路径和补偿流程是必要条件。

## 5. 分阶段实施

### 阶段 0：建立基线和冻结窗口（已完成，2026-11-04）

执行记录见 [`local-persistence-stage0-baseline-2026-11-04.md`](local-persistence-stage0-baseline-2026-11-04.md)。旧业务数据经确认不进入后续迁移范围；本地 Artifact 已清理，PostgreSQL 逻辑备份仅作为基线和短期回查材料保留。

- 记录当前 Alembic revision、PostgreSQL 表清单及每表行数。
- 对 PostgreSQL 执行一次逻辑备份，对 `data/artifacts` 或 MinIO bucket 执行完整备份。
- 跑完现有 unit、contract、integration 测试，保存基线结果。
- 确认迁移期间暂停写入；旧 PostgreSQL 和对象存储保持只读可回滚状态。

**完成标准**：备份可读取，基线 revision、行数、失败测试均有记录。

### 阶段 1：让数据库层兼容 SQLite（已完成，2026-11-04）

执行记录见 [`local-persistence-stage1-result-2026-11-04.md`](local-persistence-stage1-result-2026-11-04.md)。空 SQLite 已从 revision 0 升级到 head，schema drift 检查、UUID/JSON 往返、PRAGMA、外键和 PostgreSQL 回归均已验证。

- 引入方言感知的 Engine/Session 工厂和 SQLite PRAGMA。
- 将 ORM 的 JSONB/UUID 改成跨方言类型。
- 改造 Alembic migration，使空 SQLite 可升级到 `head`。
- 将数据库测试改为可在临时 SQLite 上运行；保留少量 PostgreSQL 兼容回归。

**完成标准**：空 SQLite `alembic upgrade head` 成功，所有表、索引、外键和唯一约束与模型预期一致。

### 阶段 2：本地运行链路（已完成，2026-11-04）

执行记录见 [`local-persistence-stage2-result-2026-11-04.md`](local-persistence-stage2-result-2026-11-04.md)。默认配置、启动脚本、健康检查和测试 fixture 均已切换到 SQLite/local ArtifactStore；真实上传—评测—报告链路及进程重启后的持久性已经验证。

- 把默认数据库 URL 改成 `sqlite:///data/runtime/qa-eval.sqlite3`。
- 固定本地 ArtifactStore 为默认值，确保路径从项目根目录解析。
- 增加本地启动脚本：创建虚拟环境/安装提示、初始化目录、执行 migration、启动单 worker API；前端仍按当前方式启动。
- 精简 `.env.example`：本地最小配置不再要求 PostgreSQL、Redis、MinIO；将 S3 和外部 MySQL 放入可选配置段。
- 更新 README 和健康检查，健康检查应报告数据库方言、DB 可写、Artifact 根目录可写，但不得泄露凭据或绝对敏感路径。

**完成标准**：在未启动 Docker 的新环境中，可注册数据集、上传版本、运行至少一个评测、生成并读取报告，重启后数据仍存在。

### 阶段 3：旧数据一次性迁移（已完成，2026-11-04）

执行记录见 [`local-persistence-stage3-result-2026-11-04.md`](local-persistence-stage3-result-2026-11-04.md)。按业务完整性筛选后，仅 Registry 三表需要进入正式本地库，并以当前 canonical config 重建；旧 Dataset/Version、契约、运行、报告、文档和 Agent 历史因 27 个 Artifact 全部缺失而明确排除。

编写独立迁移命令，使用 SQLAlchemy 分别连接源 PostgreSQL 与目标 SQLite，按外键拓扑逐表复制。不要用 `pg_dump` 直接喂给 SQLite。

迁移流程：

1. 校验目标 SQLite 为空且 schema revision 为 `head`。
2. 暂停源系统写入，记录源库 revision 和每表行数。
3. 按父表到子表顺序批量读取并插入，保留原 UUID、时间、状态和 JSON 内容。
4. 对 JSON 做结构化序列化，不通过字符串拼 SQL。
5. 迁移 Artifact：若已是本地存储则保持相对 URI；若来自 MinIO，下载到 `data/artifacts`，校验对象大小和已记录的 SHA-256，并将 S3 URI 映射为本地相对 URI。
6. 校验每表行数、非空约束、外键完整性、唯一约束和 Artifact 引用。
7. 抽样比较关键业务对象，并跑 API smoke/E2E。
8. 生成机器可读迁移报告，包含源 revision、目标 revision、行数、URI 映射、校验失败和时间。

迁移脚本必须支持 `--dry-run`，正式运行时对目标库采用全量事务或可安全重建的批次；失败后不在半成品库上继续提供服务。

**完成标准**：行数和关键对象一致，所有 Artifact 可读，核心 E2E 通过，迁移报告无未解释差异。

### 阶段 4：切换与观察（已完成，2026-11-04）

执行记录见 [`local-persistence-stage4-result-2026-11-04.md`](local-persistence-stage4-result-2026-11-04.md)。本机 `.env` 已正式切换，PostgreSQL/Redis/MinIO 容器已停止但未删除；连续 3 个真实任务、API 重启、SQLite/Artifact 一致性和切换后备份均已验证。

- 切换 `.env` 到 SQLite + local ArtifactStore。
- 首次启动只运行一个 API worker。
- 观察数据库锁错误、任务状态、磁盘增长和 Artifact missing/orphan 情况。
- PostgreSQL/MinIO 保留只读至少一个稳定观察周期；确认无回滚需求后再单独安排停机和清理。
- Redis 因无代码依赖可先停止，但仍通过完整回归验证这一判断。

**完成标准**：连续完成约定数量的真实任务且无数据丢失、不可恢复锁错误或引用断裂。

## 6. 测试矩阵

| 层级 | 必测内容 |
| --- | --- |
| Migration | 空 SQLite 从 0 到 head；已有 SQLite 从前一 revision 到 head；PostgreSQL 原链回归 |
| Repository | UUID、JSON、时区时间、唯一约束、级联/限制删除、事务回滚 |
| ArtifactStore | staging、校验和、路径穿越、覆盖行为、补偿删除、missing/orphan 扫描 |
| API | 数据集、版本、契约、计划、评测、质量结果、文档报告、Agent Session |
| 并发 | 两个后台任务争用、锁超时、失败重试、服务重启后的 RUNNING 清扫 |
| 数据迁移 | 行数、外键、UUID、JSON 深比较、Artifact URI 和 SHA-256 |
| E2E | 无 Docker 冷启动；上传—评测—报告；重启—查询；备份—恢复—查询 |

建议把 SQLite 设为默认 CI 集成测试路径；PostgreSQL 兼容测试可降频，但在正式删除 PostgreSQL 支持前不能完全取消。

## 7. 备份与恢复

### 7.1 备份

最稳妥的 v1 流程是停止 API 后冷备份：

1. 确认没有 `PENDING/RUNNING` 任务。
2. 停止 API，避免数据库和 Artifact 在复制过程中变化。
3. 执行 SQLite checkpoint，或使用 SQLite Online Backup API 生成一致数据库副本。
4. 复制数据库副本和整个 `data/artifacts/`。
5. 生成 `manifest.json`，记录应用版本、Alembic revision、文件数量、总大小和 SHA-256。
6. 将备份放到 `data/` 之外的安全位置；同盘 `data/backup` 只能作为临时副本，不能防磁盘损坏。

不能在服务运行时只复制主 `.sqlite3` 而忽略 `-wal`/`-shm` 文件，否则备份可能缺少已提交数据。

### 7.2 恢复

1. 停止 API，并把当前 `data/` 重命名留档。
2. 恢复 SQLite 与 Artifact 目录。
3. 校验备份 manifest 和 SHA-256。
4. 执行 `PRAGMA integrity_check`、`PRAGMA foreign_key_check`。
5. 运行 `alembic current`，必要时升级到当前 head。
6. 启动 API，运行只读 smoke test 后再允许写入。

## 8. 回滚方案

切换前保留 PostgreSQL/MinIO 的只读快照和旧 `.env`。出现以下任一情况立即回滚：

- SQLite `integrity_check` 或 `foreign_key_check` 失败。
- 迁移后存在无法解释的表行数差异或 Artifact 缺失。
- 正常单机负载持续出现 `database is locked`，有限重试仍不能恢复。
- 核心 E2E、删除约束或任务恢复语义与旧系统不一致。

回滚步骤：停止新 API，保存 SQLite 故障现场，恢复旧 `.env`，重新启用 PostgreSQL/原 ArtifactStore，验证源库仍为切换时的只读一致状态后启动旧版本。观察期内禁止让新旧系统同时接受写入，否则需要额外的反向增量迁移，本计划不覆盖双写合并。

## 9. 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| JSONB/UUID 方言耦合 | migration 或查询在 SQLite 失败 | 通用类型、方言回归、从 0 migration 测试 |
| SQLite 单写锁 | 后台任务状态/结果写入失败 | 单进程、短事务、busy timeout、有限重试、降低写并发 |
| DB 与文件非原子 | 元数据与 Artifact 不一致 | staging、checksum、幂等路径、补偿和一致性扫描 |
| WAL 备份不完整 | 恢复时丢数据 | 停机冷备或 Online Backup API，不裸拷运行中的主文件 |
| 时区语义变化 | 时间排序/序列化不一致 | 统一 UTC 写入，增加 aware datetime 往返测试 |
| SQLite 约束差异 | 删除/级联行为改变 | 每连接开启 foreign_keys，覆盖 RESTRICT/CASCADE 测试 |
| 误删外部 MySQL 能力 | 真实数据源无法读取 | 明确其为可选 Reader，不纳入容器清理范围 |
| 未来规模增长 | SQLite 不再合适 | 保留 Repository/SQLAlchemy 边界和 PostgreSQL 适配能力 |

## 10. 最终清理（观察期之后）

满足验收标准并完成一次备份恢复演练后，再执行以下清理：

- 将 Docker 启动说明移到历史/可选部署文档，主 README 只描述本地启动。
- 将 `deploy/docker-compose.yml` 标记为 deprecated；是否删除应单独决策，不与数据切换同时进行。
- 删除 `.env.example` 中本地必需项里的 PostgreSQL、Redis、MinIO 配置，将 S3 放入高级可选段。
- 若确认不再保留远程对象存储，再单独移除 boto3/S3 实现和依赖；第一阶段不做。
- 若确认不再维护 PostgreSQL 兼容，再另立 ADR 和清理任务；不要在迁移成功前破坏回滚路径。

## 11. 验收清单

- [ ] 不启动任何 Docker 容器即可启动 API 和 Web。
- [ ] 新库可通过 Alembic 从 0 升级到 head，而非依赖 `create_all()`。
- [ ] SQLite 已开启 WAL、foreign keys 和 busy timeout。
- [ ] API 明确以单 worker 运行，后台线程不共享 Session。
- [ ] 本地 Artifact 写入、读取、删除和校验和测试通过。
- [ ] PostgreSQL 数据和 MinIO Artifact（如有）完成迁移及校验。
- [ ] 外部 MySQL 未配置时仅相关接口不可用，不影响平台核心功能。
- [ ] Redis、MinIO、PostgreSQL 全部停止后核心 E2E 通过。
- [ ] 冷备份、完整性校验和恢复演练通过。
- [ ] 回滚所需旧数据、旧配置和操作记录在观察期内保持可用。

## 12. 建议执行顺序

优先完成“SQLite 方言兼容 + 空库 migration + 临时库测试”，随后完成“无 Docker 的全新本地运行”，最后再迁移旧数据。不要先关闭或删除 PostgreSQL volume，也不要以直接修改连接串作为试运行方式；当前 PostgreSQL 专属类型和 migration 会使这种切换产生不完整或不可预测的结果。
