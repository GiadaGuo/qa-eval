# MVP 闭环 1 进度：API/UI 读取现有真实产物

- 日期：2026-10-27
- 状态：已完成
- 对应计划：`docs/development/mvp-development-plan.md` 第 11 章“最短可交付路径 · 闭环 1”

## 目标

在不写入平台数据库、不登记为正式 EvaluationRun 的前提下，让后端 API 读取已有 LOCAL_VALIDATION 真实产物，React 页面展示当前真实质量报告与问题下钻，并明确标识 `LOCAL_VALIDATION`。

## 完成情况

### 后端

- 新增 `src/qa_eval/application/services/local_validation_service.py`
  - 只读加载 `data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/canonical-v1/` 目录下的真实产物。
  - 使用现有 canonical Pydantic contracts 校验全部对象：
    - `DatasetManifestV2`
    - `MetricResultV1`
    - `QualityContractVersionV1`
    - `RuleEvaluationV1`
    - `QualityIssueV1`
    - `IssueIndexV1`
    - `QualityReportV1`
- 新增 `apps/api/routes/local_validation.py`
  - 提供 `/api/v1/local-validation/*` 系列只读端点。
  - 所有返回均明确携带 `authority: LOCAL_VALIDATION` 与 `not_a_platform_run: true`。
- 修改 `apps/api/main.py`：注册 `local_validation_router`。

### 前端

- 新增 `apps/web/src/api/localValidation.ts`
  - 定义 LOCAL_VALIDATION 相关 TypeScript 类型与 API client。
- 新增 `apps/web/src/pages/LocalValidationReportPage.tsx`
  - 展示 LOCAL_VALIDATION 警告、数据集版本、报告概览、metric observations、rule evaluations。
  - 提供 confirmed/candidate issue 汇总与分页下钻。
- 修改 `apps/web/src/App.tsx`
  - 入口切换为 `LocalValidationReportPage`。
  - 保留 `MysqlDatasourcePage.tsx` 文件，供后续闭环按需恢复路由。

### 测试与文档

- 新增 `tests/unit/test_local_validation_service.py`
- 新增 `tests/integration/test_local_validation_api.py`
- 更新 `docs/development/canonical-models-progress.md`

## 验证结果

```bash
# 后端 service + API 测试
uv run pytest tests/unit/test_local_validation_service.py tests/integration/test_local_validation_api.py -v
# 6 passed

# 完整相关测试（unit + contract + 选定 integration）
uv run pytest tests/unit tests/contracts tests/integration/test_health_api.py tests/integration/test_local_validation_api.py -q
# 111 passed

# schema 漂移检查
uv run python scripts/contracts/generate_json_schemas.py --check
# 通过

# Python lint
uv run ruff check src/qa_eval/application/services/local_validation_service.py \
  apps/api/routes/local_validation.py apps/api/main.py \
  tests/unit/test_local_validation_service.py tests/integration/test_local_validation_api.py
# All checks passed

# 前端 lint + build
cd apps/web && npm run lint && npm run build
# 通过
```

手动启动前后端后：

- `http://127.0.0.1:8000/api/v1/local-validation` 返回 `authority: LOCAL_VALIDATION`、`gate_status: PASS`。
- `http://127.0.0.1:5173/` 正常加载报告页面，API proxy 工作正常。

## 关键数据

| 项目 | 数值 |
| --- | ---: |
| 数据集 | `policy.jhsjk_article` |
| 输入记录 | 14,960 |
| 有效文本 | 14,928 |
| 排除记录 | 32 |
| exact redundant confirmed | 45 |
| near-duplicate candidates | 7,517 |
| 规则 | `exact_redundant_record_ratio <= 0.01` |
| Gate | PASS |
| Execution | SUCCEEDED |

## 未解决风险

1. 产物路径在 `LocalValidationService` 中硬编码，仅供闭环 1 只读展示；后续应通过配置或数据库记录 artifact 引用。
2. 前端尚未引入真实路由库；当前为单页面替换，闭环 7 需要恢复六模块路由。
3. 本页数据不来自平台持久化 Run，切换为正式 EvaluationRun 后需重写数据获取逻辑。

## 下一步

进入 **闭环 2：真实文件上传与 Dataset Profile**。

验收目标：

- 浏览器上传 JSONL 并创建不可变 Dataset Version。
- Manifest、checksum、record count、SampleRecord 和 reject 数量闭合。
- Profile 来自完整数据，不以 preview 代替。

预计涉及文件与模块：

- ArtifactStore port 与 MinIO/S3 adapter。
- JSONL 上传、checksum 和 artifact 提交。
- DatasetVersion / Manifest 发布服务。
- 标准化为 SampleRecord 2.0，生成 reject 和 DatasetProfile。
- 前端 `/datasets` 模块页面。
