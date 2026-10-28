# 说明文档质量评测报告生成 — 开发进度

- 关联计划：[document-quality-report-generation-plan.md](../development/document-quality-report-generation-plan.md)
- 进度约定：每阶段完成后更新本文件；上一阶段验收通过才进入下一阶段

---

## P0：ADR 修订与 rubric 冻结

- 状态：已完成（2026-10-28）
- 范围：ADR-0003（对 ADR-0001 窄幅修订）、评测标准冻结文件
- 计划验收标准：ADR 评审通过；冻结文件 sha256 稳定

### 交付物

| 文件 | 类型 | 说明 |
|---|---|---|
| `docs/adr/0003-document-evaluation-builtin-pipeline-and-report-kind.md` | 新增 | 内置专用流水线不经 Contract/Plan；MetricResultV1 兼容语义；独立 report kind + 迁移承诺；rubric canonical 副本迁至平台 |
| `docs/adr/0001-skill-metric-ownership-and-shared-evaluation-layers.md` | 窄幅修订 | 仅在元数据区增加修订指针，正文未改写 |
| `configs/document-evaluation/rubric/1.0.0.json` | 新增（冻结） | 1.0.0 评测标准：4 指标 × 5 检查项、五态判定、算式、证据规则、fixability 分级 |
| `docs/development/document-quality-report-generation-plan.md` | 状态更新 | Proposed → Accepted，关联决策补 ADR-0003 |

### ADR-0003 与计划 §10.2 修订要点对照

| 计划修订要点 | ADR-0003 落点 |
|---|---|
| 专用流水线不经 Contract/Plan，零配置经平台内置默认实现满足 | 决策 1 |
| 结果持久化以 MetricResultV1 兼容语义为准，Skill 私有输出以此为映射目标 | 决策 2 |
| DocumentQualityReport 为独立 report kind，综合评测时泛化 subject 并迁移 | 决策 3 |
| rubric canonical 副本位于 `configs/document-evaluation/rubric/`，Skill 转上游来源 | 决策 4 |

### 验收记录

| 检查项 | 结果 | 说明 |
|---|---|---|
| ADR 覆盖计划 §10.2 全部修订要点 | 通过 | 对照表见上 |
| ADR-0001 修订为窄幅 | 通过 | 仅元数据指针，未改写正文条款 |
| rubric 结构完整性 | 通过 | 脚本校验：4 指标 × 5 检查项 = 20、item_code 连续（0101-01…0104-05）、五态/fixability/allowed_scores 枚举与计划 D6 一致、standard.version=1.0.0 |
| 冻结文件 sha256 稳定 | 通过 | 三次独立计算（Python hashlib + shasum ×2）一致，值见下 |
| JSON 可解析 | 通过 | `json.loads` 通过 |
| 未触碰其他功能 | 通过 | 本阶段零代码改动，仅上述 4 个文档/配置文件 |

冻结文件 sha256：

```text
fa8dc4a6552f6b7f3f946222174e91a55b651b67c96bc6d0f71f44a56890e6  configs/document-evaluation/rubric/1.0.0.json
```

### 设计说明

- rubric 内容为 Skill `references/evaluation-rubric.md` 的忠实迁移（中文化，协议枚举保留英文）；SKILL.md 级的执行边界（防幻觉、不覆盖原文档等）属 P2 prompt 素材，不在本标准文件内。
- 总分口径（等权平均）为平台口径，**未写入** rubric 标准，仅在 `scoring.overall_score` 声明该边界，由报告层 `scoring_policy` 披露——与计划 D3 一致。
- 冻结纪律：后续修改评分口径必须升级版本号另存新文件（如 `1.1.0.json`），不得原地改写本文件，否则 sha256 即失效。

---

## P1：DocumentVersion 版本管理

