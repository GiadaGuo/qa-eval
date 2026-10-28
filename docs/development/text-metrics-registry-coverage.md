# 文本指标清单 Registry 覆盖情况

> 依据：`plan/Idea-Pool/指标开发/文本指标清单.md`
> 更新日期：2026-10-28

## 一、总览

| 项目 | 数量 |
| --- | --- |
| 清单指标总数 | 15 |
| 原有已注册指标 | 2（0204 结构完整性、02080103 重复内容程度） |
| 本次新增指标 | 13 |
| Registry 指标总数 | 15 |
| 已实现 Calculator（可执行，`availability.status=AVAILABLE`） | 3（原有，未改动） |
| 本次新增占位 Calculator（`UNAVAILABLE`，待实现） | 13 |
| Registry Calculator 总数 | 16 |
| Metric↔Calculator Binding | 16（3 原有 + 13 新增，每个指标至少 1 条） |

清单中无「权重」列，Metric Definition 数据模型亦无权重字段，故无需迁移权重数据；
「目标数据形式」由 `supported_scopes=DATASET`（数据记录级）表达。

## 二、指标与 Calculator 对应表

| 指标编号 | 名称 | Calculator key | 状态 |
| --- | --- | --- | --- |
| 0201 | 格式规范性 | `format_compliance` | 🟡 占位，待实现 |
| 0202 | 安全规范性 | `content_safety_compliance` | 🟡 占位，待实现 |
| 0203 | 标注规范性 | `annotation_compliance` | 🟡 占位，待实现 |
| 0204 | 结构完整性 | `structural_completeness` | 🟢 已实现（v1.0.0，未改动） |
| 0205 | 内容真实性 | `content_authenticity` | 🟡 占位，待实现 |
| 0206 | 内容一致性 | `content_consistency` | 🟡 占位，待实现 |
| 0207 | 类型一致性 | `dataset_type_consistency` | 🟡 占位，待实现 |
| 0208 | 内容干净性 | `content_cleanliness` | 🟡 占位，待实现（父指标，建议由细分指标聚合实现） |
| 02080101 | 文本困惑程度（PPL） | `text_perplexity` | 🟡 占位，待实现（KenLM） |
| 02080102 | 知识信息密度 | `knowledge_information_density` | 🟡 占位，待实现 |
| 02080103 | 重复内容程度 | `text_duplicate_degree` v2.0.0、`exact_text_duplicate` v1.0.0 | 🟢 已实现（未改动） |
| 02080104 | 文本完整程度 | `text_completeness` | 🟡 占位，待实现 |
| 02080105 | 信息缺失程度 | `information_missing_degree` | 🟡 占位，待实现 |
| 02080106 | 文本纯净程度 | `text_purity` | 🟡 占位，待实现 |
| 02080107 | 文本连贯程度 | `text_coherence` | 🟡 占位，待实现 |

**清单覆盖率 100%**：15/15 指标全部注册并绑定 Calculator 信息；未覆盖指标：无。

## 三、占位 Calculator 的表达方式

- `availability.status = "UNAVAILABLE"`，`reason` 注明「待实现：占位注册……」；
- `entrypoint = null`、`code_digest = null`（不伪造执行入口与摘要）；
- `semantic_coverage = { "level": "NONE", "covered": [], "planned": [...] }`，
  `planned` 数组记录清单「计算方法」列的规划方案，作为后续实现依据；
- `parameter_schema` 为空对象（不接受任何执行参数）；
- Metric Definition 的 `definition.limitations` 保留了清单中的测量方法（X = A / B）
  与规划计算方法，并显式标注「Calculator 为占位注册，算法待实现，当前不可执行」。

## 四、执行安全（不可执行保证）

1. **Loader 阻断**：`TrustedCalculatorLoader.load()` 对非 `AVAILABLE` 的 Descriptor
   直接抛出 `CalculatorLoadError`（"Calculator is not available for execution"），
   占位 Calculator 无法被 Evaluation Run 加载执行。
2. **契约阻断**：`ApplicabilityService.evaluate()` 对非 `AVAILABLE` Calculator 返回
   `UNAVAILABLE`（finding `CALCULATOR_UNAVAILABLE`），`QualityContractService.validate_item`
   据此拒绝发布，占位 Calculator 无法进入可执行的 Quality Contract。
3. **契约最小兼容扩展**：`CalculatorDescriptorV1.entrypoint`/`code_digest` 允许为 `null`，
   但新增校验器强制 `AVAILABLE` 状态必须声明二者，防止可执行 Calculator 丢失运行时身份。
4. **Readiness 语义**：`/api/v1/registry/readiness` 现区分「声明为不可用的占位」与
   「可执行 Calculator 加载失败」；总 `ready` 仅由 `AVAILABLE` Calculator 决定，
   占位不再误报为部署故障。
5. **Registry GET 保持只读**；同步仅经 `POST /api/v1/registry/sync`，按
   （key, version）upsert，幂等、不覆盖已发布版本语义。

## 五、前端展示

完全复用现有「指标管理」页面（`apps/web/src/pages/MetricRegistryPage.tsx`）的
指标卡片、搜索、详情弹窗与 Calculator 卡片，无新增组件、无硬编码指标数据。
仅做 availability 感知的最小修正：

- 卡片「N 个可用 Calculator」只统计 `availability.status === 'AVAILABLE'` 的绑定；
- 详情弹窗中占位 Calculator 标记为「待实现」Tag，并显示 `availability.reason`；
- Entrypoint 为空时显示「—（待实现，暂无执行入口）」；
- 「构建质量契约」按钮对不可执行 Calculator 保持禁用。

## 六、后续逐个实现的步骤

1. 实现 `src/qa_eval/calculators/<name>.py`，满足 `Calculator` Protocol；
2. Descriptor 落地 `entrypoint` 与真实 `code_digest`，`availability.status` 改为
   `AVAILABLE`，补充 `semantic_coverage`；
3. 按需扩充 `parameter_schema` 并同步 Binding 兼容性说明；
4. 更新本文件的对应状态行。
