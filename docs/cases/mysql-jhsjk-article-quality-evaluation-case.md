# MySQL `jhsjk_article` 数据集综合质量评测案例

- 案例日期：2026-10-29（Asia/Shanghai）
- 案例状态：真实 Dataset Version / Agent / Contract / Run / Report 已完成；浏览器 UI 点击验收与 Ruff 全绿待完成
- 证据清单：[`evidence/mysql-jhsjk-article-20261029/manifest.json`](evidence/mysql-jhsjk-article-20261029/manifest.json)
- 报告摘录：[`evidence/mysql-jhsjk-article-20261029/quality-report.json`](evidence/mysql-jhsjk-article-20261029/quality-report.json)
- 核验记录：[`evidence/mysql-jhsjk-article-20261029/verification.md`](evidence/mysql-jhsjk-article-20261029/verification.md)

## 1. 案例背景与目标

本案例验证 `policy.jhsjk_article` 固定快照能否通过 qa-eval 的真实 Agent 综合评测链路完成：Dataset Version 发布、确定性 Agent 推荐、人工确认、多 item Quality Contract、后台 Evaluation Run、Metric Result、Rule、Issue 和 Quality Report。

案例不声称完成指南全部数据质量指标。当前真实执行范围只有 0204“结构完整性”和 02080103“重复内容程度”；其他指标保持未评测状态。

## 2. 数据集概况

固定输入为 `data/dataset-quality-demo/policy.jhsjk_article/20261022T085048Z/raw_snapshot.jsonl`，来源标识为 `mysql://policy/jhsjk_article`。

| 项目 | 值 |
| ---- | -- |
| Dataset ID | `d769d8a6-41c0-4690-98c7-3ef08ae3dd6d` |
| Dataset Version ID | `01a1852e-189d-74f0-ad95-b00793cd80bc` |
| 原始记录数 | 14,960 |
| 原始快照 SHA-256 | `dcc198fc35811a22d0d045177621876cfb5733296326e446d3c31154c142e59f` |
| Mapping | `jsonl_mapping@2.0.0` |
| Profile fingerprint | `sha256:1f2e10fdd0c432de25da7ae9a93daf335b04bed305a2e6805682b903ce36d57c` |
| 标准化记录数 | 14,960 |
| 拒绝记录数 | 0 |
| Normalized artifact SHA-256 | `176caf18d2275214b0dadf956fb9b1862e0e04c55de9f256fe11d3685c54a7b3` |

字段映射将 `sample_id` 映射为样本标识，将 `text` 映射为 `article.body.plain`，并保留 `source` 与 `text_is_null`。标准化没有拒绝记录，因此本次没有 rejection 原因样本；空字符串仍在 SampleRecord 中保留，并由 0204 作为缺失类别识别。

## 3. 评测依据与指南指标体系

评测依据为数据质量 0201–0208 及文本子指标。Registry 展示完整指南覆盖，但只有 Applicability 为 `APPLICABLE` 且 Calculator 为 `AVAILABLE` 的候选才能进入 Proposal。

本次 Contract 只包含：

- 0204：`structural_completeness@1.0.0`，主测量 `complete_record_ratio`；
- 02080103：`exact_text_duplicate@1.0.0`，主测量 `exact_affected_record_ratio`。

阈值分别为 `GTE 0.95` 和 `LTE 0.05`。它们来自 `DEMO_POLICY_V1`，不是指南规定的权威阈值。

## 4. 平台评测流程与可追溯链路

```text
Dataset d769d8a6-...
  → DatasetVersion 01a1852e-189d-...
  → AgentSession 01a1852e-1d4c-...
  → ProposalRevision 01a1852e-1db5-...
  → ContractVersion 01a1852e-251f-...
  → LogicalPlan 01a1852e-2544-...
  → PhysicalPlan 01a1852e-2546-...
  → Run 01a1852e-2546-73ec-...
  → StageRun / MetricResult / Rule / IssueIndex
  → Report 01a1852e-2ea8-...
```

Run 的 `input_fingerprint` 与原始固定快照 SHA-256 一致。Report、Run、Physical Plan 和 Contract 的 Dataset Version / Contract Version 引用已逐项核对一致。

## 5. 确定性 Agent 推荐、人工调整与契约确认

Agent Session `01a1852e-1d4c-743f-9d68-ab9b27b7789c` 加载 `data-quality@1.0.0`，依次成功调用：

1. `get_dataset_profile`
2. `discover_metrics`
3. `recommend_evaluation_proposal`
4. `create_quality_contract`
5. `create_evaluation_run`
6. `get_evaluation_status`
7. `get_quality_report`

Proposal Revision `01a1852e-1db5-7885-aafb-a9efa0ca1ee5` 校验为 valid。人工调整为“无”，确认采用确定性推荐的两个 item 及 Demo 阈值；Contract 发布时间为 `2026-10-29T01:02:36.319432Z`。

已配置的 `kimi-k2.6` Provider 拒绝 `temperature=0.1`（网关提示该模型只接受 1），两次调用失败后 Runtime 按设计记录 `DETERMINISTIC_FALLBACK / LLMTransportError`。推荐和执行结果来自确定性 Tool，不受模型失败影响；Provider 参数兼容性是进入生产化前需修复的明确缺口。

本次通过与前端相同的本地 HTTP API 完成真实写入和执行，但没有浏览器实际点击记录，因此不能把本节作为 UI 点击验收证据。UI 人工验收仍需按方案第 11 节执行一次。

