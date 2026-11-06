# 0204 政策结构真实数据画像

> 范围：Phase B。结构化完整结果见
> [`0204-policy-data-profile.json`](0204-policy-data-profile.json)。脚本为
> `scripts/audit_policy_structure.py`。统计是候选特征画像，不是解析准确率评测。

## 1. 可复现输入

- Dataset Version：`01a1a9a4-c688-7a18-a70b-927c73ab24e0`
- 原始文件：`data/政务数据集/policy.policy.jsonl`
- 记录数：321,902；无效 JSON：0
- 输入 SHA-256：`46758c72d0541a829199efd08a9574f1cdf02de84499941c417893b770388f5c`
- 该 SHA-256、记录数与 Dataset Manifest 中 RAW_DATA 完全一致。
- 抽样：每层蓄水池 30 条，seed `20261106`；层之间允许重叠，最终 270 个唯一样本。
- 输出不包含正文，只含 sample ID、原始行号、类型、层和长度等聚合特征。

复现命令：

```bash
UV_CACHE_DIR=/tmp/qa-eval-uv-cache uv run python scripts/audit_policy_structure.py \
  data/政务数据集/policy.policy.jsonl \
  --output docs/development/0204-policy-data-profile.json \
  --sample-size 30 --seed 20261106
```

## 2. 已由真实数据验证的结论

### 2.1 文种候选分布

分类依据为 `policyType`、`type`、`title` 的合并字符串，因此是可复现代理分类，不是法律文种
Ground Truth。

| 候选文种 | 数量 | 占比 |
|---|---:|---:|
| 通知 | 206,869 | 64.26% |
| 办法 | 12,255 | 3.81% |
| 条例 | 1,620 | 0.50% |
| 规划/纲要 | 3,506 | 1.09% |
| 其他 | 97,652 | 30.34% |

这与任务给出的 60.50%/1.31%/0.31%/约 0.17% 不同。差异不是数据错误结论，而是统计口径
不同：本审计把标题命中也纳入分类。生产模式选择不能依赖该代理分布，更不能硬编码数量。

### 2.2 全量候选层

| 分层条件 | 数量 | 占比 |
|---|---:|---:|
| 无行首法条/大纲编号候选 | 123,308 | 38.31% |
| 通知中含法条候选 | 15,560 | 4.83%（通知内 7.52%） |
| 宽松 HTML 嵌套异常候选 | 4,724 | 1.47% |
| 附件/图片/链接为主候选 | 19,618 | 6.09% |

“无编号”不能判为缺陷；它支持 `GENERIC_DOCUMENT` 必须是一等模式。通知中存在大量法条候选，
说明不能按文种固定模式，至少需要内容特征覆盖文种先验。

### 2.3 270 条分层样本结构

| 模式候选 | 数量 | 占样本 |
|---|---:|---:|
| GENERIC_DOCUMENT | 129 | 47.78% |
| OUTLINE_HIERARCHY | 80 | 29.63% |
| MIXED | 58 | 21.48% |
| LEGAL_HIERARCHY | 3 | 1.11% |

这不是总体无偏覆盖率：样本是多层合并，刻意过采样异常和特殊场景。它验证了三种模式均有
现实输入，且 MIXED 不能忽略；不能把这些比例当全量分布。

编号候选命中文档数：中文大纲 91、中文括号项 84、法条 60、阿拉伯大纲 47、中文章 44、
节 3、款 1、阿拉伯括号项 2。一个样本可命中多个模式。

### 2.4 HTML 表示层

270 个样本中 249 个含 HTML 标签。主要标签计数：`span` 10,336、`p` 9,783、`td` 3,630、
`br` 1,905、`tr` 787、`font` 758、`div` 152、`a` 132、`img` 81、`table` 68。
实体引用共 9,977；59 个样本嵌套深度至少 5；31 个出现宽松栈诊断异常。

结论：段落不能只认 `p`；`div/br/td/tr` 都参与 block 边界。`span/font/o:p` 和 Office/VML
标签常见，编号可能跨标签。必须从原始 HTML 建 source-map 后再归一化，不能对 plain 文本反推
HTML 坐标。

## 3. 尚待验证的假设

- 正则命中不是 parser precision/recall，也没有证明条款切分正确率。
- HTML “异常”是标准库容错 parser 的保守栈诊断，不等同 HTML5 conformance。
- 本阶段没有人工结构标注，不能报告 `segmentation_correct` PASS/FAIL 或准确率。
- “仅附件”使用可见文字少于 50 字的启发式；PDF/图片内容未下载、未 OCR。
- 规划中的批复型/纲要型、办法“无章有条”等细分类需要人工复核抽样 ID；ID 已写入 JSON 产物。

## 4. 画像对设计的约束

1. 先做内容级 mode scoring，再用文种作弱先验；允许 MIXED。
2. 无显式编号走 GENERIC，不进入编号连续性分母。
3. 附件为主样本在没有版本化解析 artifact 时，正文解析为 UNAVAILABLE，不动态联网。
4. HTML/source offset 必须基于 raw HTML lexical spans；normalizer plain 只能作降级语义文本。
5. 上线前必须建立人工校准集，分文种和异常层评估候选识别、边界、层级和坐标。

