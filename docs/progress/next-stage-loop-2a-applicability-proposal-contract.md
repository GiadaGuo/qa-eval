# 下一阶段闭环 2A：Applicability、最小 Proposal 与多指标 Contract 验收记录

- 状态：已完成
- 验收日期：2026-10-28（Asia/Shanghai）
- 范围：确定性适用性、最小 Proposal contract/validation、多指标 Contract 原子发布、单指标兼容
- 非范围：规则推荐 Baseline、Proposal 持久化/UI、Agent、Logical Plan、依赖调度

## 1. 实现结果

### Applicability

新增 `ApplicabilityService`，基于 Dataset/Version、最新 Profile、Manifest/Artifact refs、Metric Definition、Calculator Descriptor、Binding 和显式参数输出：

- `APPLICABLE`
- `NOT_APPLICABLE`
- `NEEDS_INPUT`
- `UNAVAILABLE`

判定保留结构化 findings，不把缺少 role/artifact/selector 解释为质量 FAIL，也不把运行不可用解释为不适用。运行可用性复用受信任 Calculator Loader；文本 Dataset 对图像 Metric/Calculator 的模态冲突保持为 `NOT_APPLICABLE`。

### Evaluation Proposal

新增 canonical：

- `EvaluationProposalV1`
- `ApplicabilityDecisionV1`
- `EvaluationProposalValidationV1`

Proposal 保存 Dataset Version、Profile fingerprint、revision、创建者、候选/选中 Calculator、参数、规则、理由与限制。`POST /api/v1/evaluation-proposals/validate` 校验当前 Profile fingerprint、Registry 对象、Binding、measurement、参数 schema、阈值类型和适用性；未注册对象不能通过。Proposal 不生成 Contract ID、Run ID 或 Published 状态。

### 多指标 Quality Contract

canonical `QualityContractVersionV1.items` 原本已支持多 item，因此没有新增数据库表或 migration。新增公开的 `QualityContractMultiPublishRequest` 和：

`POST /api/v1/quality-contract-versions/multi`

服务在写入前校验全部 item；任一 item 无效时整份 Contract 不持久化。每个 item 独立保存 Metric/measurement、Calculator key/version、参数、规则、严重度与 missing policy。现有单指标 `POST /api/v1/quality-contract-versions` 保持兼容，并复用同一多 item 发布实现。

## 2. Canonical Schema

新增并纳入 drift 检查：

- `schemas/qa-eval/applicability-decision/1.0.schema.json`
- `schemas/qa-eval/evaluation-proposal/1.0.schema.json`
- `schemas/qa-eval/evaluation-proposal-validation/1.0.schema.json`

未创建 Skill 私有协议，Proposal/Applicability 均属于 qa-eval 共享平台层。

## 3. 自动化质量门

| 门禁 | 结果 |
| --- | --- |
| 完整 pytest（含本地 `qa_eval_test` PostgreSQL） | `202 passed, 6 skipped`；6 项均为需显式启用的 MySQL integration |
| Ruff | 通过 |
| canonical schema drift | 通过 |
| 前端 lint | 通过 |
| 前端 production build | 通过；仅保留既有 chunk size warning |

重点回归：

- 四种 Applicability 状态及结构化 reason。
- `0204` 缺 selector 返回 `NEEDS_INPUT`。
- 当前 Profile fingerprint 下 Proposal 通过；陈旧 fingerprint、未注册 Calculator 被拒绝。
- 两个 item 原子发布；第二个 item 无效时不产生部分 Contract。
- 单指标发布 API 回归不变。
- 双指标 Contract 可被现有执行内核消费，生成两个 StageRun、两个 MetricResult 和同一 QualityReport。

## 4. 真实 PostgreSQL / ArtifactStore 冒烟

- Migration：`e6b5c3074f89 (head)`，单一 head
- 环境：本地开发 PostgreSQL `qa_eval` + 配置的本地 ArtifactStore
- Registry Sync：2 Metrics / 3 Calculators / 3 Bindings
- Registry fingerprint：`sha256:438fea8fe1391d3f83b92df7dec22dbc9a7961c0ca613a76609c6bd1b2582659`
- Dataset Version：`01a17c8d-b995-7a9c-801a-73e6551bc2ab`
- Profile fingerprint：`sha256:8f17f16f49fdd8157b15e168630d9c50e2dfff2a8b13cfb63ff4e39a9ac24cf0`
- Proposal：`01a1805d-8c0c-7621-acc3-b221600c64ab` revision 1，两个 item 均为 `APPLICABLE`
- Contract Version：`01a1805d-8c1c-7329-af12-2bdf41821787`，2 items
- Evaluation Run：`01a1805d-8c21-7fff-ac34-0254dcb9c885`，`SUCCEEDED`

StageRun：

- `01a1805d-8c28-7431-a6fd-bdd6f7a34ebe`：`0204` / `structural_completeness`，`SUCCEEDED`
- `01a1805d-8fad-7e3e-9548-c5cbc31449c5`：`02080103` / `exact_text_duplicate`，`SUCCEEDED`

MetricResult：

- `01a1805d-8f9e-7a1b-a416-170380734262`：`0204`，`OK`，Evidence 1
- `01a1805d-97c9-7ba5-a8ee-4bc51f1a680c`：`02080103`，`OK`，Evidence 1

两条 Rule 均为 `PASS`；Issue 总数 45。Report `01a1805d-97e6-71a6-b24a-02a6b099a82b` 的 execution/gate 均为 `SUCCEEDED/PASS`，包含 `0204` 与 `02080103` 两个指标。

## 5. 完成结论与后续边界

2A 已建立综合评测所需的合法候选、校验草稿和多指标正式意图边界，且未复制执行引擎。下一步可选择：

1. 按综合评测路线进入 Logical Plan / 多 Stage 状态语义；或
2. 完成 2B 的规则推荐、Proposal revision 持久化和人工确认工作台。

无论选择哪条路线，Agent 只能消费 2A 的合法候选与 validation findings，不能绕过 Contract 发布边界。