- 状态：已完成（2026-10-28）
- 范围：实体、ORM、迁移、Service、上传/列表/元数据/下载/删除 API、前端 Tab（上传+列表+下载+删除）
- 计划验收标准：集成测试覆盖上传→列表→下载哈希一致→删除；被引用版本删除返回 409；非 .md/超限/非 UTF-8/空文件全部 422

### 交付物

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/qa_eval/domain/entities/document_version.py` | 新增 | DocumentVersion 实体（不可变、无生命周期状态机） |
| `src/qa_eval/domain/repositories/document_version_repository.py` | 新增 | 仓储接口（含 `next_version_label` 自动分配 v1、v2…） |
| `src/qa_eval/infrastructure/db/models/document_version.py` | 新增 | `document_versions` + `document_report_tasks` ORM（列 + document JSON 模式） |
| `src/qa_eval/infrastructure/db/repositories/sqlalchemy_document_version.py` | 新增 | SQLAlchemy 实现；引用检查统计 `document_report_tasks` |
| `src/qa_eval/application/services/document_version_service.py` | 新增 | 上传校验（.md/≤2MB/UTF-8/非空）、存储、元数据（行数、解析状态）、下载、删除 |
| `apps/api/routes/document_versions.py` | 新增 | 5 个端点：POST 上传 201、GET 列表、GET 元数据、GET 下载流、DELETE |
| `migrations/versions/b4e7f2a8c1d9_add_document_versions.py` | 新增 | 两表 + `(dataset_id, version)` 唯一约束；`document_report_tasks` P1 仅建表供引用检查，执行器 P2 落地 |
| `apps/web/src/api/documentVersions.ts` | 新增 | 前端 API client |
| `apps/web/src/pages/DatasetDetailPage.tsx` | 修改 | 新增「说明文档版本」Tab：上传（前端预校验）+ 版本表格（下载/删除） |
| `src/qa_eval/application/services/dataset_service.py` | 窄幅修改 | 数据集删除级联删除其说明文档版本（计划 §6.1）；报告任务引用由 RESTRICT 兜底 |
| `src/qa_eval/domain/exceptions.py` | 追加 | `DocumentVersionInUseError`（code `DOCUMENT_VERSION_IN_USE`） |
| `apps/api/dependencies.py`、`apps/api/main.py` | 修改 | 依赖注入 + 路由注册 |
| 既有测试 2 处 | 修改 | `test_dataset_service.py`、`test_dataset_deletion_db.py` 适配 `DatasetService` 新构造参数，并补级联删除测试 |

### API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/datasets/{dataset_id}/document-versions` | multipart 上传（.md），201 |
| GET | `/api/v1/datasets/{dataset_id}/document-versions` | 版本列表 |
| GET | `/api/v1/document-versions/{id}` | 元数据（sha256、行数、解析状态） |
| GET | `/api/v1/document-versions/{id}/download` | 原始文件流（Content-Disposition 附件） |
| DELETE | `/api/v1/document-versions/{id}` | 204；被报告任务引用返回 409 |

### 验收记录

| 检查项 | 结果 | 说明 |
|---|---|---|
| 上传→列表→下载哈希一致→删除 | 通过 | `test_document_version_db.py::test_upload_list_download_delete_flow`（真实 qa_eval_test PostgreSQL + 本地 ArtifactStore） |
| 被引用版本删除返回 409 | 通过 | DB 级：插入 `document_report_tasks` 行后删除抛 `DocumentVersionInUseError`（dependencies 含计数）；API 级：409 + `DOCUMENT_VERSION_IN_USE` 结构化 detail |
| 非 .md / 超限 / 非 UTF-8 / 空文件 422 | 通过 | DB 级与 API 级参数化负例；空含"仅空白字符"文件同拒 |
| 版本号自动分配 | 通过 | v1→v2 顺序分配；`(dataset_id, version)` 唯一约束 IntegrityError 实测兜底 |
| 数据集删除级联文档版本 | 通过 | 单测（fake）+ 既有删除链路全绿 |
| 未回归现有链路 | 通过 | 完整 pytest：238 passed、6 skipped（仅 `RUN_MYSQL_INTEGRATION=1` 的 MySQL 集成项）；ruff 全绿 |
| 迁移 | 通过 | `qa_eval_test` 与开发库 `qa_eval` 均升级至单一 head `b4e7f2a8c1d9` |
| 前端 | 通过 | `tsc -b` 无错误；Tab 含上传预校验（类型/大小/非空）、SHA-256 短码展示、下载与删除 |

