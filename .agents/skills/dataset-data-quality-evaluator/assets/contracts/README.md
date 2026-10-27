# 02080103 MVP Quality Contract request

`02080103-mvp-baseline.request.json` 是供 Skill 或未来平台 API 接收的用户入参，
不是 canonical `QualityContractVersion` 本身。Publisher 会补齐 UUIDv7、PUBLISHED
状态、发布时间和 semantic fingerprint，再输出可交给执行器的不可变 Contract version。

当前建议基线使用 `exact_redundant_record_ratio <= 0.01`：即每个完全重复组保留
一个代表样本后，冗余记录不超过有效文本的 1%。这是面向当前通用政策文章语料的
MVP 建议值，不是指南强制值，也不应成为所有数据集的系统默认值。用户应根据数据集
用途、去重成本和容忍度修改 request 中的 threshold。

发布：

```bash
uv run python .agents/skills/dataset-data-quality-evaluator/scripts/publish_02080103_contract.py \
  .agents/skills/dataset-data-quality-evaluator/assets/contracts/02080103-mvp-baseline.request.json \
  /path/to/quality-contract.json
```

发布新版本时，在 request 中传入已有 `contract_id` 并提升 `version_label`；Publisher
始终生成新的 `contract_version_id`。未来 Web/API 应提交相同 request 结构，不能允许
用户直接指定 fingerprint、PUBLISHED 状态或发布时间。
