# 0204 Metric–Calculator 建模纠偏迁移验收

## 最终关系

- `0204@1.0.0 → structural_completeness@1.0.0` 保持原行为。
- `0204@2.0.0 → structural_completeness@1.0.0` 产出字段 / Content Role 完整性 measurements。
- `0204@2.0.0 → policy_structure_compliance@1.0.0` 产出政策正文结构 measurements。
- BG-02-03/04/05 是规则来源，不是 Metric 或独立 Check Item 实体。
- `02040305@1.0.0` 已从文件 Registry 和目标开发数据库撤销。

`0204@2.0.0` 的 measurement schema 是两个 Calculator 输出集合的并集。每个
Binding 通过 `produced_measurements` 声明自己的输出集合；Contract、Proposal 和
Logical Planner 均拒绝不匹配的 measurement–Calculator 组合。旧 Binding 缺少该
字段时继续使用历史兼容行为。

## 选择、执行和报告

Contract 以唯一 item key 保存用户确认的 metric、measurement、Calculator
key/version 和参数。单选时只生成一个 item；明确选择两个方法时保存两个 item，
Planner 生成两个独立 operation，Runner 持久化两个不同 UUID 的 MetricResult。
Rule Evaluation 按 measurement 将 Contract item 关联到对应结果；Report 以
Metric + Calculator 区分同一 Metric 的多个结果，不合并 observation 或得分。

推荐流程只使用每个 Metric 的最新定义版本，避免同时推荐 `0204@1.0.0` 和
`0204@2.0.0`。推荐仍只产生被选中的 Contract item，不会因为一个 Metric 有多个
Binding 而默认执行全部 Calculator。policy Calculator 的文种识别仅影响内部解析。

## Registry 数据库回滚

目标数据库：`data/runtime/qa-eval.sqlite3`（项目 `.env` 指向的本地 SQLite 开发库）。

执行前审计结果：

- `metric_definitions`：1 条 `02040305@1.0.0`。
- `metric_calculator_bindings`：1 条错误 Binding。
- `calculator_implementations`：1 条共享 policy Calculator（保留）。
- Contract、Logical/Physical Plan、Run、Metric Result、Rule Evaluation、Issue、
  Report 及其他 document 表：0 条引用。

回滚工具 `scripts/rollback_02040305_registry.py` 默认 dry-run，执行模式要求显式数据库
和备份目录，包含全 document 表引用检查、事务、执行后验证和幂等行为。执行前备份：

- `data/runtime/registry-backups/02040305-registry-20261106T023219Z.json`
- `data/runtime/registry-backups/qa-eval-before-02040305-rollback-20261106T023219Z.sqlite3`

回滚后重新 Registry Sync：16 个 Metric Definition、19 个 Calculator、20 个
Binding。数据库仅保留 `0204@1.0.0`、`0204@2.0.0` 及三条 0204 Binding；
`02040305` Metric/Binding 均为 0，policy Calculator 原记录保留且支持范围更新为
`0204 >=2.0.0,<3.0.0`。再次 dry-run 无删除目标，证明操作幂等。

## 验证结果

- policy-only E2E：成功，保留 parser、source-map、validator、Evidence 行为。
- structural-only：既有 `0204@1.0.0` 正式运行回归成功。
- 同一 Contract 双 Calculator E2E：2 个 Stage、2 个 MetricResult、2 个
  RuleEvaluation、2 个带 Calculator 身份的 Report metric section，均未覆盖。
- Registry、Loader、Code Digest、Contract、Logical/Physical Plan、Run、Result、
  Evidence 和 Report 回归：通过。
- 测试：`485 passed, 6 skipped`；跳过项均为需显式环境开关的 MySQL 集成测试。

## 兼容边界

- `0204@1.0.0` 未修改，旧 Contract 和历史结果继续按原版本解析。
- 未改政策结构解析、Flag、聚合或 Evidence Schema；Calculator code digest 仍覆盖
  package 内所有影响结果的本地源码。
- 报告的正式综合评分仍是 Metric 级；两个 Calculator 的 observations 不自动形成
  0204 综合分数。
- 当前自动推荐根据适用性和成本选择一个候选；用户可通过 Proposal/Contract 的多个
  item 明确选择两个。仓库没有独立前端 Calculator 选择页面，本次未新增 UI。
