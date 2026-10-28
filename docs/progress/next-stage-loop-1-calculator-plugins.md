# 下一阶段闭环 1：Calculator 真正插件化验收记录

- 验收日期：2026-10-28
- 结论：已完成；实现、自动化测试及真实 PostgreSQL/ArtifactStore 冒烟通过
- 范围：Calculator Protocol、受信任 Loader、02080103 平台归属、通用执行、标准参数校验、显式 Registry 同步

## 1. 平台边界

新增 `Calculator` Protocol，统一 `validate(context, parameters)` 和 `execute(context, parameters)`。执行结果统一包含 observations、population、semantic coverage、evidence 和 diagnostics；Calculator 不执行 Quality Contract 阈值判定。

`TrustedCalculatorLoader` 只接受 `qa_eval.calculators.*` entrypoint，并校验：

- `module:attribute` 格式与受信任包边界；
- Protocol 结构；
- Calculator key/version 与 Registry descriptor 一致；
- Calculator 声明源文件的组合 SHA-256 digest；
- availability 和 Python runtime dependency。

加载、参数和语义校验异常使用稳定错误码 `CALCULATOR_LOAD_ERROR`、`CALCULATOR_VALIDATION_ERROR`，StageRun 会保存 code、message 和 details；计算失败仍生成 `MetricResult.status=ERROR`，不会转换为质量 FAIL。

## 2. 02080103 迁移

- 正式算法位于 `src/qa_eval/calculators/text_duplicate_algorithm.py`。
- 平台适配器位于 `src/qa_eval/calculators/text_duplicate_degree.py`。
- Skill 旧脚本保留命令兼容性，但其公开 `calculate` 已指向平台算法；平台不再读取或动态加载 `.agents/skills`。
- `EvaluationRunService` 不再导入 `02080103` 或包含该指标的 observations、population、semantic coverage、evidence 组装逻辑。
- Calculator evidence 只能从自己的临时输出目录交付，执行器统一提交 ArtifactStore 并生成 canonical EvidenceArtifact。

## 3. Registry 与参数

Calculator descriptor 在原有协议上扩展 runtime、输入 artifact、resource profile、applicability constraints、semantic coverage、availability 和敏感内容访问策略，没有创建第二套 Registry。

参数使用 Draft 2020-12 JSON Schema 校验；Calculator 的 `validate` 继续负责 JSON Schema 无法表达的语义约束，例如 MinHash permutations 必须可被 LSH bands 整除。

Registry GET 已变为纯只读，不再隐式写数据库。显式同步入口为：

```text
POST /api/v1/registry/sync
```

应用部署或测试初始化必须先执行同步，再发布引用 Registry 对象的 Contract。

## 4. 验收证据

| 检查项 | 结果 |
| --- | --- |
| canonical schema drift | 通过 |
| Calculator Loader/semantic validation tests | 通过 |
| 02080103 数值、Evidence、Issue、Report 回归 | 通过 |
| 完整 pytest | 190 passed、6 skipped |
| 跳过项 | 仅需 `RUN_MYSQL_INTEGRATION=1` 的 6 个 MySQL 集成测试 |

覆盖的拒绝路径包括：非受信任 entrypoint、缺失 entrypoint、code digest 不匹配、runtime dependency 缺失、JSON Schema 参数错误和 Calculator 语义参数错误。

## 5. 后续边界

- 当前 Runtime 是受信任 Python 进程内加载，不提供第三方任意代码沙箱。
- timeout/resource profile 已进入 descriptor，但进程级强制隔离留给后续 Runtime 演进。
- QualityResult 的特定 Issue detector 仍属于共享结果层的后续插件化议题，不影响 Calculator 调用已经通用化的结论。

## 6. 运行期加固

针对实际环境 Registry 未同步导致 Run 创建后立即失败的问题，增加以下保护：

- `EvaluationRunService.create()` 在持久化 Physical Plan 和 Run 前调用受信任 Loader 预检；不可加载的 Descriptor 直接返回结构化客户端错误，不再创建必然失败的 Run。
- `GET /api/v1/registry/readiness` 只读检查数据库中每个 Calculator Descriptor 与当前进程代码、Protocol、digest 和依赖是否一致。
- `GET /api/v1/evaluation-runs/{run_id}/stage-runs` 暴露 StageRun 的结构化 error，便于定位原始 Calculator 异常。
- 后台执行线程对 Stage 边界外的未处理异常记录完整服务端 traceback。

这些保护不会自动执行 Registry Sync，也不会修改历史 Physical Plan、Run、Metric Result 或 Quality Report。

## 7. 真实环境闭环

2026-10-28 在本地开发 PostgreSQL `qa_eval`、当前 Registry 和配置的本地 ArtifactStore 上完成最终冒烟：Alembic 为单一 `e6b5c3074f89 (head)`；显式 Registry Sync/readiness 通过；在已发布 Dataset Version `01a17c8d-b995-7a9c-801a-73e6551bc2ab` 上为 `text_duplicate_degree@2.0.0` 新建 Contract Version `01a1804e-01ea-7107-a5d3-482f1d48601f` 和 Run `01a1804e-01f0-7e2b-b977-07170dbc3863`。StageRun、MetricResult、Rule/Issue/IssueIndex 与 Report 均成功，Report `01a1804e-4a0e-7724-84bf-ab1d1a2be396` 可读取。完整证据与 exact-only 对照见闭环 1.5 验收记录。
