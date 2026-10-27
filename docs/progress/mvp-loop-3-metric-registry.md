# MVP 闭环 3 进度：Metric / Calculator Registry

- 日期：2026-10-27
- 状态：3A–3D 已完成
- 对应计划：`docs/development/mvp-development-plan.md` 第 11 章“闭环 3：指标驱动的评测入口（重构）”

## 原阶段目标

将真实指标 `02080103` 的 Metric Definition、Calculator Descriptor 和 Binding 从混合 Skill YAML 中拆分为平台 canonical registry 对象，持久化到 PostgreSQL，提供只读 API 和前端指标管理页面。

## 完成内容

- 新增 `MetricDefinitionV1`、`CalculatorDescriptorV1`、`MetricCalculatorBindingV1` contracts 及 JSON Schema。
- 增加 source-controlled registry seed：
  - `configs/registry/metrics/02080103-v1.0.0.json`
  - `configs/registry/calculators/text-duplicate-degree-v2.0.0.json`
  - `configs/registry/bindings/02080103-text-duplicate-degree.json`
- `02080103` 明确注册六个 measurements、输入 role、语义限制和 candidate-only 解释。
- Calculator 明确注册参数 JSON Schema、输出 Schema、确定性、能力和代码 digest。
- 新增三张 Registry 表及 Alembic migration `9c71f0e8a321`。
- 新增幂等 seed 和查询应用服务。
- 新增只读 API：`/metrics`、`/calculators`、`/metric-calculator-bindings` 及详情端点。
- 新增 React 指标管理页面，展示指标、measurements、Calculator 参数和 Binding。

## 2026-10-27 重构结论

根据 `plan/Idea-Pool/指标管理模块开发.md`，闭环 3 已调整为“指标驱动的评测入口”。现有 Registry 数据层已复用，3A 页面交互已按新定义重构；整个闭环仍不能标记为完成。

3A 已补齐：

- 指标卡片网格及按指标编码/名称搜索。
- 点击指标卡打开详情窗口，并按 Binding 展示 Calculator 卡片。
- 展示 definition、measurement、适用范围、语义限制、参数 schema、输入角色、输出 schema 和证据能力。
- 根据 Binding 状态和 Calculator Descriptor 是否存在计算实现可用性。
- Contract 服务未接入时明确禁用入口并说明原因，不用前端状态模拟成功。

重构后的指标驱动评测闭环已全部接入真实后端。

## 3B：真实 Quality Contract 配置与发布

已完成：

- Calculator 卡片的“构建质量契约”入口已接入真实配置页。
- 配置页选择已发布 Dataset Version、measurement、Calculator 参数、operator、threshold、severity 和 `on_missing`。
- 后端发布请求不接受客户端生成的平台 ID、fingerprint、状态或发布时间。
- 后端校验 Dataset Version 为 PUBLISHED 且存在 Manifest、数据集模态兼容、FieldMapping content roles 满足 Calculator 输入、Metric/Calculator/Binding 存在且 Binding 启用。
- 后端校验 measurement、阈值类型以及 Calculator 参数类型和最小/最大值。
- 发布时生成不可变 `QualityContractVersionV1`，Calculator 选择记录在 implementation constraints 中。
- 新增 `quality_contract_versions` PostgreSQL 表和 Alembic migration `c418dfbd1174`。
- 新增发布、列表和详情 API：`POST/GET /api/v1/quality-contract-versions`。
- 发布成功页展示真实 Contract Version ID、Dataset Version ID 和 fingerprint；EvaluationRun 未接入时明确标记下一步，不模拟执行。

## 3C：正式 EvaluationRun 与单进程后台执行

已完成：

