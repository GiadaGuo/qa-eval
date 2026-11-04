# 任务：重构 qa-eval Evaluation Run 状态轮询机制

## 一、问题背景

当前 qa-eval 平台的评测管理模块存在不合理的轮询行为。

前端持续请求：

`GET /api/v1/evaluation-runs`

该接口每次返回所有历史 Evaluation Run，包括：

- `RUNNING`
- `SUCCEEDED`
- `PARTIAL_SUCCEEDED`
- 其他终态任务

当前存在以下问题：

1. 即使只提交了一个新评测任务，前端仍持续请求完整历史列表。
2. 已完成任务仍被重复查询。
3. 页面持续轮询，产生不必要的请求和数据库查询。
4. 历史任务中存在长期停留在 `RUNNING` 的记录，可能影响轮询停止条件。
5. 缺少对当前活跃任务与历史任务的明确区分。

**目标：将全量列表轮询改为基于 run_id 的精准状态轮询，同时保留评测管理列表的正常查询能力。**

## 二、首先审查当前代码

请先阅读项目实际代码，不要直接假设接口、目录或状态枚举。

重点定位：

- Evaluation Run 列表 API 和详情 API
- 前端 Evaluation Run 管理页面
- 提交评测任务的入口及响应
- 当前轮询实现（setInterval、setTimeout、React Query、SWR 或其他机制）
- Evaluation Run 状态机及终态定义
- Worker 心跳更新机制
- 异常任务恢复与超时检测机制

明确当前问题的调用链：

`用户提交评测 → 后端创建 Run → 前端获取 Run → 定时查询 → 状态更新 → 停止轮询`

先确认是否已经存在 `GET /api/v1/evaluation-runs/{run_id}`，优先复用现有 API，不重复开发。

## 三、目标设计

### 1. 区分列表查询和单任务轮询

**列表查询**

`GET /api/v1/evaluation-runs`

用途：

- 进入评测管理页面时加载历史任务
- 用户主动刷新列表
- 任务完成后同步更新列表

默认不进行持续的全量列表轮询。

**单任务状态查询**

`GET /api/v1/evaluation-runs/{run_id}`

用途：

- 查询指定评测任务的状态
- 评测任务运行期间持续轮询
- 获取任务完成时间、错误信息及执行状态

不允许为了获取单个 Run 的状态而反复拉取全部 Run，再由前端筛选。

### 2. 提交任务后启动精准轮询

用户提交 Evaluation Run 后：

1. 后端返回新创建的 `run_id`。
2. 前端保存该 `run_id`。
3. 使用该 `run_id` 发起单任务状态轮询。
4. 默认每 3 秒查询一次，可配置。
5. 任务状态更新后同步更新前端对应记录。
6. 任务进入终态后立即停止轮询。
7. 完成后刷新对应任务详情、评测结果及必要的列表数据。

不要使用“按创建时间排序取最新一条”的方式确定轮询对象。

### 3. 状态机与停止条件

从后端实际定义中读取状态枚举，不要自行创造状态。

原则上：

| 状态              | 是否继续轮询 |
| ----------------- | ------------ |
| PENDING / QUEUED  | 是           |
| RUNNING           | 是           |
| SUCCEEDED         | 否           |
| PARTIAL_SUCCEEDED | 否           |
| FAILED            | 否           |
| CANCELLED         | 否           |

实际枚举不一致时，以项目代码为准。

终态停止后，不应继续产生周期性请求。

### 4. 支持多个并发任务

不要将整个应用设计成只能追踪一个 Run。

应满足：

- 每个活跃 Run 以 `run_id` 唯一标识。
- 多个并发任务互不覆盖状态。
- 切换页面或数据集时不会误用其他任务的 run_id。
- 组件卸载时正确清理定时器或取消请求。
- 防止重复注册轮询器。
- 避免前一次请求未完成时继续堆积相同请求。

