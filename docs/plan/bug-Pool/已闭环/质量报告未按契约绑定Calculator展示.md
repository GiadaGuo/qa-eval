质量报告未按 Quality Contract 绑定的 Calculator 展示结果

═══════════════════════════════ 闭环记录（2026-10-28）═══════════════════════════════

## 根因（有代码证据）

- 执行层正确：EvaluationRunService.create() 根据 Quality Contract item 的 implementation_constraints 只把契约绑定的 `text_duplicate_degree` 写入 Physical Plan stages；execute() 只调度这些 stage，只产生一条 MetricResult。
- 数据证据（Run 01a18002-e958-7f5c-93e0-5075f9d1915b）：
  - Contract item：measurement = "word_ngram_repetition_ratio"，calculator = text_duplicate_degree/2.0.0
  - Plan stages：只有一个 stage，calculator = text_duplicate_degree/2.0.0
  - MetricResult：只有 1 条，status=OK，observations 包含 6 个字段（exact_affected_record_ratio、exact_redundant_record_ratio、char_ngram_repetition_ratio、word_ngram_repetition_ratio、near_duplicate_candidate_record_count、paragraph_affected_ratio）
- 问题在报告层：src/qa_eval/application/evaluation/report_generator.py:123 把 MetricResult.observations 全部平铺写入 ReportMetricSection.observations，没有区分契约声明的 measurement 与该次 Calculator 执行附带的诊断字段。
- 前端只渲染后端返回的 metric.observations，未额外调用 Registry。因此根因不在前端。
- 值得注意的是规则判定层本身是正确的：src/qa_eval/application/evaluation/rule_evaluator.py:119 已经用契约 item.metric.measurement 去结果里找观测做阈值判定；报告层没有复用同一语义。

## 修复策略

不修改历史 MetricResult，不在执行层过滤，不删除 Registry 中其他 Calculator，也不改外键/数据库约束。

- 扩展 QualityReportV1 Schema：ReportMetricSection 新增
  - measurement: ReportObservation | null —— 契约 item 选中的主测量
  - diagnostics: list[ReportObservation] —— 同一次执行附带输出的其他观测字段
  - observations 字段保留为全量观测，兼容既有消费者与历史报告快照
- 修改 report_generator.py：
  - 用 contract.items 中每个 item.metric.measurement 从 result.observations 中定位主测量
  - 其余观测放入 diagnostics
  - 当结果状态异常或找不到 measurement 时，measurement = null
- 前端 EvaluationResultsPage.tsx：
  - 新版报告：主结果表格只展示 measurement，其余 diagnostics 折叠在「诊断字段」面板，并明确提示「同一次执行附带输出，非独立计算方法」
  - 旧版报告快照没有 measurement/diagnostics 字段时，回退为平铺展示，保证向后兼容
- 重新生成 checked-in JSON Schema：schemas/qa-eval/quality-report/1.0.schema.json

## 修改文件清单

后端
- src/qa_eval/contracts/quality.py：ReportMetricSection 新增 measurement 与 diagnostics
- src/qa_eval/application/evaluation/report_generator.py：按契约 measurement 拆分主测量与诊断字段
- schemas/qa-eval/quality-report/1.0.schema.json：JSON Schema 同步

前端
- apps/web/src/api/qualityResults.ts：TypeScript 类型新增 measurement / diagnostics
- apps/web/src/pages/EvaluationResultsPage.tsx：主结果 + 可折叠诊断字段，兼容旧报告快照

测试
- tests/unit/test_report_generator.py（新增）：4 个单元测试覆盖主测量、不同契约选择不同测量、ERROR 无观测、测量缺失场景
- tests/integration/test_quality_result_api.py：_seed_contract 参数化 measurement；新增 2 个集成测试验证端到端报告只展示契约 measurement

## 修复前后数据流对比

修复前：
```
Contract: 02080103 → measurement: word_ngram_repetition_ratio
Plan: text_duplicate_degree（1 个 stage）
MetricResult: 1 条，observations 含 6 个字段
Report.metrics[0].observations: 6 行平铺
前端表格：6 行（用户误以为 6 个独立 Calculator）
```

修复后：
```
Contract: 02080103 → measurement: word_ngram_repetition_ratio
Plan: text_duplicate_degree（1 个 stage）
MetricResult: 1 条，observations 含 6 个字段
Report.metrics[0].measurement: word_ngram_repetition_ratio（主结果）
Report.metrics[0].diagnostics: 其余 5 个字段
Report.metrics[0].observations: 仍保留 6 个字段（兼容旧消费者）
前端表格：1 行主结果 + 折叠的 5 个诊断字段
```

## 指定 Run 最终展示结果

Run: 01a18002-e958-7f5c-93e0-5075f9d1915b

修复后生成报告（不修改已持久化旧快照）的 metrics 结构：

| 指标编号 | 指标名称 | 计算方法 | 评测结果 |
|---|---|---|---|
| 02080103 | 重复内容程度 | word_ngram_repetition_ratio | 0.5066655294941792 |

折叠的诊断字段（5 个，非独立计算方法）：
- exact_affected_record_ratio
- exact_redundant_record_ratio
- char_ngram_repetition_ratio
- near_duplicate_candidate_record_count
- paragraph_affected_ratio

## 测试执行结果

- tests/unit/test_report_generator.py：4 passed
- tests/integration/test_quality_result_api.py：13 passed（含 2 个新增集成测试）
- 全量后端 pytest：183 passed, 6 skipped, 1 warning
- JSON Schema 校验：重新生成后 contract schema 测试通过
- 前端 tsc -b && vite build：通过

## 是否存在其他指标受到同类问题影响

是。本问题由报告生成逻辑引起，与具体指标无关。只要满足以下任一条件都会复现：

1. 一个 Metric Definition 注册了多个 measurements（或 Calculator 输出多个观测字段）。
2. Quality Contract 只选择其中某一个 measurement。
3. MetricResult.observations 包含多个字段。

修复后，所有 Metric Definition 的报告都会按各自契约 item 的 measurement 拆分主结果与诊断字段。历史已持久化的 QualityReport 快照仍保留旧结构（observations 平铺），前端通过 measurement === undefined 回退兼容；新 finalize/新 Run 会生成包含 measurement/diagnostics 的新结构。

## 历史报告限制

QualityResultService.finalize 对同一 run_id 幂等：已存在报告时直接返回旧快照，不会重新生成。因此：
- 旧 Run 的持久化报告 JSON 仍是 observations 平铺结构；
- 前端回退逻辑保证旧报告仍可正常显示；
- 如需刷新历史报告展示效果，需删除 quality_reports 行并重新 finalize（会重新走 evaluate_contract / issues / index / build_quality_report），但该操作会重写 issues/rule_evaluations，生产环境应谨慎。
