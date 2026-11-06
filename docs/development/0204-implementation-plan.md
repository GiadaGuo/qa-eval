# 0204 政策结构完整性 Phase D–E 实施计划

> 本文仅列后续工作；本次在 Phase A–C 后停止。

## 1. 前置决策（阻断生产注册）

1. 指标身份：保留现有 0204 字段缺失语义并新增子指标，或发布 0204 不兼容版本。
2. 产品结果形态：接受“一 MetricResult + 多 measurements + sample evidence”，还是要求四个独立
   MetricResult/sample Flag API。
3. 文种 canonical 来源与置信策略。
4. Ground Truth task key、schema、边界容差与最小标注集规模。
5. 超长 HTML 默认上限及拒绝/全扫策略；是否允许 evidence excerpt。

## 2. Phase D：最小纵向实现（约 8–12 人日）

### D1 指标与 schema（1–2 人日）

- 版本化 Metric Definition，声明所有 observation；不覆盖 0204@1.0.0。
- 冻结 sample evidence schema、`StructureParseResult` 内部 schema、reason/violation code。
- 用 Pydantic/JSON Schema 测四状态闭合、ratio 分母、非 OK 结果约束。

验收：Registry contract 校验通过；既有 0204 binding 行为不变；每个公开 measurement 有唯一语义。

### D2 source-map 与 block extractor（2–3 人日）

- 实现 raw lexical spans、entity/whitespace mapping、DOM path、block boundaries、round-trip validator。
- 覆盖跨标签编号、重复文本、entity、Office HTML、malformed nesting、Unicode/UTF-8 区分。

验收：金标 fixture 上 EXACT 范围逐字符可复核；所有 offset 均声明 coordinate space/index semantics；
超长输入不静默截断。

### D3 mode/parser/numbering（2–3 人日）

- 三模式与 mixed subtree；中文数字；scope-aware sequence；引用/日期/金额/附件排除。
- 建人工校准集，按通知/办法/条例/规划/其他及异常层分层。

验收：报告 candidate detection、node boundary、parent relation 的 precision/recall；阈值未达标时保持
UNAVAILABLE/PARTIAL，不宣称完整覆盖。

### D4 Calculator 与聚合（2 人日）

- 实现 Protocol、严格参数验证、JSONL streaming、incremental evidence、evidence cap、单样本隔离。
- 四 flag 独立状态；gap 去重；无可判分母不输出 ratio。

验收：10 万条合成流式测试内存有界；cap 不改变 aggregate；plain/附件/无编号/无标注状态正确。

### D5 Registry 与说明（1–2 人日）

- descriptor、binding、code digest、开发说明；Loader、Registry sync、Planner 测试。

验收：`TrustedCalculatorLoader` 可加载；digest 一致；旧 calculator regression 全绿。

## 3. Phase E：集成与真实验证（约 5–8 人日，不含标注）

1. `DatasetVersion → Plan → Run → MetricResult → Evidence` E2E。
2. 在固定 321,902 条版本执行；记录 wall time、峰值内存、异常/状态分布、artifact checksum。
3. 人工复核各 violation code 与四状态分层样本；评估 false positive/negative。
4. 确认报告/Rule 层只消费声明 measurements，不把 UNAVAILABLE 当 FAIL。
5. 若需要 PDF/OCR，另立 derived artifact 项目；不在 Calculator 内联网。

验收：全量运行可复现；population 与四组状态分别闭合；Evidence 可按 sample ID 回溯；无全文泄露；
无法判定项有明确 reason；结果不声称未覆盖的法律语义或 segmentation accuracy。

## 4. 可选平台增强（另立 ADR，约 5–10 人日）

仅当产品坚持四个独立 MetricResult 且仍要求一次解析时实施：

- 定义 deterministic shared computation/derived artifact contract；
- Logical/Physical Plan 支持 parser operation → 多 metric scorer dependencies；
- Runner 管理共享 artifact 生命周期、fingerprint、失败隔离；
- sample Flag 的 canonical contract、存储/API 与报告行为。

风险最高的是 contract 版本化和失败语义，而不是 parser 本身。未做该增强时，一个 Calculator + 一个
MetricResult 是最小且与现有平台一致的方案。

## 5. 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| 0204 指标语义冲突 | 高 | 先决策子指标/新版本，禁止覆盖 |
| 无结构 Ground Truth | 高 | 标注集完成前 segmentation=UNAVAILABLE |
| HTML raw offset 漂移 | 高 | lexical source-map + round-trip，不反推 plain |
| 文种/体例混合与引用误报 | 高 | 内容 scoring、scope、人工校准、保守 UNAVAILABLE |
| 8GB normalized 输入性能 | 中 | 单遍、当前样本生命周期、增量 evidence |
| 附件正文缺失 | 中 | 版本化 derived artifact，禁止动态下载 |
| Evidence 暴露正文 | 中 | 默认无 excerpt；有界、声明 raw-content policy |

