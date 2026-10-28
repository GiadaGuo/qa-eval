# 下一阶段闭环 0：可信开发基线验收记录

- 验收日期：2026-10-28
- 结论：通过
- 范围：canonical contract、PostgreSQL migration/test、前端质量门、最小 E2E 主链路

## 1. 固化的 E2E 基线

固定输入为 `tests/fixtures/e2e-text-quality-baseline.jsonl`，共 7 条文本记录：

- 2 条正常记录；
- 1 条空文本；
- 2 条 exact duplicate；
- 2 条内容不同但达到阈值的 near-duplicate candidate。

`tests/integration/test_e2e_baseline.py` 在真实 `qa_eval_test` PostgreSQL 上覆盖：上传 JSONL、保存字段映射、发布 Dataset Version、发布 `02080103` Quality Contract、创建并执行 Run、读取 Metric Result、Report 与 Issue。回归断言同时约束：空文本被排除但不伪装成失败、exact duplicate 生成 CONFIRMED Issue、near duplicate 生成 CANDIDATE Issue、报告主测量来自 Contract 中选择的 measurement。

该测试是浏览器主链路所调用后端能力的可重复自动化验收；本轮同时通过前端生产构建和 lint。人工浏览器冒烟不作为 CI 门禁，发布前仍建议按本文第 4 节做一次 UI spot check。

## 2. 验收结果

| 检查项 | 结果 |
| --- | --- |
| `uv run python scripts/contracts/generate_json_schemas.py --check` | 通过，无 contract/schema 漂移 |
| `uv run alembic heads` | 单一 head：`e6b5c3074f89` |
| `qa_eval_test` 执行 `alembic upgrade head` | 通过，current 为 `e6b5c3074f89` |
| 完整 `pytest` | 184 passed、6 skipped |
| 跳过项 | 仅 6 个需 `RUN_MYSQL_INTEGRATION=1` 的 MySQL 集成测试 |
| 前端 `npm run build` | 通过；仅保留 bundle size warning |
| 前端 `npm run lint` | 通过 |

测试库迁移注意：`migrations/env.py` 以 `QA_EVAL_DATABASE_URL` 为准，会覆盖 Alembic Config 中临时设置的 URL。迁移测试库时必须在该进程中把 `QA_EVAL_DATABASE_URL` 显式设为已配置的 `QA_EVAL_TEST_DATABASE_URL`，并确认 Alembic 输出中的数据库名是 `qa_eval_test`；不能只看 revision 就判定测试库已迁移。

## 3. 当前实现与旧进度文档差异

以下旧记录仅代表当时状态，不再作为当前开发事实：

- `docs/development/canonical-models-progress.md` 中“完整测试因 PostgreSQL 未运行而 error”的记录已失效；当前真实测试库全套通过。
- 同文档中“共享 Registry、Contract、Run、Rule、Issue、Report 尚未完成”的阶段性描述已失效；这些 MVP 共享层已进入当前源码和迁移。
- `dataset-data-quality-evaluator/SKILL.md` 的“当前开发状态”仍保留早期局部链路叙述；平台当前已有正式 Registry、Contract、Physical Plan、Evaluation/Stage Run、Result、Rule、Issue 和 Report。Skill 的职责边界仍有效，但进度摘要不能替代源码事实。
- `docs/development/next-stage-agent-calculator-plan.md` 原始基线中的 2 failed、17 PostgreSQL errors 已由本闭环消除。

## 4. 发布前 UI spot check

使用固定 fixture 执行一次人工检查：

1. 新建文本 Dataset 和 Draft Version，上传 `tests/fixtures/e2e-text-quality-baseline.jsonl`。
2. 将 `sample_id` 映射为 Sample ID，将 `text` 映射为 `article.body.plain`，发布 Version。
3. 发布 `02080103` Contract，measurement 选择 `exact_affected_record_ratio`，Calculator 选择 `text_duplicate_degree@2.0.0`。
4. 创建 Run 并等待成功；Report 应显示主测量、诊断观测和 FAIL gate。
5. Issue 列表应同时存在 CONFIRMED exact duplicate 与 CANDIDATE near duplicate；空文本不应成为重复内容 Issue。

## 5. 非阻塞事项

- 前端构建提示主 bundle 超过 500 kB，属于性能优化项，不影响本闭环正确性。
- FastAPI TestClient 仍有 `httpx`/Starlette deprecation warning，不影响测试结果，后续依赖升级时处理。
