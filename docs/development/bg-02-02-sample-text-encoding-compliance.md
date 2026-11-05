# BG-02-02 SampleRecord 文本编码合规检测

`sample_text_encoding_compliance@1.0.0` 是指标 `0201` 的独立 Calculator，扫描 SampleRecord 2.0 中 `PRESENT` + `text` + `inline_text` 的 ContentItem。它不执行 JSONL/Schema/sample_id 唯一性检查，这些仍属于 `format_compliance`。

## 输入、输出与调用

输入是平台 `NORMALIZED_DATA` Artifact，契约为 SampleRecord 2.0。可通过 `include_roles` / `exclude_roles` / `include_content_ids` 限制范围，并用四个 `check_*` 布尔参数分别开关替换字符、Unicode 合法性、乱码签名和控制字符检查。`max_evidence_per_record` 默认为 20，只限制证据行，不截断聚合计数。

通过 Quality Contract 选择：

```json
{
  "metric_key": "0201",
  "metric_version": "1.0.0",
  "measurement": "compliant_record_ratio",
  "calculator_key": "sample_text_encoding_compliance",
  "calculator_version": "1.0.0",
  "parameters": {"include_roles": ["article.title", "article.body.plain"]}
}
```

主观测值为 `compliant_record_ratio = 1 - affected_record_count / eligible_record_count`。无适用文本时返回 `NOT_APPLICABLE`，不伪造 100% 合规率。证据 Artifact 为 `sample-text-encoding-violations.jsonl`。

## 规则与证据

- `INVALID_UNICODE_SURROGATE`：孤立 UTF-16 surrogate，确定性错误。
- `INVALID_UNICODE_CODE_POINT`：Unicode noncharacter，确定性错误。
- `REPLACEMENT_CHARACTER`：U+FFFD，仅表述为“疑似上游解码异常”。
- `SUSPICIOUS_MOJIBAKE`：复用 `text_purity_rules` 的 Latin-1 / `锟斤拷` 等签名，属疑似异常。
- `SUSPICIOUS_CONTROL_CHARACTER`：非 tab/LF/CR 的 Cc 控制字符。

offset 是从 0 开始的 Python Unicode 字符索引，左闭右开。HTML 的 Unicode/替换字符/控制字符检查使用原始 HTML 坐标；乱码签名只扫描可见文本，其 `coordinate_space=visible_text`，不伪造原始 HTML offset。

## 统计口径

- Record：至少有一个适用 ContentItem 才进入分母；一条 sample 有多个异常内容项也只计一条 affected record。
- ContentItem：每个适用内容项独立统计。
- Violation：每个规则命中独立计数。HTML/plain 派生项的证据均保留，`source_paths` 用于追溯；因坐标不可直接比较，不做事件级强制合并。

## 关系、性能和覆盖边界

`format_compliance` 继续负责归一化结构合规；`text_purity` 继续按 02080106 的显式干扰规则和原有阈值聚合。两者可引用同一底层乱码证据，但 0201 表示文本编码/表示规范性，0208 表示内容干净性。

输入与证据均逐行处理，内存不随记录总数线性增长。每个文本全长扫描，不静默跳过长文。本算子不覆盖原始上传文件的 UTF-8 字节合法性、原始非法字节位置、PDF/DOCX/OFD 物理编码、OCR 准确性或语义错误。

## 测试

单元测试覆盖正常中英文、Emoji、HTML/CSS/URL/Entity，四类异常、offset、筛选、跳过/NA、record/content 去重和证据上限。Registry Loader 和 EvaluationRun 集成链验证注册、执行、Metric Result 及 Artifact 持久化。