- 新增 canonical `PhysicalEvaluationPlanV1`、`EvaluationRunV1`、`StageRunV1` contracts 和生成的 JSON Schema。
- 从 Published Contract 编译不可变最小 Physical Plan，固化 Calculator key/version/code digest、完整 resolved parameters、输入/输出 schema 和 semantic fingerprint。
- 新增 `physical_evaluation_plans`、`evaluation_runs`、`stage_runs`、`metric_results` 表及 migration `d91a837db256`。
- `POST /api/v1/evaluation-runs` 创建 PENDING Run 并立即返回 `run_id`；单线程后台 dispatcher 使用独立数据库 Session 执行。
- 正式平台 adapter 复用 Skill 已验证的 `02080103` Calculator，不另造算法。
- Runner 从 Dataset Version 的完整 NORMALIZED_DATA artifact 读取 SampleRecord 2.0，真实计算并持久化 canonical MetricResult 1.0。
- `sample_features.jsonl` 和 `duplicate_groups.json` 作为不可变 evidence artifact 提交到 ArtifactStore，MetricResult 只保存带 checksum 的引用。
- Calculator 参数和 fingerprint 固化在 Plan/StageRun；MetricResult 只保存观察事实、population、semantic coverage 和 evidence。
- Calculator 异常生成 `MetricResult(ERROR)`、`StageRun(FAILED)` 和执行错误事实，不生成质量 FAIL。
- 前端发布契约后可点击“开始评测”，轮询正式 Run 状态，并展示持久化 MetricResult observations。
- 判定运算符按优化要求展示为 `<`、`≤`、`>`、`≥`、`=`、`≠` 及中文说明，传输值仍使用 canonical operator code。

## 3D：规则、问题、报告与正式评测结果

已完成：

- EvaluationRun 进入终态后，Rule Evaluator 只读取已持久化 MetricResult observations。
- RuleEvaluation 独立持久化，质量 FAIL 不改变 Calculator 的执行成功状态。
- 从带 checksum 的 DUPLICATE_GROUPS evidence 生成 confirmed exact-duplicate issues 和 candidate near-duplicate issues。
- QualityIssue 独立关联 Run、MetricResult、measurement、subject 和 evidence locator。
- issues 全量写入不可变 ArtifactStore，并生成带 checksum 的 IssueIndex。
- QualityReport 只从已持久化 Contract、MetricResult、RuleEvaluation、QualityIssue 和 IssueIndex 聚合。
- 新增 `rule_evaluations`、`quality_issues`、`issue_indices`、`quality_reports` 表和 migration `e6b5c3074f89`。
- 新增正式 Report、Rule、Issue API，Issue 支持 determination 和分页过滤。
- 主导航新增“评测结果”模块，展示正式 Run 列表、execution/gate 状态、coverage、observations、规则判定和问题下钻。
- `LOCAL_VALIDATION` 继续作为开发诊断入口，与正式平台结果明确分离。

上述流程在产品上连续，但领域层继续保持 Registry、Quality Contract、Evaluation Run 和 Quality Report 分离。当前只读 Registry API、canonical contracts、数据库表和 seed 可以直接复用，不进行推倒重建。

## 当前已完成部分的边界

- Registry 不包含 Contract threshold 或规则；这些仍由 Quality Contract 管理。
- 当前 Registry 基础部分不创建 EvaluationRun，也不把 LOCAL_VALIDATION 登记为平台 Run；EvaluationRun 将在重构后的 3C 中由正式运行服务创建。
- Calculator descriptor 已注册平台目标 entrypoint；将 Skill 原型收敛为正式运行适配器属于后续 EvaluationRun 闭环。
- 目前只有 `02080103` 可执行，未虚构其他指南指标的实现状态。

## 验证

- contract/schema 测试和 schema drift 检查。
- Registry seed 连续执行两次保持单条记录。
- API 返回 02080103、Calculator 2.0.0 和启用 Binding。
- 前端 lint 与 build 通过。
- 3B contract/API/Registry 针对性测试：18 passed。
- 3C 真实 Calculator 执行与 canonical contracts 针对性测试：50 passed。
- 3D Rule/Issue/Report 纵向链路测试通过；测试明确验证 1 个 CONFIRMED 与 2 个 CANDIDATE 问题。
- Alembic 单一 head：`e6b5c3074f89`。

运行 3B 前需要执行：

```bash
uv run alembic upgrade head
```

## 下一步

闭环 3 已完成。下一步按 MVP 计划进入 Agent 或端到端浏览器验收，并继续扩展其他真实指标；不得以未实现指标填充 Registry。