### 设计说明

- 文件名做目录成分剥离（`docs/readme.md` → `readme.md`），防存储路径分叉。
- 元数据 `line_count` 按需从 ArtifactStore 读取（≤2MB），不在 DB 冗余存储；`parse_status` 在 P2 生成解析 artifact 后转为 `PARSED`。
- 上传以 `LookupError`（404）区分数据集不存在，与校验失败 `ValueError`（422）分离。
- 「最近报告」列与「生成评测报告 / 查看报告」操作为占位，P3 接入。
- 本阶段无新 wire 契约（报告契约属 P2），故 `generate_json_schemas.py` 无变更。



## P2：评测流水线（LLM 客户端、解析/校验器、任务执行器、报告契约）

- 状态：已完成（2026-10-28）
- 范围：LLM 客户端基础设施、Markdown 解析器与证据校验器、prompt/输出契约、逐指标评测器（含重试与截断）、报告汇总器（分数程序复算）、DocumentQualityReportV1 契约与 JSON Schema、任务执行与 ORM、后台线程路由、启动 orphan 清扫
- 计划验收标准：validator 单测 green on golden + tampered 负例；mock-LLM 集成覆盖 success / partial ERROR / validation-retry 耗尽 / transport-error / process-restart sweep；总分恒等于复算；不回归 `tests/integration`；新契约 JSON Schema 纳入 `generate_json_schemas.py`

### 交付物

