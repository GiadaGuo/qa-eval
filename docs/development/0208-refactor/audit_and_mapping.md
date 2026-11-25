# 0208 审计与迁移映射

| 原编码 | 0208 槽位 | 迁移口径与缺口 |
| --- | --- | --- |
| 02080101 | text_perplexity | 复用本地 KenLM，需外部本地模型。PPL 仅观测，缺少正式分值转换。 |
| 02080102 | knowledge_information_density | 保留有效知识密度映射。原实现依赖共享 LLM，新版槽位暂不可用。 |
| 02080103 | cross_record_duplicate、intra_record_repetition | 跨样本精确和近似比对、单记录重复区间。 |
| 02080104 | text_completeness | 复用缺失、最小长度与截断后缀规则。语义完整性仍待覆盖。 |
| 02080105 | information_missing_degree | 保留关键要素缺失映射。原实现依赖共享 LLM，新版槽位暂不可用。 |
| 02080106 | text_purity | 复用显式广告、页码、符号、乱码及标记残留规则。隐性干扰仍待覆盖。 |
| 02080107 | text_coherence | 保留主题一致及句段衔接映射。原实现依赖共享 LLM，新版槽位暂不可用。 |

旧 0208 是聚合入口。02080103 的 exact_text_duplicate 对单正文角色做规范化与 SHA-256，按首次样本选代表，输出精确受影响率、冗余率及组证据，整文件入内存。text_duplicate_degree 同样计算正文精确重复，另输出字符及词 n-gram 重复、段落受影响范围、MinHash+LSH 近重复候选与证据。旧近重复以签名估计相似度并取连通分量，有传递合并风险。新版复用规范化、SHA-256、MinHash 与 0207 字段适配器。

Binding 保存候选实现与优先级，推荐流程有优先级信息，发布与 Planner 使用 Contract item 固定实现。多 item 支持并行存在。已有 output_slot_id 对应 Contract item，缺少跨实现稳定的业务槽位。Report 支持按 Metric 与 Calculator 分段。迁移采用独立主 Measurement 标识每个槽位的 Contract item，显式实现固定版本，保留旧结果路径。

旧父聚合消费子指标结果，MVP 运营评分配置另有原子指标映射及等权示例。新版使用单一主测量形成算子分，组合分等待正式权重。记录内执行粒度与数据集聚合、跨记录组和单样本位置在运行和证据中独立表达。
