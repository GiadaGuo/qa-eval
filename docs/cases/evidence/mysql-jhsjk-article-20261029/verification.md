# 案例证据核验记录

- 数据链一致：Report、Run、Physical Plan、Contract 均引用 Dataset Version `01a1852e-189d-74f0-ad95-b00793cd80bc`；Report 与 Run 均引用 Contract Version `01a1852e-251f-72fe-8f28-504307408481`。
- 执行事实：Physical Plan 只有 0204、02080103 两个 stage；数据库和 API 也只有对应两个 `MetricResult(status=OK)`，未执行指标没有伪 Result。
- 0204：`14928 / 14960 = 0.9978609625668449`，Rule `GTE 0.95` 为 PASS；32 条缺失/空字符串证据，artifact SHA-256 为 `a14a2d...ebe6`。
- 02080103：affected `89 / 14928 = 0.005961950696677385`，redundant `45 / 14928 = 0.0030144694533762056`，Rule `LTE 0.05` 为 PASS；44 个重复组、45 个 confirmed redundant issues。
- ERROR/FAIL 语义：本次两个 MetricResult 均为 OK，两个 Rule 均为 PASS；实现与测试保留 ERROR 不映射为 FAIL 的语义。本次 Run 本身不包含 ERROR 样本，不能作为 ERROR 分支的运行时实证。
- 雷达图语义：0201、0202、0203、0205、0206、0207 为 `null/N/A`；0208 只采用叶子 02080103，父子不重复计权。
- 分数语义：已评测得分 `99.59495` 与有效覆盖率 `14.2857%` 并列，报告包含“不代表指南全指标综合质量”的免责声明。
- UI 证据限制：本次从本地 HTTP API 调用了与工作台相同的端点，但没有浏览器实际点击录像/截图，因此 UI 点击验收标记为 `NOT_EXECUTED`。
- Agent 响应模式：已配置的 `kimi-k2.6` 网关拒绝 `temperature=0.1`（该模型只接受 1），重试后按设计进入 `DETERMINISTIC_FALLBACK`；Skill/Tool/Proposal/Contract/Run 均是真实后端轨迹，不是前端模拟。Provider 参数兼容性仍需修复。

## 质量门

```text
alembic development database: e84d9b73a6f1 (head)
alembic qa_eval_test database: e84d9b73a6f1 (head)
pytest: 371 passed, 6 skipped, 1 warning
schema drift: passed
frontend lint: passed
frontend build: passed (bundle-size warning)
ruff: failed, 22 findings in pre-existing uncommitted text perplexity/purity and Skill script changes
```

6 个 pytest skip 均为需要 `RUN_MYSQL_INTEGRATION=1` 的 MySQL 直连测试；本案例按方案使用固定上传快照，不把它们记为本次 Run 阻塞项。Ruff 未全绿，因此仓库级质量门仍为部分通过。