## 6. Contract、Run、Calculator 与真实指标结果

Contract Version `01a1852e-251f-72fe-8f28-504307408481` 含 2 个 items。Run `01a1852e-2546-73ec-8ea9-b7e6b63ca491`、Physical Plan `01a1852e-2546-7924-abe8-1d775ee3f1f7` 均为本次新建，Run 状态为 `SUCCEEDED`。

| 指标 | Calculator / code digest | 参数 | 分子/分母 | 值 | Result / Rule |
| ---- | ------------------------ | ---- | --------- | -- | ------------- |
| 0204 | `structural_completeness@1.0.0` / `sha256:6ae27c...13751` | `content_role=article.body.plain` | 14928 / 14960 | 0.9978609626 | OK / PASS |
| 02080103 affected | `exact_text_duplicate@1.0.0` / `sha256:d453df...ce00` | `content_role=article.body.plain` | 89 / 14928 | 0.0059619507 | OK / PASS |
| 02080103 redundant | 同上 | 同上 | 45 / 14928 | 0.0030144695 | diagnostic |

0204 Evidence 为 32 条 missingness 记录，SHA-256 `a14a2db43f65f6f45810ae07657ab0c028d4916cf5d9fe4bc5df232b3653ebe6`。02080103 Evidence 为 44 个完全重复组，SHA-256 `5e152948c1a2f74a1e2b111b9e69bb2542e55fab83599873886c661c53ae5fce`。

## 7. 已评测得分、有效覆盖率与雷达图分析

- 已评测指标得分：`99.59495`；
- 有效覆盖率：`14.2857%`；
- 0204 雷达值：`99.7861`；
- 0208 雷达值：`99.4038`，只聚合叶子指标 02080103；
- 0201、0202、0203、0205、0206、0207 为 N/A，不以 0 分填充。

报告强制声明：“仅代表已评测指标，不代表指南全指标综合质量”。已评测得分不能脱离有效覆盖率单独解释。

## 8. 主要质量问题与证据样本

0204 发现 32 条空字符串记录，并生成缺失证据，但当前报告没有为其创建 Quality Issue。02080103 发现 44 个完全重复组，涉及 89 条记录，其中 45 条被判定为冗余副本，生成 45 个 `CONFIRMED / WARNING / EXACT_DUPLICATE_REDUNDANT` Issue。

IssueIndex `01a1852e-2ea5-7cb0-b7a9-bc775909c551` 含 45 条记录，artifact SHA-256 为 `372b8b8913fb4527b4b6777c5d7d10fbe56dcf15c49ad4c8e625b2959954d66e`。证据只保存样本标识、group hash 和 normalized hash，本案例文档不复制文章正文。

## 9. 数据整改建议及优先级

1. 高优先级：按 02080103 Evidence group 审核 45 条冗余副本；确认业务语义和版本关系后再去重，不能仅凭 hash 自动删除源数据。
2. 中优先级：复核 32 条 `article.body.plain` 空字符串记录，区分抓取失败、正文解析失败和合法空值；修复后重新发布新 Dataset Version，不覆盖本次版本。
3. 后续：补齐其余 Calculator 或所需业务输入，再提高有效覆盖率；在覆盖率提升前不使用当前已评测得分代表全量质量。

## 10. 未评测指标、当前能力与局限性

0201、0202、0203、0205、0206、0207，以及 02080101、02080102、02080104、02080105、02080106、02080107 均为 `NOT_EVALUATED`，没有伪造 MetricResult 或分数。

本次两个 MetricResult 均为 OK、两个 Rule 均为 PASS，因此 ERROR 不映射为 FAIL 的性质由实现和自动化测试保证，但没有在这条真实 Run 中人为制造 ERROR 来作为运行时证据。Agent Runtime 仍是 V1：后台执行使用进程内线程池，缺少持久队列、租约、多进程恢复、真正取消和完整预算/重试。

本次 LLM Provider 因 temperature 参数不兼容而降级到确定性 policy；这验证了 fallback 路径，但不构成 Provider 模式成功验收。

浏览器 UI 点击链路尚未人工执行；仓库 Ruff 仍有 22 项来自当前未提交的文本困惑度/纯度与 Skill 脚本改动。因此案例数据链成立，但仓库级发布质量门尚未完全通过。

## 11. 可复现命令、测试结果与案例总结

主要质量门命令：

```bash
docker compose --env-file .env -f deploy/docker-compose.yml up -d postgres
uv run alembic upgrade head
QA_EVAL_DATABASE_URL="$QA_EVAL_TEST_DATABASE_URL" uv run alembic upgrade head
uv run pytest -q
uv run ruff check .
uv run python scripts/contracts/generate_json_schemas.py --check
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

结果为：开发库和测试库均位于 Alembic head `e84d9b73a6f1`；pytest `371 passed / 6 skipped`；schema drift、前端 lint/build 通过；Ruff 未通过，共 22 项。6 个 skip 均为需显式启用的 MySQL 直连测试，本案例按批准方案使用固定上传快照。

本案例证明固定的 `jhsjk_article` Dataset Version 已能完成真实 Agent → Proposal → Contract → Run → Result/Rule/Issue → Report 闭环，并诚实表达覆盖缺口。进入 Agent Runtime 生产化开发的基础已经具备；正式发布前仍需完成 UI 人工验收、Ruff 清理及生产级执行生命周期建设。
