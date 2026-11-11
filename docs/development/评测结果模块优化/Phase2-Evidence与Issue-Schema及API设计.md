# Phase 2：Evidence、Issue Schema 及 API 设计

> 本文与《Phase2-结果溯源技术架构设计》共同定义契约。示例均为模拟数据，不代表真实运行结果。

## 1. 现有 Schema 审计

| Evidence | 当前行结构 | 可定位能力 | 缺口 |
| --- | --- | --- | --- |
| 0202 敏感实体 | sample_id、content_id、logical_field、category、start/end、coordinate_space、mapping_precision、channels、脱敏摘录、review_status | Record/Content/可见文本 span | 无稳定 row_id/text_version；总量可能被 100 行上限截断 |
| 0204 missingness | sample_id、selector/kind/category 等 | Record/字段 | 字段命名未统一为 field_path；通常无 span |
| 编码/0206 类文本证据 | sample_id、content_id、role、source_paths、violation 与 offset | Record/Content/文本位置 | 坐标语义随 schema；每记录可能截断 |
| 0208 duplicate groups | group_id、代表样本、sample_ids、hash；JSON object 而非逐行 JSONL | Group/成员 Record | 行引用与通用 locator 不一致 |

`EvidenceArtifact` 目前只描述整个 Artifact；`IssueEvidenceRef.locator` 是自由 JSON object；`QualityIssueV1` 已支持 DATASET/GROUP/SAMPLE/PAIR/CONTENT/ANNOTATION 和 related subjects。无需新增业务实体，但要给 Evidence 行和 locator 建立规范。

## 2. Evidence 行信封 v1

新产物推荐使用 schema-specific payload + 最小公共信封；历史 schema 不重写，由相应 Reader adapter 投影为同一内部视图。

```json
{
  "row_id": "sha256:...",
  "subject": {"type": "CONTENT", "sample_id": "p-001", "content_id": "body-html"},
  "location": {
    "field_path": "$.contents[content_id=body-html].payload.text",
    "logical_field": "article.body",
    "span": {"start": 16, "end": 27, "unit": "UNICODE_CODE_POINT"},
    "coordinate_space": "VISIBLE_TEXT",
    "text_version": "sha256:...",
    "mapping_precision": "EXACT_VISIBLE_TEXT"
  },
  "fact": {"code": "MOBILE_PHONE_CN", "method": ["RULE"], "review_status": "UNREVIEWED"},
  "display": {"masked_excerpt": "138****8000"},
  "extensions": {"rule_ids": ["mobile-cn-v1"], "model_score": null}
}
```

公共字段：

| 字段 | 类型/必填 | 说明与兼容 |
| --- | --- | --- |
| row_id | string，新 schema 必填 | `sha256:` + canonical identity fields；历史行由 Reader 稳定派生 |
| subject | IssueSubject 子集，必填 | 不重复创建 record_id；统一沿用 `sample_id` |
| location.field_path | string，可选 | JSONPath-like 展示定位；content_id 仍是寻址主键 |
| location.clause_path | string，可选 | 只有解析器产出稳定条款路径时存在 |
| location.span | object，可选 | 半开区间 `[start,end)`；unit 显式声明 |
| coordinate_space | enum，span 时必填 | `CONTENT_TEXT`、`VISIBLE_TEXT`、`RAW_HTML`；不得混用 |
| text_version | checksum，span 时必填 | 对被定位文本视图做 SHA-256，防止错位高亮 |
| related_subjects | array，可选 | Pair/Group 行的关联对象；沿用 IssueSubject 结构 |
| fact/display/extensions | object | 指标专属事实、脱敏展示和扩展；不强行统一业务字段 |

`UTF8_BYTE`、`UNICODE_CODE_POINT`、`UTF16_CODE_UNIT` 是 offset unit；`RAW_HTML/VISIBLE_TEXT/CONTENT_TEXT` 是 coordinate space，两个维度必须分别表达。0202 当前 start/end 投影为 `UNICODE_CODE_POINT`。浏览器高亮必须转换为 UTF-16 code unit，禁止直接使用 code-point index。

### 2.1 稳定 row_id

身份输入只包含事实稳定字段：schema_ref、dataset_version_id（由 Artifact 上下文注入）、sample/content/group/pair、事实 code、coordinate space、start/end、规则/模型身份。对 canonical JSON（UTF-8、键排序、无无意义空字段）取 SHA-256。不得使用行号、byte offset、写入时间或脱敏文案；因此重建排序变化不改变 row_id。

外部引用格式：

```json
{"evidence_id":"...", "locator":{"type":"EVIDENCE_ROW","version":"1.0","row_id":"sha256:..."}}
```

byte offset 与 line number 只存在技术索引，API 可返回用于诊断，但不作为 QualityIssue 的稳定身份。