如果当前使用 React Query / TanStack Query，优先利用 `queryKey`、`refetchInterval`、`enabled` 等机制，而不是另建一套定时器。

### 5. 页面刷新后的状态恢复

刷新页面后不能只依赖内存中的 run_id。

请结合当前架构实现：

- 评测详情页从 URL 参数恢复 run_id。
- 评测管理列表可以识别当前非终态任务。
- 仅对需要展示或追踪的活跃任务启动精准轮询。
- 已完成的历史任务不进入轮询队列。

如果有多个活跃任务，可以分别查询，或评估是否有必要设计批量状态接口。

不要为了恢复活跃任务而持续高频查询完整历史列表。

### 6. 网络错误与异常处理

实现合理的容错机制：

- 请求失败不直接将任务标记为 FAILED。
- 短暂网络异常允许有限次数重试。
- 连续失败后暂停轮询并展示连接异常或状态未知。
- 404、权限错误等不可恢复错误需要单独处理。
- 页面卸载后不得继续更新已卸载组件的状态。
- 前端超时不等于后端任务失败。

避免无限制重试。

## 四、重点排查长期 RUNNING 的历史任务

当前 API 中存在以下异常任务：

`01a18653-8ec9-7afb-b60a-810fbd3283a6`

`01a1864e-a332-763d-a808-b389aeb2fdbe`

`01a1863d-a93a-725b-9fc6-5dfd051b29c3`

这些任务创建于 2026-10-29，仍然显示 `RUNNING`，且 `execution` 为空。

另一个任务：

`01a199e6-cf83-7308-a8e1-8b08a0779856`

状态为 `RUNNING`，最近一次返回的 `heartbeat_at` 为 `2026-11-02T01:41:13.449940+00:00`。

请检查：

1. Worker 异常退出时是否正确更新 Run 状态。
2. 是否存在异常未捕获导致任务永久停留 RUNNING。
3. Worker 心跳超时后是否存在恢复或失败收敛机制。
4. Worker 重启后是否有孤儿任务恢复逻辑。
5. Stage Run 失败后，父级 Evaluation Run 是否能够正确进入终态。
6. `execution` 为空是否意味着旧版本任务缺少执行元数据。

**注意：不要直接把所有历史 RUNNING 任务修改为 FAILED。**

应先分析 Worker 心跳、租约、任务执行记录和状态转移机制，明确哪些任务可以安全判定为失联。

若已有恢复机制，优先修复现有机制；若没有，给出最小可行的恢复方案。

## 五、实现约束

1. 优先修改现有前端轮询逻辑，不进行无关架构重构。
2. 保持现有 Evaluation Run API 的兼容性。
3. 不修改现有业务状态的语义。
4. 不删除历史评测数据。
5. 不引入新的重型依赖。
6. 如果需要数据库结构调整，必须提供 Alembic migration。
7. 不要将前端轮询停止机制和后端任务失败判定混为一谈。
8. 保持现有评测结果展示、任务详情和列表操作正常。

## 六、测试与验收

请补充必要的前后端测试，至少覆盖：

**场景 A：单任务运行**

提交一个任务，只轮询对应 run_id，不重复请求完整列表。

**场景 B：任务成功**

状态由 RUNNING 变为 SUCCEEDED，自动停止轮询并刷新结果。

**场景 C：任务部分成功**

状态变为 PARTIAL_SUCCEEDED，正确停止轮询并保留失败 Stage 信息。

**场景 D：任务失败**

状态变为 FAILED，停止轮询并展示错误原因。

**场景 E：并发任务**

同时存在两个 RUNNING 任务，状态互不干扰。

**场景 F：页面刷新**

刷新后能够恢复当前活跃任务的状态追踪。

**场景 G：历史异常任务**

长期 RUNNING 的历史任务不会导致全局列表无限轮询。

**场景 H：组件卸载**

离开页面后不再产生无意义的轮询请求。

**场景 I：网络异常**