| 文件 | 类型 | 说明 |
|---|---|---|
| `src/qa_eval/infrastructure/llm/client.py` | 新增 | `OpenAICompatibleLLMClient`：OpenAI 兼容 chat completions、response_format json_object、代码块容错抽取、传输级重试、调用/失败统计；未配置返回 `None` |
| `src/qa_eval/infrastructure/llm/__init__.py` | 新增 | 导出 `LLMError`、`LLMTransportError`、`LLMResponseError`、`OpenAICompatibleLLMClient` |
| `src/qa_eval/application/document_evaluation/parser.py` | 新增 | `parse_markdown`：UTF-8 解码、行号化、ATX 标题结构、sha256/line_count/size_bytes；`ParsedDocument` 不可变 |
| `src/qa_eval/application/document_evaluation/prompts.py` | 新增 | `SYSTEM_PROMPT` + `OUTPUT_CONTRACT` + `build_metric_user_prompt`；含五态定义、6 条硬性边界、重试错误反馈段 |
| `src/qa_eval/application/document_evaluation/validator.py` | 新增 | `LLMItemJudgment` / `LLMMetricJudgment`（extra=forbid）；`validate_metric_judgment`：rubric 顺序/编码一致、证据行号范围、摘录逐字命中、MISSING 语义、必填字段 |
| `src/qa_eval/application/document_evaluation/evaluator.py` | 新增 | `DocumentEvaluator.evaluate`：逐指标 LLM 调用→Pydantic 解析→程序校验；校验失败带反馈重试（`max_validation_retries`）；传输失败立即 ERROR 不计入校验重试；截断披露与进度回调 |
| `src/qa_eval/application/document_evaluation/report_builder.py` | 新增 | `build_report`：SATISFIED 计 100、其余计 0；指标得分=满足/5×100；总分=四指标等权平均；任一 ERROR 总分置空；missing_items/recommendations 自动汇总 |
| `src/qa_eval/application/document_evaluation/rubric.py` | 新增 | `load_rubric`：相对 cwd/repo-root 解析路径；计算 sha256；检查 metrics/每指标 5 项 |
| `src/qa_eval/application/document_evaluation/__init__.py` | 新增 | 流水线组件统一导出 |
| `src/qa_eval/contracts/document.py` | 新增 | `DocumentQualityReportV1` 完整 Pydantic 契约（extra=forbid）及所有子段 |
| `schemas/qa-eval/document-quality-report/1.0.schema.json` | 新增 | 由 `generate_json_schemas.py` 生成 |
| `scripts/contracts/generate_json_schemas.py` | 修改 | 注册 `DocumentQualityReportV1` |
| `src/qa_eval/infrastructure/db/models/document_quality_report.py` | 新增 | `document_quality_reports` ORM：PK report_id、FK document_version_id / task_id（unique）、execution_status、document JSONB、generated_at |
| `migrations/versions/f3a1c5d7e9b2_add_document_quality_reports.py` | 新增 | 报告表 + 索引 + task_id 唯一约束；down_revision `b4e7f2a8c1d9` |
| `src/qa_eval/domain/repositories/document_version_repository.py` | 修改 | 新增 `set_parsed_artifact_uri(version_id, uri)` 抽象方法 |
| `src/qa_eval/infrastructure/db/repositories/sqlalchemy_document_version.py` | 修改 | 实现 `set_parsed_artifact_uri` |
| `src/qa_eval/application/services/document_report_task_service.py` | 新增 | 任务创建、执行（解析→LLM 评测→报告落库）、查询、启动 orphan RUNNING 清扫 |
| `apps/api/routes/document_report_tasks.py` | 新增 | POST 202（创建并派发后台线程）、GET 列表/详情；`ThreadPoolExecutor` 前缀 `qa-eval-document-runner` |
| `apps/api/routes/document_quality_reports.py` | 新增 | GET /{report_id} |
| `apps/api/dependencies.py` | 修改 | 新增 `get_document_llm_client`（未配置 503）、`get_document_report_task_service` |
| `apps/api/main.py` | 修改 | 注册两个新路由；lifespan 启动 best-effort 孤儿任务清扫 |
| `pyproject.toml` | 修改 | 新增 `httpx>=0.28.1,<1.0` 依赖 |
| `tests/unit/test_document_parser.py` | 新增 | 解析器单元测试 |
| `tests/unit/test_document_validator.py` | 新增 | 校验器 golden + 篡改行号/摘录/顺序/枚举/MISSING 语义负例 |
| `tests/unit/test_llm_client.py` | 新增 | MockTransport：成功、代码块、非 JSON、超时、非 2xx、未配置 |
| `tests/unit/test_document_evaluator.py` | 新增 | mock LLM：成功、重试成功、重试耗尽 ERROR、传输失败、截断、进度回调 |
| `tests/unit/test_document_report_builder.py` | 新增 | 分数复算、等权平均、ERROR 总分置空、聚合 |
| `tests/integration/test_document_report_task_db.py` | 新增 | 真实 DB + mock LLM：success / partial ERROR / all ERROR / retry 耗尽 / transport / 非 PENDING 拒绝 / orphan sweep |
| `tests/integration/test_document_report_task_api.py` | 新增 | mock service：POST 202/404/422/503、GET list/detail/report 200/404 |
| `tests/contracts/test_canonical_contracts.py` | 修改 | 新增 `document-quality-report/1.0.schema.json` CASES |
| 既有 fake repo 测试 2 处 | 修改 | `test_document_version_service.py`、`test_dataset_service.py` 的 Fake 补齐 `set_parsed_artifact_uri` |

### API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/document-report-tasks` | 创建并派发评测任务，202；LLM 未配置 503；文档版本不存在 404；校验失败 422 |
| GET | `/api/v1/document-report-tasks` | 任务列表；可过滤 `document_version_id` |
| GET | `/api/v1/document-report-tasks/{task_id}` | 任务详情 |
| GET | `/api/v1/document-quality-reports/{report_id}` | 质量报告 |