## 3. EvidenceArtifact 与完整性扩展

优先扩展现有 `EvidenceSummary.attributes`，避免立刻升级 MetricResult schema：

```json
{
  "detected_count": 325,
  "emitted_count": 325,
  "truncated": false,
  "truncation_reason": null,
  "completeness_status": "COMPLETE",
  "row_identity_version": "1.0",
  "location_schema_version": "1.0"
}
```

字段语义：

| 字段 | 定义与来源 |
| --- | --- |
| detected_count | Calculator 按该 Evidence schema 的“检测事实单位”累计的总数；0202 是去重后实体命中，0208 可是 group/member 等 schema 声明的单位 |
| emitted_count | 实际写入 Artifact 的 schema record 数，由写入器累计；应与 `EvidenceArtifact.summary.record_count`/Artifact record_count 一致 |
| truncated | 写入前是否因上限/配额主动丢弃了应持久化的事实 |
| truncation_reason | truncated=true 时必填的稳定原因码，如 `EVIDENCE_LIMIT_REACHED` |
| completeness_status | `COMPLETE / INCOMPLETE / UNKNOWN`；放在 attributes 以兼容现有 Schema，不新增业务状态 |

完整性规则必须由 `schema_ref` 声明，不能在通用层强制 `detected_count == emitted_count`：

- 0202 一行对应一个合并后的实体命中，COMPLETE 要求 `detected_count == emitted_count == record_count`、`truncated=false` 且 checksum/解析校验通过；
- 0204 按其 schema 的一行一个缺失事实规则校验，但 Measurement 的缺失 Record 数仍可能小于 Evidence 行数；
- 0208 一条 group 记录可代表多个成员或冗余事实，COMPLETE 只要求所有已检测 group 都被写入并通过 group 内部计数校验，不能与成员异常数直接比较。

API/UI 预览限制不改变 Artifact。历史数据缺少完整性属性为 UNKNOWN；明确截断为 INCOMPLETE；只有 schema-specific validator 校验通过才是 COMPLETE。Artifact 存在本身不代表完整。

Issue Builder 输入完整性规则：COMPLETE 可生成完整明细；INCOMPLETE 只生成已保存行可支持的 Issue，并输出 `issue_detail_completeness=INCOMPLETE`；UNKNOWN 对历史数据保守展示为未知，不允许声称全量。Builder 不因不完整而伪造缺失 Issue，也不从 Measurement 反推记录。

如后续升级 `EvidenceArtifact`，仅新增可选 `completeness`；技术索引引用不建议嵌入不可变的 MetricResult/Evidence 事实快照，应放独立技术 metadata 中，维持 `MetricResultV1` 旧文档可解析。

## 4. Issue locator 规范

保持 `JsonObject` 以兼容历史数据，所有新 locator 必须含 `type/version`：

- `EVIDENCE_ROW/1.0`：row_id，可选 location 摘要；
- `MEASUREMENT/1.0`：measurement、rule_evaluation_id、coverage；
- `GROUP/1.0`：group_id、signature；
- `PAIR/1.0`：pair_id、left/right row_id；
- `FIELD/1.0`：sample_id、content_id/field_path；
- `SPAN/1.0`：field locator + span/coordinate_space/text_version。

Issue 自身的 `subject` 是治理对象；`IssueEvidenceRef.locator` 是在某 Evidence Artifact 内找到支撑事实的方法。不要在 locator 复制整行 Evidence。历史 locator 没有 discriminator 时按对应 schema 的 legacy reader 解释。

0202 一条 CONTENT Issue 的 evidence 可有多项：

```json
[
  {"evidence_id":"11111111-1111-1111-1111-111111111111","locator":{"type":"EVIDENCE_ROW","version":"1.0","row_id":"sha256:a..."}},
  {"evidence_id":"11111111-1111-1111-1111-111111111111","locator":{"type":"EVIDENCE_ROW","version":"1.0","row_id":"sha256:b..."}}
]
```

## 5. Issue Builder 注册与幂等

建议文件：

- `src/qa_eval/application/issues/protocol.py`
- `src/qa_eval/application/issues/registry.py`
- `src/qa_eval/application/issues/generic_threshold.py`
- `src/qa_eval/application/issues/sensitive_entity.py`
- `src/qa_eval/application/evidence/readers.py`

Registry 精确匹配 metric/version/kind/schema_ref，不使用“最新版本”隐式回退。Builder 输出排序稳定；`dedup_key` 为 `sha256:` 加 canonical business identity。DB 已有 `(run_id, dedup_key)` 唯一约束；`issue_id` 当前由 uuid7 生成，并不具备跨重建确定性，因此命中已有 dedup key 时必须保留原 issue_id。