短暂失败允许恢复，连续失败不会造成无限高频请求。

## 七、交付要求

请直接检查并修改项目代码，完成必要的测试。

最后输出：

1. 根因分析：为什么原来会持续轮询完整列表。
2. 修改文件清单及每个文件的作用。
3. 新的轮询调用链及状态管理方式。
4. 长期 RUNNING 任务的排查结果。
5. 测试命令及测试结果。
6. 尚未解决的风险与后续建议。

**最终验收重点：打开浏览器 DevTools → Network，在新建评测任务后，应看到指定 run_id 的详情请求，而不是每 3 秒重复请求整个 `/api/v1/evaluation-runs` 列表；任务进入终态后，周期性请求应停止。**

---

## 八、闭环结果（2026-11-02）

### 1. 根因分析

问题位于评测结果管理页：页面首次加载完整 Evaluation Run 列表后，只要列表中存在任意非终态记录，就会每 2.5 秒再次调用 `GET /api/v1/evaluation-runs`。轮询停止条件依赖整个历史列表，因此一条长期停留在 `RUNNING` 的历史记录就足以让全量列表请求一直持续。

后端还存在两个相关但独立的问题：

1. 原后台线程没有在提交任务前持久化 worker 所有权，进程重启后无法可靠识别孤儿任务。
2. 后台执行出现未捕获异常时只记录日志，失败状态未必能在独立、干净的事务中可靠落库，可能遗留永久 `RUNNING`。

前端停止轮询与后端失联任务收敛已分别处理，没有用前端超时冒充后端 `FAILED`。

### 2. 修改文件及作用

- `apps/web/src/pages/EvaluationResultsPage.tsx`
  - 完整列表只在进入页面、用户手动刷新或删除后同步时查询。
  - 对需要追踪的任务并发调用 `GET /api/v1/evaluation-runs/{run_id}`，不再周期性请求完整列表。
  - 每个 Run 独立合并状态，支持多个并发任务。
  - 任务进入终态后立即退出后续轮询。
  - 单个 Run 连续查询失败 3 次后暂停自动刷新并提示用户；重试使用退避间隔。
  - effect 清理时取消后续调度，避免组件卸载后继续轮询或更新状态。
- `apps/web/src/api/evaluationRuns.ts`
  - 补齐 `EvaluationRun.execution` 类型，复用现有详情 API。
- `apps/web/src/utils/evaluationRunStatus.ts`
  - 统一终态集合：`PARTIAL_SUCCEEDED`、`SUCCEEDED`、`FAILED`、`CANCELED`。
  - 仅自动追踪具有 `execution.worker_id` 的非终态任务，旧版 `execution` 为空的历史 Run 不进入自动轮询队列。
- `apps/web/tests/evaluationRunStatus.test.mjs`
  - 覆盖全部终态停止规则、失败状态展示和 worker 所有权筛选。
- `apps/api/routes/evaluation_runs.py`
  - API 进程生成稳定的 `EVALUATION_WORKER_ID`。
  - Run 创建后、提交后台线程前先持久化 worker claim。
  - 后台执行或线程提交异常时调用失败收敛逻辑。
- `src/qa_eval/contracts/execution.py`
  - Evaluation Run 增加兼容旧数据的 `execution` 元数据，默认空对象。
- `src/qa_eval/application/services/evaluation_run_service.py`
  - 增加 worker claim、运行心跳、异常失败边界和启动时孤儿任务清扫。
  - Stage 执行异常后保留失败详情，并确保父 Evaluation Run 最终进入 `FAILED` 或 `PARTIAL_SUCCEEDED`。
  - 已进入终态的 Run 不会被迟到的 worker 回写覆盖。
- `apps/api/main.py`
  - API 启动时调用 Evaluation Run 孤儿任务清扫。

本次不需要数据库结构调整或 Alembic migration；执行元数据保存在既有 Run document 中。

### 3. 新的轮询调用链

