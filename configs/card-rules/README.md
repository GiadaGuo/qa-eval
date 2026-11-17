# BG-01 P0 契约冻结决策

- 日期：2026-11-16
- 状态：技术契约已冻结；业务必填级别、正式编码及权重待批准
- 边界：仅 P0，不含表、迁移、API、Calculator 或执行链实现

1. 通用四要素声明使用 `dataset-card-declarations/1.0`；Schema 只要求四个要素容器，不写入未批准的字段必填级别或政务枚举。
2. BG-01 使用独立版本化 RuleSet。字段 REQUIRED/CONDITIONAL/OPTIONAL 是待审分类，均为 `PENDING_BUSINESS_CONFIRMATION` 且禁用；`null`/空串为结构空值，“无”保留为待确认业务值。
3. 临时 Metric key 为 `BG01_CARD_CONFORMANCE`、`BG01_CARD_DATA_CONSISTENCY`，状态为 `TEMPORARY_UNAPPROVED`。Measurement、coverage、不可评测原因及 Evidence 由 `bg01-evaluation-contract@1.0.0` 冻结；无批准权重不评分，不可评测项不以 0/1 代替。
4. `bg01-lifecycle-contract@1.0.0` 冻结四类版本的独立生命周期与 Snapshot 精确绑定关系。Snapshot 内容寻址、不可变并可幂等复用。
5. Calculator Protocol 仅在 P2 additive 增加默认空的 `input_artifacts` 与可选 `progress`，保留 `input_path`；P0 不修改协议或 Worker。

待批准项：正式 BG-01 编码/父节点/权重、各字段必填级别、政务枚举、标签语义、容差及分层字段。冻结清单的增删或语义变更必须发布新版本。