### 5.1 幂等与事务算法

最终采用“**完整计算后事务级差异 upsert（B，以 A 的唯一约束兜底）**”，禁止删除旧集合再插入：

1. 在数据库写事务之外/保存点内流式读取并校验全部输入，构建稳定排序的候选集合；Builder、Artifact、schema 任一失败时不写 Issue。
2. 开启事务并锁定同一 `metric_result_id` 的 IssueIndex/构建互斥对象；数据库唯一约束处理并发竞争。
3. 按 `(run_id, dedup_key)` 查询已有 Issue。新键插入；已有键保留 issue_id、created_at 和 status，只更新允许的系统派生字段及 evidence 引用。
4. 当前没有 Issue 状态更新 API，所有现存生成路径创建 OPEN；但契约已经允许 ACKNOWLEDGED/RESOLVED/DISMISSED。任何非 OPEN 状态均视为人工/外部管理字段，重建绝不能覆盖。OPEN 也保留，除非未来引入明确的状态 provenance。
5. 对新集合中消失的旧 Issue，本轮不物理删除、不自动 RESOLVED/DISMISSED；记录重建差异并保留旧对象，避免 Builder 版本变化破坏历史。未来有状态来源与审计后再设计 retire 语义。
6. 基于事务提交后的最终 DB Issue 集合生成新的 IssueIndex，并与 upsert 同一事务提交引用；失败时 Issue、IssueIndex 和 Report 均不可部分可见。

系统拥有的可更新字段限于 detector version、message、severity/category/code（业务身份未变化时）和 Evidence refs；状态、issue_id、created_at 以及未来 review actor/note/timestamp 是受保护字段。determination 从 CANDIDATE 变 CONFIRMED 也不能靠 Builder 重跑模拟人工确认；只有显式自动确认政策版本变化并走审计迁移，或未来复核 API，才能改变。

异常恢复：Artifact 暂不可用、未知 schema、Builder 中途异常均回滚本次集合；并发 finalize 由锁/唯一约束串行化，冲突方重读最终集合；Builder 版本变化先 shadow diff，不删除历史 Issue。现有 `finalize()` 对已有 Report 直接返回，本轮设计的“重建”必须是未来显式受控入口，不能暗中改变已冻结报告。

0208 现有逻辑先包成 `DuplicateGroupIssueBuilder`，输出保持不变；迁移期间 Registry 未启用时仍走旧分支，二者不得同时运行。

## 6. 技术索引

Evidence index Artifact 使用 JSONL 或 SQLite（二选一；MVP 推荐 SQLite 以支持复合过滤），内容仅为：`row_id, byte_start, byte_length, line_no, sample_id, content_id, fact_code, group_id`。Sample index 为 `sample_id → byte range`。索引 metadata 必须绑定 source artifact_id、checksum、size 和 schema_ref。

源码没有可直接复用的 Artifact 派生索引状态定义；StageRun/DocumentTask 的运行状态语义不同，不应复用。因此状态只属于技术索引 metadata：`NOT_BUILT / BUILDING / READY / FAILED / STALE`，不是 MetricResult、Evidence 或 Run 的业务状态。metadata 可先存 ArtifactStore sidecar + 控制面记录；若 Phase 2.3 证明需要新表，该表仅是可重建技术索引目录。

生命周期：Artifact commit 后发出 best-effort 构建触发，但不等待；首次查询 NOT_BUILT 时以 source artifact_id+checksum 作为幂等键触发；条件更新/租约保证同一 checksum 只有一个 BUILDING worker。READY 查询前校验 source checksum/size；不符转 STALE，拒绝使用并触发重建。FAILED 保存稳定错误码、attempt、next_retry_at，允许有界指数退避；不得改变 MetricResult。source Artifact 删除时清理 sidecar/catalog；孤儿索引可由周期任务回收。历史 Artifact 首次访问按相同流程补建。

查询降级：READY 使用索引；BUILDING 返回 `INDEX_BUILDING` 和重试提示；NOT_BUILT 触发构建，小于配置阈值且 Local 的 Artifact 可单次安全线扫；FAILED 返回可解释状态并允许显式/定时重试；STALE 禁止使用并重建。大文件或 S3 不得在每次请求中完整下载或反复线扫。

索引构建完全在 `Evidence Artifact 持久化 → MetricResult 可见` 之后或并行发生。缺失、失败、STALE 只影响高效查询。

IssueIndex 是业务 Issue 导出/汇总，Evidence index 是可重建访问结构，二者不可合并。

## 7. API

所有接口复用现有认证；在部署鉴权中增加 dataset-version read 权限和 sensitive-evidence 权限。无后者时仅返回脱敏 excerpt，永不返回 Artifact URI 或 raw 敏感值。