```text
进入评测结果页
  → GET /api/v1/evaluation-runs（仅加载一次历史列表）
  → 筛选“非终态且具有 worker_id”的 Run
  → 每 3 秒并发 GET /api/v1/evaluation-runs/{run_id}
  → 按 run_id 更新对应列表记录
  → Run 到达终态后从轮询集合移除
  → 所有受追踪 Run 均到达终态后不再产生周期性请求
```

新建任务时，后端响应包含准确的 `run_id` 和 worker 所有权；不再通过创建时间或列表第一条记录推断当前任务。页面刷新后会先读取一次列表，再恢复有明确 worker 所有权的活跃任务追踪。

### 4. 长期 RUNNING 任务排查结论

- Worker 正常执行时会记录 `worker_id`，并在执行边界更新 `heartbeat_at`。
- 后台执行抛出未捕获异常时，会回滚可能已损坏的事务，再以新事务将父 Run 收敛为 `FAILED`。
- API 重启时，会扫描 `PENDING`/`RUNNING` Run：如果其 `worker_id` 属于旧进程，则将 Run 及仍在运行的 Stage Run 收敛为 `FAILED`，错误分类为 `WORKER_INTERRUPTED`。
- `execution` 为空的旧版任务缺少 worker 所有权事实，无法区分“旧版孤儿任务”和“其他进程仍在执行的任务”，因此没有直接批量修改为 `FAILED`。这些任务也不会再触发前端自动轮询。
- 给出的三个 2026-10-29 历史 Run 属于上述缺少 execution 元数据的旧记录，应保留原始事实并由管理员结合进程日志、Stage Run 和数据库记录单独处置。
- 带 `heartbeat_at` 的 Run 可由新的 worker 所有权与启动清扫机制管理；不能仅因前端查询超时就判定后端失败。

### 5. 测试与结果

执行命令：

```bash
cd apps/web
npm test
npm run lint
npm run build

cd ../..
.venv/bin/pytest -q tests/integration/test_evaluation_run_create.py
git diff --check
```

结果：

- 前端状态与追踪规则测试：`3 passed`。
- ESLint：通过。
- TypeScript + Vite 生产构建：通过；仅有既存的大 chunk 体积提示。
- Evaluation Run 创建、异常失败和启动恢复集成测试：`7 passed`。
- `git diff --check`：通过。

### 6. 验收场景对应情况

- A 单任务运行：仅请求对应 Run 详情。
- B/C/D 成功、部分成功、失败：均属于终态，更新状态后停止轮询；失败信息保留在 Run/Stage 错误字段中。
- E 并发任务：按 `run_id` 并发查询和独立合并，互不覆盖。
- F 页面刷新：一次列表查询后恢复具有 worker 所有权的活跃 Run。
- G 历史异常任务：`execution` 为空的旧任务不进入自动轮询，不再导致完整列表无限请求。
- H 组件卸载：清理定时器，已完成请求的结果也会受取消标志保护。
- I 网络异常：短暂错误退避重试；连续 3 次失败暂停该轮询并提示，不将 Run 误标为失败。

### 7. 尚存风险与后续建议

1. 当前心跳在 Stage 执行边界更新；如果单个 Stage 或外部 LLM 请求长时间阻塞，心跳可能长期不变化。后续可增加独立心跳线程或数据库租约，但需要先定义最大执行时长和多实例抢占语义。
2. `execution` 为空的旧任务不会自动修改状态。若需要清理，应提供显式的管理员审计/收敛命令，并要求操作人逐条确认，不能按状态批量失败。
3. 当前多个活跃 Run 使用并发详情请求。若未来单页活跃任务规模显著增长，可再设计只返回指定 ID 集合的批量状态接口；目前无需增加新 API。
4. 最终人工验收仍应打开 DevTools Network：新建任务后只应周期性看到具体 `run_id` 的详情请求；Run 进入终态后该请求应停止。