### 产物路径约定

- 行号化解析产物：`datasets/{dataset_id}/documents/{document_version_id}/parsed/lines.json`
- 单指标 items 产物：`datasets/{dataset_id}/documents/{document_version_id}/reports/{report_id}/metrics/{metric_code}.items.json`
- 完整报告产物：`datasets/{dataset_id}/documents/{document_version_id}/reports/{report_id}/report.json`

### 验收记录

| 检查项 | 结果 | 说明 |
|---|---|---|
| validator golden fixture 通过 | 通过 | `test_document_validator` |
| 篡改行号/摘录负例 | 通过 | `test_document_validator::test_tampered_line_number_rejected`、`test_tampered_excerpt_rejected` |
| mock-LLM success | 通过 | `test_document_evaluator`、`test_document_report_task_db::test_success_flow_persists_report_and_artifacts` |
| partial ERROR → PARTIAL_SUCCEEDED + null overall | 通过 | `test_document_report_task_db::test_partial_error_produces_partial_report_with_null_overall` |
| all ERROR → FAILED 无报告 | 通过 | `test_document_report_task_db::test_all_error_metrics_fail_task_without_report` |
| validation-retry 耗尽 → metric ERROR（非 0） | 通过 | `test_document_evaluator::test_retry_exhaustion_marks_error_not_zero`、`test_document_report_task_db::test_retry_exhaustion_consumes_configured_attempts` |
| transport error → 立即 ERROR 不消耗重试 | 通过 | `test_document_evaluator::test_transport_failure_is_immediate_error`、`test_document_report_task_db::test_transport_failure_fails_task_without_report_row` |
| process-restart sweep | 通过 | `test_document_report_task_db::test_sweep_marks_running_tasks_failed` |
| 总分恒等于复算 | 通过 | `test_document_report_builder` + `test_document_report_task_db::test_success_flow_persists_report_and_artifacts` |
| 不回归现有链路 | 通过 | 全量 `pytest`：293 passed、6 skipped（仅 MySQL 集成）；ruff 全绿；`generate_json_schemas.py --check` 通过 |
| 新契约 JSON Schema | 通过 | `schemas/qa-eval/document-quality-report/1.0.schema.json` 已生成并纳入 `generate_json_schemas.py`；`test_canonical_contracts` 54 passed |
| 迁移 | 通过 | `qa_eval_test` 与开发库 `qa_eval` 均升级至包含 `document_quality_reports` 的单一 head（含环境已有 `a7d2c6f41b90`） |

### 设计说明

- **分数口径**：LLM 输出不含任何分数字段；指标得分与总分由 `report_builder` 按 rubric 复算，SATISFIED=100、其余=0，等权平均；任一指标 ERROR 总分置空并披露原因（D3）。
- **重试边界**：传输级失败在 `OpenAICompatibleLLMClient` 内重试；响应格式/校验失败在 `DocumentEvaluator` 层带错误反馈重试 `document_max_validation_retries` 次；传输失败直接置 ERROR，不消耗校验重试额度。
- **截断透明性**：`document_max_prompt_lines` / `document_max_prompt_chars` 双重上限；超出时 `truncated_after_line` 写入 prompt，并在 `limitations` 中披露。
- **任务状态**：PENDING → RUNNING → SUCCEEDED/PARTIAL_SUCCEEDED/FAILED；全部指标 ERROR 时 FAILED 且不生成报告；孤儿 RUNNING 在 lifespan 启动时 best-effort 清扫。
- **配置冻结**：任务创建时把 `evaluator_version`、`prompt_version`、`model`、`temperature`、`max_output_tokens`、`max_validation_retries` 快照进 `config` JSONB，保证同任务内一致。
- **LLM 未配置**：创建入口 `get_document_llm_client` 直接 503，避免生成不可执行的任务；查询/报告路由不依赖 LLM。

