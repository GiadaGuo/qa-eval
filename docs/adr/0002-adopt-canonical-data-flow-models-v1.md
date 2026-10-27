# ADR-0002：采用 qa-eval Canonical Data-flow Models v1

- 状态：Accepted
- 日期：2026-10-27
- 决策范围：qa-eval 平台领域协议、三个评测 Skill 及其运行产物
- 详细规范：[Canonical Data-flow Models v1](../architecture/canonical-data-flow-models-v1.md)
- 前置决策：[ADR-0001](0001-skill-metric-ownership-and-shared-evaluation-layers.md)

## 背景

RD、现有 Python domain 与 `dataset-data-quality-evaluator` 的真实产物存在多套不兼容表示：Sample 内容分别使用 `content.text`、`data_content[]` 和顶层 `text`；Manifest、字段映射、NULL 语义、Metric Result、运行轨迹和证据也没有统一协议。这会阻止 Calculator 注册、结果复用、问题下钻和跨 Skill 报告。

平台当前需要支持 `policy.jhsjk_article + 02080103` 的真实评测，同时为多模态和带标注 SFT 数据保留明确扩展点。继续横向开发指标前，必须冻结一个共同语义基线。

## 决策

采用 `docs/architecture/canonical-data-flow-models-v1.md` 作为 qa-eval canonical data-flow models 的冻结设计基线。

核心决策如下：

1. qa-eval 平台拥有 canonical domain 与交换协议；Skill 只生产或消费协议实例。
2. SampleRecord 2.0 使用 `contents[]`，内容载荷为可判别的 inline text、asset 或 structured payload。
3. 内容显式区分 `PRESENT/NULL/MISSING`；空字符串不等于 NULL，标准化 reject 是独立事实。
4. FieldMappingSpec 2.0 使用显式 `MAP/PASSTHROUGH/IGNORE`；前端“未配置”不得出现在发布版本中。
5. 标注使用 typed `Annotation[]`，支持多任务、多标注者、偏好、序列和多模态/SFT 目标。
6. 原始、标准化、reject、证据、IssueIndex 和报告均通过带 schema 与 checksum 的 ArtifactRef 管理。
7. Metric Definition 与 Calculator Descriptor 分离；多输出指标声明命名 measurements，Contract 必须引用具体 measurement。
8. Logical Plan 固化“算什么”，Physical Plan 固化“怎么执行”；PhysicalStage 是调度单位，StageRun 引用 `stage_id`。
9. MetricResult、RuleEvaluation、QualityIssue 和 QualityReport 分别表达计算事实、规则判定、可定位问题和不可变汇总，不相互覆盖或重算。
10. Rule PASS 时仍允许存在样本级 Issue；确定不符合与待复核候选分别使用 `CONFIRMED/CANDIDATE`。
11. 高基数样本、样本对、分组特征和 Issue 明细进入文件/对象存储，数据库保存控制面、聚合事实和可查询索引。
12. 运行状态、结果状态、语义覆盖、规则状态、Issue 生命周期和报告 gate 状态使用独立枚举。

## 冻结含义

冻结的是对象边界、字段语义、状态分层、追踪关系和版本规则，不是某个 ORM、队列或对象存储实现。

- 后续 JSON Schema 和 Pydantic 模型必须忠实实现该基线。
- 字段细化、可选字段增加和实现优化不得破坏既有语义。
- 不兼容修改必须新增 ADR，并提升对应 `schema_version` 的 major version。
- RD 中与本 ADR 冲突的早期示例视为历史草案，应在后续文档更新中指向本规范。
- 当前 Demo YAML/JSON 和历史 `data/dataset-quality-demo/` 产物保留原状，通过显式迁移器接入，不原地伪装成 canonical v1/v2 产物。

## 验收依据

冻结基线已经覆盖并区分：

- `jhsjk_article` 的 HTML、纯文本、标题、摘要、来源、时间和 NULL 语义。
- 02080103 的 exact、near、paragraph、n-gram 多 observation 与证据。
- 45 条确定冗余样本、7,517 条近重复候选和 32 条排除记录的不同下钻语义。
- 图文等多模态内容、内容级定位和资产校验。
- 多轮 SFT、混合模态 turn、typed annotation 和偏好数据。
- Contract、Plan、Run、Stage、Result、Rule、Issue、Report 的完整引用链。

## 后果

正面影响：

- 指标和 Skill 可以共享同一平台运行、证据与报告协议。
- 02080103 可作为第一条 canonical 参考实现，后续指标不再发明私有输入输出格式。
- 问题可以下钻到 dataset/group/sample/pair/content/annotation，并支持分页和审计。

代价与迁移：

- 现有 DatasetManifest、SampleRecord、FieldMappingConfig、Adapter 和 ORM 需要迁移。
- 需要建立 `schemas/`、Pydantic wire contracts、显式 1.x→2.0 迁移器与 schema 一致性测试。
- Registry、Planner、Runner、Rule、Issue 和 Report 尚需按冻结协议实现；冻结不表示这些服务已经完成。

## 后续工作

1. 创建 ArtifactRef、DatasetManifest 2.0、FieldMappingSpec 2.0、SampleRecord 2.0、RejectedRecord 和 MetricResult 1.0 JSON Schema。
2. 分离 domain entity、wire contract 与 ORM，并建立 schema/Pydantic 一致性测试。
3. 修复 MySQL Adapter 的 NULL 保真，生成完整标准化 `jhsjk_article`。
4. 拆分 02080103 Metric Definition 与 Calculator Descriptor，生成 canonical Result、Evidence 和 Issue。
5. 按冻结模型实现 Contract、Plan、Run、Rule、Issue 和 Report 的持久化与 API。

