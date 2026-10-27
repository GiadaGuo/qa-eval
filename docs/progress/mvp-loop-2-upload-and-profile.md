# MVP 闭环 2 进度：真实文件上传与 Dataset Profile

**日期**：2026-10-27  
**目标**：浏览器上传 JSONL → 创建 immutable Dataset Version → 关闭 counts（Manifest / checksum / record count / SampleRecord / reject）→ 从全量数据生成 Dataset Profile。

## 1. 完成情况

### 1.1 后端： canonical 合约扩展

- 在 `src/qa_eval/contracts/datasets.py` 新增：
  - `DatasetProfileV1`
  - `NormalizationRunV1` / `NormalizationAdapterIdentity` / `NormalizationCounts` / `NormalizationError`
  - `ProfilerIdentity`
- 更新 `src/qa_eval/contracts/__init__.py` 导出。
- 更新 `scripts/contracts/generate_json_schemas.py` 的 `SCHEMAS` 映射，生成：
  - `schemas/qa-eval/normalization-run/1.0.schema.json`
  - `schemas/qa-eval/dataset-profile/1.0.schema.json`
- 运行 `uv run python scripts/contracts/generate_json_schemas.py --check` 通过，无 schema drift。

### 1.2 后端：Artifact Store 抽象与实现

- 新增 `src/qa_eval/domain/ports/artifact_store.py`：定义 `ArtifactStore` protocol 与 `StoredArtifact`。
- 新增实现：
  - `src/qa_eval/infrastructure/storage/local_file_system_artifact_store.py`
  - `src/qa_eval/infrastructure/storage/s3_artifact_store.py`（MinIO 兼容）
- `src/qa_eval/config.py` 增加 artifact store 配置项（local / s3）。
- `pyproject.toml` 增加 `boto3>=1.35,<2.0`。

### 1.3 后端：数据库 schema 扩展

- `src/qa_eval/infrastructure/db/models/dataset_version.py`：新增 `artifact_refs`（JSONB）和 `content_fingerprint`。
- 新增 `src/qa_eval/infrastructure/db/models/dataset_profile.py`。
- 新增手动迁移 `migrations/versions/3759c5e66bdb_add_artifact_refs_and_dataset_profiles.py`。
- 新增 Repository 接口与 SQLAlchemy 实现：
  - `dataset_version_repository.py` / `sqlalchemy_dataset_version.py`
  - `dataset_profile_repository.py` / `sqlalchemy_dataset_profile.py`

### 1.4 后端：标准化与 Profile 生成

- 新增 `src/qa_eval/application/normalization/jhsjk_article_normalizer.py`：
  - 读取 legacy JSONL snapshot
  - 输出 `SampleRecordV2` / `RejectedRecordV1`
  - 生成 `DatasetManifestV2` / `NormalizationRunV1` / `DatasetProfileV1`
  - counts 闭合：input = normalized + rejected
- 修复 `SampleRecordV2` 不接受 `created_at` 字段导致的全部 reject 问题。

### 1.5 后端：Dataset Version Service

- 新增 `src/qa_eval/application/services/dataset_version_service.py`：
  - `create_version`
  - `upload_raw_file`
  - `publish_version`
  - `get_manifest`
  - `get_profile`
- 新增 API 路由 `apps/api/routes/dataset_versions.py`：
  - `POST /api/v1/datasets/{dataset_id}/versions`
  - `GET /api/v1/datasets/{dataset_id}/versions`
  - `POST /api/v1/dataset-versions/{version_id}/files`
  - `POST /api/v1/dataset-versions/{version_id}/publish`
  - `GET /api/v1/dataset-versions/{version_id}`
  - `GET /api/v1/dataset-versions/{version_id}/manifest`
  - `GET /api/v1/dataset-versions/{version_id}/samples`
  - `GET /api/v1/dataset-versions/{version_id}/profile`
- `apps/api/main.py` 注册 router；`apps/api/dependencies.py` 注入 store / service。
- `pyproject.toml` 增加 `python-multipart>=0.0.20` 以支持文件上传。

### 1.6 后端：测试

- 新增 `tests/unit/test_artifact_store.py`：本地 ArtifactStore 路径遍历与 checksum 校验。
- 新增 `tests/unit/test_dataset_version_service.py`：使用 fake repositories 验证上传-发布-counts 闭合。
- 结果：
  - `uv run pytest tests/unit tests/integration -q` → **85 passed, 6 skipped**
  - `uv run python scripts/contracts/generate_json_schemas.py --check` → **通过**
  - `uv run ruff check <changed files>` → **通过**

