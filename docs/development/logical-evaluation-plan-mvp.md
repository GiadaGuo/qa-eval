# Logical Evaluation Plan MVP 开发方案与实施记录

- 状态：已实施
- 日期：2026-10-28（Asia/Shanghai）
- 范围：Logical Plan、可消费的 Stage DAG 契约；不包含 Agent Runtime 生产化、真实共享 LLM 推理或分布式调度

## 1. 现状审查

项目已有能力包括：Metric Definition、Calculator Descriptor 与 Binding 注册表；支持多 item 的 Quality Contract；Dataset Version、Field Mapping、Dataset Profile；Physical Plan、Evaluation Run、Stage Run、Metric Result、Rule、Issue 和 Report 闭环。单指标 Contract 由 `publish()` 转换为一个 item 的多指标 Contract，因此无需另建 Contract 协议。

主要缺口是 Contract 直接编译为 Physical Plan，缺少可独立校验、查询和复用的 Logical Plan；Physical Stage 只有顺序号，未保留逻辑操作来源和依赖边；占位 Calculator 直到运行创建阶段才被拒绝；一次语义计算对应多个 Metric Result 的输出映射没有模型。

本次复用现有 `QualityContractVersionV1`、Registry、Dataset Version/Profile、`PhysicalEvaluationPlanV1` 和执行服务。不会在 Agent 或 Calculator 内建立平行 Planner，也不会改变原有单指标入口。

## 2. 设计

`LogicalEvaluationPlanV1` 固化：数据集/契约版本、编译器、逻辑操作、Calculator binding 的逻辑身份、输入依赖、依赖边、共享计算组、独立 Metric Result 输出绑定、结构化诊断和语义指纹。

Planner 流程：

1. 校验 Published Contract 与 Published Dataset Version。
2. 解析每个 Contract item 对应的 Metric、Calculator 和 Binding。
3. 拒绝未注册、disabled、UNAVAILABLE/DEPRECATED 或无 entrypoint 的占位实现。
4. 根据 Field Mapping 校验 modality 与 content role；按 descriptor 校验 scope、artifact、profile 和参数 schema。
5. 生成每指标逻辑操作和独立 output binding。
6. 按确定性兼容键识别共享语义组。
7. 校验依赖引用、唯一 ID 和环，生成 canonical semantic fingerprint。
8. 持久化 Logical Plan，再编译现有 Physical Plan；Physical Stage 保存 `logical_operation_ids` 与 `depends_on`。

共享 LLM 分组只有在三个指标属于允许集合，并显式声明 `allow_shared_execution`、独立输出能力，且 provider、model、抽样指纹、prompt family、rubric bundle、预处理、输入字段、上下文和隐私策略完全相同时才合并。不兼容成员保持独立。共享组提供共同 `source_identity`，每个 metric output binding 仍单独存在。

## 3. 持久化与 API

新增 `logical_evaluation_plans` 表。Physical Plan 通过可空外键引用 Logical Plan，以兼容迁移前历史数据。

查询接口：

- `GET /api/v1/evaluation-runs/logical-plans/{logical_plan_id}`
- `GET /api/v1/evaluation-runs/logical-plans/by-contract/{contract_version_id}`

创建 Evaluation Run 的外部接口不变，内部链路变为：

```text
Published Contract
  → LogicalPlannerService
  → LogicalEvaluationPlan
  → PhysicalEvaluationPlan / Stage DAG
  → EvaluationRun
```

## 4. 测试方案

覆盖单指标兼容、多规则/混合指标、三个语义指标共享、模型不兼容拆组、占位 Calculator、缺少角色、未知依赖和环检测；现有 Evaluation Run 创建与真实 Calculator 集成测试作为回归。共享语义测试只验证规划，不生成或冒充真实模型结果。

## 5. 当前限制与后续

- 当前注册表中的 02080102、02080105、02080107 仍是不可执行占位，因此生产 Contract 会被诚实拒绝；测试使用仅参与 Planner 的 descriptor fixture，不执行模型。
- Logical Plan 已表达共享计算与多输出，现有本地执行器尚不会执行共享 LLM operation。
- Stage DAG 已有稳定依赖字段与逻辑来源，后续 Physical Planner 应把共享 operation 编译为一个 stage，并由结果拆分器生成三个 Metric Result。
- 下一阶段补充 dependency-aware Stage 调度、失败传播、重试/缓存、共享推理 artifact 与 token/耗时记账；这些不得反向改变 Logical Plan 的语义身份。