## P3：前端闭环（一键生成、报告页、历史任务）

- 状态：已完成（2026-10-28）
- 范围：前端 API client、说明文档版本面板新增「生成报告/查看报告」入口、任务状态轮询、报告详情页
- 计划验收标准：在「说明文档版本」Tab 一键创建评测任务并实时轮询状态；任务完成后可查看报告详情页；报告页展示总分、四维度指标、检查项证据、缺失项与建议；`tsc -b` 无错误；不回归既有测试

### 交付物

| 文件 | 类型 | 说明 |
|---|---|---|
| `apps/web/src/api/documentReportTasks.ts` | 新增 | `createDocumentReportTask`、`listDocumentReportTasks`、`getDocumentReportTask`、`getDocumentQualityReport`；task/report 类型定义与状态辅助函数 |
| `apps/web/src/pages/DocumentQualityReportPage.tsx` | 新增 | 报告详情页：总体得分、四维度折叠面板、检查项证据（行号+逐字摘录）、缺失项汇总、修改建议、评测限制 |
| `apps/web/src/pages/DatasetDetailPage.tsx` | 修改 | DocumentVersionsPanel 增加：最近任务状态列、生成报告按钮、查看报告按钮、2 秒轮询未完成任务、加载各版本历史任务 |
| `apps/web/src/App.tsx` | 修改 | 新增 `reportView` 状态与 `DocumentQualityReportPage` 渲染；导航切换时清理报告视图 |

### 验收记录

| 检查项 | 结果 | 说明 |
|---|---|---|
| 一键生成报告 | 通过 | 说明文档版本行「生成报告」按钮调用 POST `/api/v1/document-report-tasks`；按钮在任务未完成前禁用并显示 loading |
| 任务状态轮询 | 通过 | 未完成任务每 2 秒轮询 `GET /document-report-tasks/{task_id}`；完成后刷新任务状态并切换为「查看报告」 |
| 历史任务展示 | 通过 | 列表加载各版本的最近任务，状态以 Tag 展示（等待中/评测中/已完成/部分完成/失败） |
| 报告详情页 | 通过 | 独立页面展示报告头、总分（ERROR 时显示「—」并说明原因）、四维度指标、检查项状态与证据、缺失项、建议；返回按钮可回到数据集详情 |
| 类型检查 | 通过 | `cd apps/web && npx tsc -b` 无错误 |
| 不回归现有链路 | 通过 | 全量 `pytest`：294 passed、6 skipped（仅 MySQL 集成）；ruff 全绿 |

### P3 验收问题修复

| 问题 | 修复 |
|---|---|
| LLM timeout 默认 120s，失败后前端长时间显示「评测中」 | `llm_timeout_seconds` 默认值改为 60.0，`llm_transport_retries` 默认值改为 1；可在 `.env` 用 `QA_EVAL_LLM_TIMEOUT_SECONDS` 覆盖 |
| 后端已失败但前端仍显示「评测中」 | 前端轮询增加失败计数：连续 3 次获取任务状态失败后，本地将任务降级为 `FAILED`，按钮恢复为「生成报告」可重试 |
| 全量测试偶发 `test_from_settings_returns_none_when_unconfigured` 失败 | 该测试增加 `monkeypatch` 清空 `QA_EVAL_LLM_*` 环境变量，避免被本地 `.env` 污染 |

### 设计说明

- 报告页不直接轮询任务，仅展示已生成的报告；创建任务后的轮询由列表页负责。
- 生成报告失败（如 LLM 未配置 503）通过 `message.error` 提示后端返回的 detail。
- 报告页证据展示使用 `blockquote` 突出逐字摘录，与行号对齐，便于用户定位原文。
- 所有新增 UI 文本保持中文，状态/枚举沿用契约英文键做内部映射。

## P4（backlog）：evidence-assisted、subject 泛化迁移等

- 状态：未开始