### 1.7 前端：闭环 2 页面

- 新增 API client：`apps/web/src/api/datasetVersions.ts`
- 新增页面：
  - `DatasetListPage.tsx`：数据集列表 + 创建
  - `DatasetDetailPage.tsx`：版本列表 + JSONL 上传 + 发布
  - `DatasetVersionPage.tsx`：版本详情（概览 / Manifest / Sample 预览 / Profile）
- 更新 `apps/web/src/App.tsx`：顶部菜单切换「数据集管理」与「本地验证报告」。
- 安装 `react-router-dom`（实际使用状态导航，依赖已加入）。
- 结果：
  - `npm run lint` → **通过**
  - `npm run build` → **通过**（仅 chunk size warning）

### 1.8 端到端验证

- 启动 PostgreSQL：`docker compose --env-file .env -f deploy/docker-compose.yml up -d postgres`
- 迁移：`uv run alembic upgrade head`
- 启动后端：`uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload`
- 启动前端：`cd apps/web && npm run dev`
- 通过 curl 完成完整链路：
  1. 创建数据集
  2. 创建版本
  3. 上传 3 条 JSONL（含 1 条 text=null）
  4. 发布版本
  5. 获取 Manifest / Samples / Profile
- 验证结果：
  - `counts.input == 3`
  - `counts.normalized == 3`
  - `counts.rejected == 0`
  - `profile.summary.normalized_record_count == 3`
  - `profile.field_profiles[0].non_null_count == 2`, `null_count == 1`
- 前端代理检查：`curl -s http://localhost:5173/api/v1/datasets` 正常返回。

## 2. 关键修复记录

| 问题 | 原因 | 修复 |
|------|------|------|
| `test_full_upload_and_publish_closes_counts` 中 `normalized == 0` | `SampleRecordV2` 模型没有 `created_at` 字段，normalizer 传入导致全部 validation 失败进入 rejected | 从 normalizer 的 `SampleRecordV2.model_validate` 调用中移除 `created_at` |
| FastAPI 文件上传路由启动报错 | 缺少 `python-multipart` | 加入 `pyproject.toml` 并 `uv sync` |
| 前端 `react-hooks/set-state-in-effect` lint 错误 | `useEffect` 内同步调用 `setState` | 初始化 loading 为 `true`，仅在 Promise `.then()` / `.finally()` 回调中 `setState`；对 dataset/version 切换使用 `key` prop 强制 remount |
| 集成测试 `test_dataset_version_unique_constraint` 失败 | `qa_eval_test` 数据库未应用新增列迁移 | 使用 `QA_EVAL_TEST_DATABASE_URL` 运行 `alembic upgrade head` |

### 2026-10-27 复查修复

用户浏览器验证发现：选择文件后点击“上传”没有产生 `POST /files` 请求，随后发布返回 `No raw file uploaded for this version`，DRAFT 详情页同时产生 Manifest、Samples、Profile 三个 404。

根因与修复：

- Ant Design `beforeUpload` 提供的是 `RcFile` 本身，原页面却继续读取不存在的 `originFileObj`，导致上传函数在浏览器端提前返回。现直接保存并上传该 `File`。
- 页面现在显示已选择文件名，并在成功消息中展示服务端确认的文件名与大小。
- API client 解析 FastAPI `detail`，不再只显示笼统的 HTTP 422。
- DRAFT 版本详情不再请求只有发布后才存在的 Manifest、Samples 和 Profile，而是展示发布提示。
- 后端在上传阶段验证 UTF-8、逐行 JSON object 及 `sample_id`/`text` 输入契约，无法识别的文件立即返回带行号的 422，不再先显示上传成功、到发布时才失败。
- 增加 multipart API 回归测试和无法识别 JSONL 的 service 回归测试。

## 3. 运行方式

```bash
# 1. 启动 PostgreSQL
docker compose --env-file .env -f deploy/docker-compose.yml up -d postgres

# 2. 应用迁移
uv run alembic upgrade head

# 3. 启动后端
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload

# 4. 启动前端
cd apps/web && npm run dev
```

前端地址：http://localhost:5173/  
后端地址：http://127.0.0.1:8000/

## 4. 下一步（闭环 3）

根据 `docs/development/mvp-development-plan.md`，闭环 3 进入 **Metric / Calculator Registry**：拆分并注册现有真实 `02080103` 的 Metric Definition、Calculator Descriptor 和 Binding，提供只读 API 与指标管理页面。Quality Contract 和 Evaluation Run 分别留在后续闭环。
