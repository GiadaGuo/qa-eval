# Calculator / Shared Inference 执行重设计

## 结论

采用 **Execution Capability + Adapter**，不扩张既有 `Calculator.execute()` 的语义。规则与统计类实现继续走 `DIRECT`；依赖模型的指标声明为 `SHARED_INFERENCE`，共享 Operator 只负责模型推理，各 Calculator Adapter 独立负责解析、评分和聚合。

核心不变量是：共享底层推理，不共享指标语义、评分和 Contract 判定。

## 现状审查

- Metric Definition 只保存指标语义、适用性、measurement 与持久化策略，不依赖 Provider。
- Calculator Registry 使用 descriptor + binding 注册；此前 `AVAILABLE` 只检查 entrypoint/code digest，Loader 仅认可统一 `Calculator` Protocol。
- Quality Contract 通过 implementation constraints 绑定 Calculator。
- Logical Planner 已能建立共享语义 operation、group 与独立 output bindings，但依赖硬编码指标集合，且把 `rubric_bundle` 放进兼容 fingerprint。
- Physical Planner 此前忽略共享 operation，始终按合同项创建 Stage。
- Stage Executor 此前固定执行一次 Calculator 并生成一个 Metric Result，重复读取 normalized artifact。
- `metric_results.stage_run_id` 唯一约束进一步阻止一个 Stage 生成多个结果。
- 现有 OpenAI-compatible client 可复用，支持 JSON 输出、传输重试和调用计数；此前未累计 token usage。
- Rule Evaluation、Quality Issue 与 Report 已能消费独立 Metric Result，无需建立平行 runtime。

## 方案比较

### A. 扩展现有 Calculator 接口

给所有 Calculator 增加 prepare/infer/score 生命周期。优点是接口统一；缺点是会迫使全部 DIRECT Calculator 迁移，兼容面大，并容易再次把 Provider 调用混入指标实现。

### B. Execution Capability + Adapter（采用）

descriptor 增加向后兼容、默认 `DIRECT` 的 execution capability。`SHARED_INFERENCE` 实现满足独立评分 Protocol；Physical Executor 根据 mode 选择现有直接链路或共享 Operator 链路。

此方案改动集中，DIRECT 实现零迁移；Registry 仍以真实 entrypoint、Protocol 与 code digest 验证实现，不允许用布尔标志伪装可执行。未来可增加新的 operator type，而不改变 Metric Definition。

## 执行契约

`CalculatorDescriptorV1.execution` 包含：

- `mode`: `DIRECT` / `SHARED_INFERENCE`
- `operator_type`
- `prompt_family`
- 独立 `rubric`
- 独立 `output_field`
- `aggregation_strategy`
- `shareability_constraints`

三个语义 Calculator 均注册真实评分 Adapter，并共享 `llm_semantic_judge` Operator。Operator 不包含指标编号。

## 兼容与 fingerprint

Logical Planner 根据 execution mode 识别候选，不再硬编码指标编号。共享执行 fingerprint 包括 Provider、Model、抽样、Prompt Family、预处理、上下文、隐私策略、输入 selector、operator type 和输入依赖；不同 Rubric 不进入环境兼容 fingerprint，因此可组合。每个 operation 仍保留独立 rubric fingerprint，完整 Logical Plan semantic fingerprint 继续覆盖分组和 output bindings。

已有 Logical/Physical Plan 仍可读取：新增字段都有默认值，旧 Stage 默认为 `DIRECT`。canonical schema 仍为 1.0 的向后兼容字段扩展。

## 新旧链路

旧链路：`Contract item → Physical Stage → Calculator.execute → Metric Result`，三个 LLM 指标会形成三次读取、三套模型调用。

新链路：

```text
Contract items
  → Logical shared group + independent bindings
  → one SHARED_INFERENCE Stage
  → read normalized data once
  → one Provider call per selected record
  → shared structured intermediate outputs
  → three metric scoring adapters
  → three Metric Results
  → existing Rule / Issue / Report flow
```

单指标 `SHARED_INFERENCE` 使用同一 Operator，无需特殊执行器；DIRECT Stage 保持旧链路。

## 示例

Calculator capability：

```json
{
  "mode": "SHARED_INFERENCE",
  "operator_type": "llm_semantic_judge",
  "prompt_family": "text-quality-semantic-v1",
  "rubric": {"criterion": "文本连贯程度", "score_0": "严重跳跃", "score_1": "衔接自然"},
  "output_field": "text_coherence",
  "aggregation_strategy": {"kind": "THRESHOLD_RATIO", "direction": "HIGHER_IS_BETTER"}
}
```

共享 Operator 输出：

```json
{
  "sample_id": "article-1",
  "shared_computation_id": "shared-llm-0",
  "outputs": {
    "knowledge_density": {"score": 0.8, "rationale": "...", "flags": []},
    "information_missing": {"score": 0.2, "rationale": "...", "flags": []},
    "text_coherence": {"score": 0.9, "rationale": "...", "flags": []}
  }
}
```

Physical Stage 的 `stage_kind` 为 `SHARED_INFERENCE`，包含一个 `shared_computation_id` 和三个完整 `output_bindings`。Stage stats 记录 provider calls、failures、latency、token usage、输入数与显式截断数。

## 错误语义与审计

- Provider/超时/JSON envelope 错误：共享 Stage 失败，三个 Metric Result 均为 `ERROR`。
- 某输出字段缺失或某评分 Adapter 失败：仅对应 Metric Result 为 `ERROR`，兄弟指标保留结果。
- 不可评测结果不会转成 0 或 PASS。
- 原始共享结构化输出、inference ID、模型、Prompt Family、截断策略及成本写入审计 artifact；不保存输入原文。

## 数据库与迁移

迁移 `e84d9b73a6f1` 移除 `metric_results.stage_run_id` 唯一约束，改为普通索引，使一个共享 StageRun 能关联多个独立 Metric Result。

## 验证边界

自动化集成测试以 Fake Provider 验证三条记录只调用三次 Provider、三个结果共享同一 StageRun，并覆盖输出缺失及非法结构化响应。真实 MySQL/API 验收依赖外部数据库和密钥，不伪造结果；长文本使用显式 `HEAD_CHARS_WITH_EXPLICIT_RECORD_FLAG` 策略，并在 Stage stats 中报告截断数量。
