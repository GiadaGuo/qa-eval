# ADR-0001：三个评测 Skill 的指标所有权与共享评测层

- 状态：Accepted
- 日期：2026-10-27
- 决策范围：qa-eval 平台及其数据集评测 Skills

## 背景

高质量数据集指南包含三个一级指标维度：说明文档、数据质量和模型应用。三类指标的输入证据、计算方式和演进节奏不同，适合由独立 Skill 开发和验证；但指标注册、契约、执行轨迹、规则判定、问题管理和报告需要由 qa-eval 平台统一支持。

现有 `dataset-quality-demo` 同时描述了三个维度的指标设计，以及 Quality Contract、Rule Evaluation、Quality Issue 和 Quality Report。这会造成两个问题：

1. 单个 Skill 容易成为事实上的平台边界，产生与 qa-eval 不兼容的私有协议。
2. 三个指标域可能分别实现契约和报告，导致状态语义、运行标识和持久化格式分叉。

## 决策

### 1. 指标所有权

| 指标维度 | 指标编码 | Skill | 所有权范围 |
|---|---|---|---|
| 说明文档 | 0101–0104 | `dataset-document-evaluator` | 文档证据检查、条目判定、指标值及证据生成 |
| 数据质量 | 0201–0208 及其模态子指标 | `dataset-data-quality-evaluator` | 数据质量 Metric Definition、Calculator 原型、计算结果及证据生成 |
| 模型应用 | 0301–0305 | 计划中的 `dataset-model-application-evaluator` | 下游任务配置解析、模型应用评测、指标值及证据生成 |

父指标和子指标均随所属维度归属。跨维度汇总不是任何单个 Skill 的职责。

### 2. Skill 与平台的关系

三个 Skill 是 qa-eval 指标能力的开发、验证和 Agent 编排入口；qa-eval 是其稳定的平台实现和唯一 canonical contract 的所有者。

Skill 可以：

- 梳理和验证本维度的 Metric Definition。
- 开发、校准和测试 Calculator 或其他确定性评测实现。
- 在平台服务尚未完成时，用本地文件执行最小闭环验证。
- 生成符合 qa-eval canonical schema 的 Metric Result 和计算证据。
- 提出平台协议优化建议并通过版本化变更落地。

Skill 不可以：

- 建立一套与 qa-eval 并行的 Dataset、Metric Result、Contract 或 Report canonical schema。
- 直接把实验性文件格式视为平台正式协议。
- 绕过平台领域模型直接依赖其他模块的 ORM 模型。
- 将计算失败解释为质量不合格。

如现有 qa-eval 模型不足，应修改平台领域模型、JSON Schema、迁移和兼容策略；不得只在某个 Skill 内增加长期私有字段。Skill 本地产物可以是平台对象的文件序列化形式，但标识、版本、状态和语义必须一致。

### 3. 共享层的位置

以下能力属于 qa-eval 共享平台层，不归属于任何一个指标 Skill：

- Metric Registry 与 Calculator Registry
- Dataset、DatasetVersion、DatasetManifest、SampleRecord 和 DatasetProfile
- Quality Contract 与 Contract Version
- Logical Plan、Physical Plan、Evaluation Run 和 Stage Run
- Rule Evaluation
- Quality Issue 生命周期和去重
- 跨维度 Quality Report 与综合展示
- 运行日志、审计、重试、缓存、持久化和 API/UI

共享层在逻辑上的流转为：

```text
DatasetVersion + DatasetProfile + QualityContractVersion
                         |
                         v
            LogicalPlan -> PhysicalPlan
                         |
                         v
              EvaluationRun / StageRun
                         |
       +-----------------+------------------+
       |                 |                  |
       v                 v                  v
 Document results   Data-quality results   Model-application results
       +-----------------+------------------+
                         |
                         v
                    MetricResult
                         |
                         v
                  RuleEvaluation
                         |
                         v
              QualityIssue / QualityReport
```

各 Skill 可以为本地验证读取 Quality Contract，或渲染局部开发视图，但不得拥有共享 Contract/Report 的正式模型和生命周期。正式 Quality Report 必须由平台基于持久化的 Metric Result、Rule Evaluation 和 Quality Issue 生成，不得重新计算指标或规则。

### 4. Canonical schema 与版本规则

1. qa-eval 的 domain model 定义业务语义和不变量。
2. qa-eval 维护的 JSON Schema 定义 Skill、API、Registry 和持久化边界之间的交换协议。
3. Skill 中的 YAML/JSON 是定义实例、Calculator descriptor、fixture 或运行产物，必须能通过对应平台 schema 校验。
4. 所有运行产物至少通过 `dataset_version_id`、`run_id`、`metric_key`/`metric_version` 和 Calculator 版本建立追踪关系。
5. 不兼容变更必须升级 `schema_version`，已发布的数据集版本、契约版本和历史运行产物不得原地改写。
6. 高基数样本明细和证据存入文件或对象存储；平台数据库保存聚合结果、失败结果、索引和 URI。具体持久化介质不得改变领域语义。

### 5. 当前迁移约束

- `dataset-quality-demo` 重命名为 `dataset-data-quality-evaluator`，其范围收缩到 020x。
- 当前 `02080103` 的 YAML、脚本和结果是 Calculator 原型及历史运行证据，不自动成为平台正式协议。
- `data/dataset-quality-demo/` 保留为历史产物位置；不通过目录重命名伪造历史运行路径。新默认输出可以迁移到新名称，并需保持 manifest/URI 可追溯。
- `dataset-document-evaluator` 当前私有输出格式后续也必须映射或迁移到平台 Metric Result，不因它已独立存在而获得例外。
- 模型应用 Skill 建立时必须直接采用本 ADR，不再复制 Contract、Run 或 Report 模型。

## 后果

正面影响：

- 三个指标域可以独立演进，同时共享统一运行轨迹和交付协议。
- Skill 中验证成熟的 Calculator 可以注册到平台，而无需重写业务语义。
- Contract 阈值变化不会污染客观 Metric Result，跨维度报告也不会依赖某个 Skill。

代价：

- 在继续扩展指标前，需要先收敛 qa-eval 现有文档、Python 模型与 Skill 产物之间的协议差异。
- 本地 Demo 在平台 Planner/Runner 完成前需要轻量 execution metadata，但这些字段必须向平台模型兼容。

## 后续工作

1. 盘点 DatasetManifest、SampleRecord、Metric Definition 和 Metric Result 的现有多种表示。
2. 确定 qa-eval canonical models v1，并建立 JSON Schema 与 Pydantic 的一致性测试。模型边界与语义已由 [ADR-0002](0002-adopt-canonical-data-flow-models-v1.md) 冻结；Schema 与实现测试仍待完成。
3. 将 `02080103` 改造成第一条遵守 canonical contract 的参考实现。
4. 再实现 Quality Contract、Plan、Run、Rule Evaluation、Issue 和 Report 的平台闭环。

