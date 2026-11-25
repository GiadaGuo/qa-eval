# M4 · Checkpoint

基线为 M3 的 36 条指标版本 × 算子版本绑定。A 批先复用已验收的正文完整度，对照 0208 聚合指标中的对应槽；纯度共享旧规则和统计核心，v3 直接读取 Resolver 文本。纯度保留阈值、分母、排除规则和表示内坐标；源定位只在坐标可验证时验收。每项上线条件以矩阵为准。

| 批次 | 状态 | 恢复位置 |
|---|---|---|
| A | PARTIAL | `text_purity@1.1.0` 的 02080106、0208 绑定，以及 `text_completeness@1.1.0` 的 0208 绑定已登记并小样本执行。补齐预检、缺值矩阵、Issue 来源回查，再迁移 HTML 格式及实体检测。 |
| B | PARTIAL | 沿 M3 InputSelector 为各算子冻结实际字段。需适配旧选择器与证据路径；业务码表、日期及附件关系保持未确认。 |
| C | PARTIAL | 实现尚待迁移。先验证现有重复聚合的输入步骤和有序样本身份，随后处理内容一致性的候选及模型依赖。 |
| D | PARTIAL | 实现尚待迁移。先接独立工件和 fixture，再适配共享推理的生产及消费阶段。真实 FINAL 标注、字典及模型就绪性独立记录。 |
| E | PARTIAL | 已保持旧格式算子、占位及禁用绑定状态。独立 v3 格式度量仍缺 P1 要求的完整摄取行账本和拒绝数来源。 |

A 批定向 28 通过。旧算子源文件及冻结身份保留原样。后续先在隔离库同步 Registry 并继续定向验证；全迁移门禁通过后再做全仓回归。

## 续跑 checkpoint

- A · 正文完整度和纯度 4 条 v3 绑定升级为 EXECUTION_PASS。续跑夹具包含相同正文及 NULL，旧 v2 结果对照通过。HTML 格式与实体检测尚未迁移。
- B · 尚待开发。先从结构完整性配置槽切入，保持 v2 冻结实现。
- C · 新增精确重复 `exact_text_duplicate@1.1.0`，1 条绑定 EXECUTION_PASS。其余重复与内容一致性仍待迁移。
- D · 尚待开发。外部依赖状态沿用前述记录。
- E · 独立 v3 格式度量缺完整摄取账本和拒绝数来源，继续保持旧格式专用、占位与禁用状态。
- 隔离 S0 同步 Registry 后，5 条 v3 绑定全部通过 Loader 和生产共用输入预检，默认修订均解析到 content。续跑定向 12 通过。恢复时从未迁移项及第二轮证据回查继续。

## 批量实施 checkpoint

M4.1 部分完成。现有 Worker 对本地 v3 原件直接使用 ArtifactStore 的本地路径，仍由 Reader 验证摘要。既有 FrozenInputPlan 可携带登记的 DATASET_MANIFEST，预检要求工件 URI 和摘要，Worker 按冻结引用校验后交给算子。fixture 验证本地原件未走复制、工件替换阻断执行。定向 5 通过。

恢复点为 M4.1 的共享推理生产阶段、统一诊断及原始、派生和组证据回查。其他独立工件类型尚待接入。M4.2、M4.3、M4.4 剩余版本仍以效率诊断附表的 12、5、6 个为准。M4.5 的 5 条既有执行绑定仍待完整验收；独立 v3 格式度量仍等摄取账本。

## M4-A · 核心政策算子

5 条 `1.1.0` v3 绑定均为 EXECUTION_PASS，原有 `1.0.0` 源码及 Registry 身份保留。验收走隔离库 Registry 同步、Loader、正式发布契约、FrozenInputPlan 预检和 Worker。每个算子执行两组 v2/v3 参数对照，共 10 对 Run。对照包括原生统计或指标观测、分母及排除、证据载荷和 v3 原件字段回查。证据回查覆盖工件和记录来源，Issue 高亮接口未纳入本批验收。

| 指标 | v3 算子 | 实际状态 | 依赖与验收边界 |
|---|---|---|---|
| 0201@1.0.0 | `policy_document_format@1.1.0` | EXECUTION_PASS | 合法夹具提供附件 URL 及保真/标准适用范围验证属性。真实附件关系、文种和验证标注待确认。 |
| 0202@2.0.0 | `sensitive_dictionary_match@1.1.0` | EXECUTION_PASS | 对照内联词典、Worker 本地 JSON 词典及内容 ID 筛选。生产词典及路径待确认。 |
| 0202@2.0.0 | `structured_sensitive_element_regex@1.1.0` | EXECUTION_PASS | 对照手机、邮件类别和 HTML 角色筛选。真实字段选择待确认。 |
| 0204@2.0.0 | `policy_structure_compliance@1.1.0` | EXECUTION_PASS | 合法 FINAL 标注参与分段分母，并对照证据行数上限。真实标注及目标映射待确认。 |
| 0205@1.0.0 | `policy_effectiveness@1.1.0` | EXECUTION_PASS | 对照带 SHA-256 的 Manifest 捕获日与显式观察日。真实文种、日期语义和发布 Manifest 待确认。 |

v3 由契约显式选择输入，`title`、`html`、`plain` 为对应的内容 ID。正文与标注关联、文种、日期和附件含义由契约提供。定向检查 79 通过，1 条既有真实发布样本用例因缺本地 SampleRecords 和 Manifest 跳过。五条迁移绑定均无源码级执行阻塞。

## M4-B · 核心一致性及重复

| 指标 | v3 算子 | 实际状态 | 生产待验收 |
|---|---|---|---|
| 0206@2.0.0 | `content_consistency@2.1.0` | EXECUTION_PASS | 规则及内置 concept embedding 通过。真实 BGE 模型环境待验收。 |
| 0207@2.0.0 | `field_relation_consistency@2.1.0` | EXECUTION_PASS | 动态字段别名、嵌套数组路径、空值、原样及大小写规范化通过。生产字段映射、字典、TF-IDF 资源与业务阈值待确认。 |
| 0208@2.0.0 | `cross_record_duplicate@1.1.0` | EXECUTION_PASS | 整行精确、字段精确和字段近似通过，分组及首条代表样本对照一致。真实 policy 快照及字段配置待验证。 |
| 0208@2.0.0 | `intra_record_repetition@1.1.0` | EXECUTION_PASS | 句段和局部 n-gram 参数对照通过，字符加权和证据区间一致。真实 policy 快照及字段配置待验证。 |

0206 使用 2.0.0 单篇正文绑定作为基线。0207 的选择器按字段别名登记，0208 两个算子按 `fields` 下标登记。Quality Contract 将原始路径或已确认语义冻结到 FrozenInputPlan。跨记录算子沿用 SQL 索引和输入顺序，v3 样本 UUID 和代表样本可回查。测试阻断了本地原件复制路径。

四个算子各执行两组 v2/v3 参数，共 8 对隔离正式 Run。定向检查 101 通过，1 条依赖本地 policy 快照的既有用例跳过。四条新绑定均无源码级执行阻塞。
