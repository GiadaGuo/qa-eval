# Document template and writing rules

Use this organization unless the user explicitly requests another Markdown structure. Structure does not imply that every section is complete.

```text
# <dataset_id> 数据集说明

> 文档状态、数据版本、生成时间、证据范围

## 1. 数据集概述
### 1.1 基本信息
### 1.2 文件与字段结构
### 1.3 访问渠道与技术支持

## 2. 内容特征
### 2.1 模态与内容形态
### 2.2 数据分布
### 2.3 标签类别统计
### 2.4 样本示例
### 2.5 局限性

## 3. 建设过程
### 3.1 数据来源与采集方法
### 3.2 加工处理流程
### 3.3 标注规范
### 3.4 版本控制记录

## 4. 应用说明
### 4.1 使用许可
### 4.2 目标应用场景
### 4.3 评估方法
### 4.4 基准测试结果
### 4.5 典型应用案例

## 5. 待确认事项与维护
```

## Section rules

- Basic information: use manifest/profile facts for scale, format, files, schema, and hashes. Access channels and technical support require explicit project or human facts.
- Content characteristics: describe modality only to the level supported by configured fields. Use profile distributions. An unlabeled statement requires a deterministic full-record key scan or authoritative schema evidence.
- Samples: use profile samples, preserve structural fields, truncate long values, and state that content is truncated. Do not select records merely because they contain sensitive or sensational text.
- Construction process: use manifest metadata for source, mapping, ordering, consistency, and export time. Do not infer upstream collection history that the manifest does not contain.
- Annotation: distinguish “the snapshot has no label field” from “no annotation process ever existed.” The former may be derived; the latter requires human evidence.
- Version record: document the current snapshot version and changes evidenced by the manifest. Do not invent earlier versions or approvers.
- License and support: when unresolved, say the current inputs do not provide enough information and identify the responsible role needed. Never convert a placeholder into a policy.
- Application scenarios and cases: use only explicit human/project input. If none exists, mark unresolved. A proposed workflow must be labeled proposed, not completed.
- Evaluation methods: deterministic checks actually supported by the snapshot/profile may be documented. Do not label data-quality checks as model benchmark results.
- Benchmark results: include only completed, sourced task/model/metric/result records. Otherwise state that the supplied evidence contains no benchmark result and add an unresolved item.

## Draft placeholders

Use precise wording, for example:

- “当前输入未提供可执行的技术支持渠道，待项目维护负责人补充。”
- “当前输入未提供许可证或授权范围，待数据权利/合规负责人确认。”
- “当前输入未明确目标用户和业务任务，待数据负责人补充；不得仅依据数据内容推断用途。”
- “当前证据中未包含下游基准测试结果，不能据此判断模型性能。”

Avoid ambiguous claims such as “暂无许可,” “不需要支持,” “无风险,” or “适用于所有 AI 场景.”

## Traceability and tone

Keep the document readable. Put detailed provenance in `generation_manifest.json`, while the document states its evidence scope and links to companion artifacts. Use exact units and statistical definitions. Clearly distinguish current facts, proposed processes, and unresolved information.