| Method / URL | 参数 | 响应 | Handler 建议 |
| --- | --- | --- | --- |
| GET `/api/v1/evaluation-runs/{run_id}/quality-issues` | 现有参数；扩展 metric_key/code/scope/sample_id/cursor | Issue page | `quality_results.list_issues` |
| GET `/api/v1/quality-issues/{issue_id}` | — | 单个 Issue | 新 handler |
| GET `/api/v1/quality-issues/{issue_id}/evidence` | cursor,limit | 已解析 Evidence 行 page | 新 handler → EvidenceQueryService |
| GET `/api/v1/evidence/{evidence_id}` | — | Artifact 安全元数据、完整性 | 新 handler |
| GET `/api/v1/evidence/{evidence_id}/rows` | cursor,limit,row_id,sample_id,content_id,fact_code | 行 page | 新 handler |
| GET `/api/v1/dataset-versions/{version_id}/samples/{sample_id}` | 已有 | SampleRecord | 现有 handler，内部改 offset index |
| GET `/api/v1/quality-issues/{issue_id}/locations` | view=`normalized|visible` | 前端可用定位、受限文本视图 | 新组合 handler |

Evidence 分页使用不透明 cursor（编码 `index checksum + last sort key`），不再为高基数数据使用 offset。limit 默认 20，最大 100。Issue API 可保留 offset 兼容，并新增 cursor。

错误码：404 `ISSUE_NOT_FOUND/EVIDENCE_NOT_FOUND/SAMPLE_NOT_FOUND`；409 `INDEX_NOT_BUILT/INDEX_BUILDING/INDEX_STALE`；422 `UNSUPPORTED_EVIDENCE_SCHEMA/INVALID_LOCATOR/SPAN_OUT_OF_RANGE/TEXT_VERSION_MISMATCH`；403 `EVIDENCE_ACCESS_DENIED`；410 `ARTIFACT_GONE`；503 `INDEX_BUILD_FAILED/ARTIFACT_UNAVAILABLE`。响应附 `index_status` 和可选 `retry_after`，但不修改评测结果状态。

### 7.1 模拟端到端响应

```json
{
  "issue": {
    "issue_id": "22222222-2222-2222-2222-222222222222",
    "dataset_version_id": "33333333-3333-3333-3333-333333333333",
    "source": {"metric_result_id":"44444444-4444-4444-4444-444444444444","metric_key":"0202","metric_version":"2.0.0","measurement":"sensitive_entity_hit_count","rule_evaluation_id":null},
    "detector": {"key":"sensitive-entity-issue-builder","version":"1.0.0"},
    "category": "SENSITIVE_ENTITY",
    "code": "MOBILE_PHONE_CN_CANDIDATE",
    "determination": "CANDIDATE",
    "severity": "WARNING",
    "scope": "CONTENT",
    "subject": {"type":"CONTENT","sample_id":"policy-001","content_id":"body-html"},
    "message": "正文疑似包含手机号，请复核。",
    "evidence": [{"evidence_id":"11111111-1111-1111-1111-111111111111","locator":{"type":"EVIDENCE_ROW","version":"1.0","row_id":"sha256:abc"}}]
  },
  "evidence_rows": [{
    "row_id":"sha256:abc",
    "location":{"logical_field":"article.body","span":{"start":16,"end":27,"unit":"UNICODE_CODE_POINT"},"coordinate_space":"VISIBLE_TEXT","text_version":"sha256:def"},
    "fact":{"code":"MOBILE_PHONE_CN","method":["RULE"],"review_status":"UNREVIEWED"},
    "display":{"masked_excerpt":"138****8000"}
  }],
  "record_location": {
    "sample_id":"policy-001",
    "content_id":"body-html",
    "view":"visible",
    "highlight":{"start":16,"end":27,"unit":"UNICODE_CODE_POINT"},
    "precision":"EXACT_VISIBLE_TEXT"
  },
  "limitations":["模拟数据；位置不代表真实政策记录", "不能映射到 raw HTML offset"]
}
```

## 8. 安全、兼容与降级

- Evidence 默认只含脱敏片段；原始记录查看沿用 Dataset 权限，并记录 issue/evidence/sample、操作者和时间的访问审计。
- API 不暴露内部 Artifact URI、本地路径、模型目录或 checksum 以外的基础设施信息。
- 历史 MetricResult/Issue/Report 不迁移；Reader adapters 支持旧 schema，缺 row_id 时确定性派生，缺完整性字段则 UNKNOWN。
- span 校验失败时仍返回 Issue、字段和脱敏 Evidence，前端禁止高亮并显示原因。
- raw HTML 采用安全渲染/转义；不得把未可信 HTML 直接注入 DOM。
