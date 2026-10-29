
# 开发任务：Agent 多会话管理与同一数据集版本下的独立评测

## 一、背景与现有问题

当前项目为 `qa-eval` 数据质量评测平台，前端 React + TypeScript，后端 FastAPI + PostgreSQL。

目前 Agent 对话页面存在一个问题：

对于同一个 Dataset Version，进入 Agent 对话界面后，会持续加载之前的聊天记录，包括：

- 历史用户消息与 Agent 回复
- 历史指标推荐
- 历史 Quality Contract 生成与确认记录
- 历史 Evaluation Run 执行与评测结果

例如当前接口：

`GET /api/v1/agent-sessions/01a181b8-68e0-7098-9c12-2f703a73bf5d`

能够获取某个 Agent Session 的历史状态。

但目前前端缺少清晰的「新建对话」能力，用户无法方便地针对同一个数据集版本开启一次全新的 Agent 评测流程。

**本次目标：实现同一 Dataset Version 下多个独立 Agent Session 的创建、切换、持久化与评测隔离。**

## 二、核心业务模型

请先审查现有 Agent Session、Dataset Version、Quality Contract、Evaluation Run 的数据模型和关联关系。

期望关系为：

```text
Dataset
└── Dataset Version
    ├── Agent Session A
    │   ├── Conversation Messages
    │   ├── Quality Contract A
    │   └── Evaluation Run A
    │
    ├── Agent Session B
    │   ├── Conversation Messages
    │   ├── Quality Contract B
    │   └── Evaluation Run B
    │
    └── Agent Session C
        ├── Conversation Messages
        ├── Quality Contract C
        └── Evaluation Run C
```

要求：

1. 一个 Dataset Version 可以关联多个 Agent Session。
2. 每个 Agent Session 有独立的 Session ID。
3. 每个 Session 有独立的消息历史和 Agent 工作流状态。
4. 每个 Session 可以独立推荐指标、生成质量契约、确认契约、提交评测。
5. 不同 Session 可以使用同一个 Dataset Version 作为评测输入。
6. 不同 Session 的 Quality Contract 和 Evaluation Run 必须可以追溯到对应 Session。
7. 创建新 Session 不得删除、覆盖或重置旧 Session 的历史记录。

注意：Quality Contract 和 Evaluation Run 仍然是平台级业务资源，不需要为了 Agent Session 重新设计一套独立评测引擎。Session 只是保存工作流上下文及关联关系。

## 三、前端交互设计

### 3.1 新建对话

在当前 Agent 对话界面增加明显但不过度突出的「新建对话」按钮。

点击后：

1. 基于当前 Dataset Version 创建新的 Agent Session。
2. 为新 Session 分配唯一 ID。
3. 清空当前界面显示的历史消息。
4. 清空当前 Session 的临时指标推荐、未确认契约、待提交评测状态。
5. 展示初始欢迎消息或默认操作引导。
6. 允许用户重新启动完整的 Agent 评测流程。
7. 不影响旧 Session 的任何持久化记录。

**这里的清空是新 Session 的空白初始状态，不是删除旧 Session 的数据。**

### 3.2 历史对话列表

建议在 Agent 对话区域增加可折叠的会话列表，展示当前 Dataset Version 下的历史 Session。

每个 Session 至少展示：

- 会话标题
- 创建时间
- 最近更新时间
- 当前状态（例如进行中、已完成评测、评测失败）

支持：

- 新建对话
- 切换历史对话
- 恢复对应消息与工作流状态
- 查看该会话产生的契约和评测结果

会话标题初期可以使用「新对话 + 创建时间」，不需要为了自动命名引入额外 LLM 调用。

保持当前前端的森系视觉风格，不要重设计整个应用。

### 3.3 切换会话

切换 Session 时：

- 加载目标 Session 的消息历史。
- 恢复该 Session 的指标推荐、契约状态和评测记录。
- 不得混入其他 Session 的内容。
- 不得重新触发已经完成的评测任务。
- 不得因为切换会话而重复提交 Quality Contract。
- 当前页面刷新后，能够恢复当前 Session，或通过明确规则选择最近会话。

对于正在执行的 Evaluation Run，切换回原 Session 后应能通过既有任务查询机制恢复状态。

## 四、后端 API 与数据模型

请优先复用现有 Agent Session API，不要直接按照以下建议重复创建接口。

检查是否已有：

- 创建 Agent Session
- 根据 Session ID 获取详情
- 根据 Dataset Version 查询 Session 列表
- 获取 Session 消息
- 更新 Session 状态
- 查询 Session 关联的 Quality Contract / Evaluation Run

如缺少必要能力，进行最小扩展。

参考 API 设计：

```http
POST /api/v1/agent-sessions
GET /api/v1/agent-sessions?dataset_version_id=...
GET /api/v1/agent-sessions/{session_id}
```

实际路由、请求体和返回结构以项目现有规范为准。

后端必须保证：

1. 新建 Session 每次生成新的 Session ID，不因 Dataset Version 相同而自动复用旧 Session。
2. 同一个 Dataset Version 可以拥有多个 Session。
3. Session 必须与合法 Dataset Version 关联。
4. Session 的消息、契约和评测结果正确关联。
5. 不允许通过 Session ID 访问其他数据集版本的工作流状态。
6. 并发创建多个 Session 不发生 ID 冲突或状态覆盖。
7. 旧 Session 仍可正常查询。

如果数据库当前存在 Dataset Version 与 Agent Session 的唯一约束，需要检查并通过 Alembic Migration 安全调整。

不得删除已有会话或历史评测数据。

## 五、评测流程隔离

这是本次最重要的业务验收点。

假设 Dataset Version 为 V1。

Session A：

```text
V1
→ Session A
→ Agent 推荐指标
→ Contract A
→ Evaluation Run A
→ Report A
```

用户点击「新建对话」。

Session B：

```text
V1
→ Session B
→ Agent 重新推荐指标
→ Contract B
→ Evaluation Run B
→ Report B
```

要求：

- Session B 不继承 Session A 的聊天消息。
- Session B 不继承 Session A 的待确认契约。
- Session B 不自动复用 Session A 的 Evaluation Run。
- Session B 可以重新推荐相同或不同指标。
- Session B 可以独立确认 Quality Contract。
- Session B 可以重新提交一次 Evaluation Run。
- Session A 和 Session B 的历史结果均保留。
- 平台的数据集版本、指标注册表和 Calculator 不需要重复创建。

注意区分两种行为：

**新建对话：创建新 Session，从头开始 Agent 工作流。**

**重新评测：在当前 Session 或新 Session 中，基于新的评测请求提交 Evaluation Run。**

不能将「新建对话」实现成清空聊天记录后继续复用原 Session ID。

## 六、前端状态管理与异步安全

重点检查：

- 当前 Session ID 的来源
- Dataset Version 切换时的状态更新
- 消息缓存与数据请求 Key
- Agent 推荐结果的本地状态
- Quality Contract 草稿状态
- Evaluation Run 轮询
- 页面刷新后的会话恢复
- 路由参数与 Session ID 的关联

必须避免：

1. 切换 Session 后仍展示上一个 Session 的消息。
2. 旧 Session 的异步响应写入新 Session。
3. 旧 Session 的评测轮询更新新 Session 的状态。
4. 新建 Session 后因自动恢复逻辑再次跳回旧 Session。
5. React 组件状态未重置导致历史契约残留。

可以使用现有请求取消、查询缓存隔离或 Session ID 校验机制解决。

不要引入不必要的新状态管理框架。

## 七、测试与验收

请补充必要的后端和前端测试。

### 后端

- 同一 Dataset Version 连续创建两个 Session。
- 两个 Session ID 不同。
- 两个 Session 消息互不影响。
- 两个 Session 可以分别创建契约。
- 两个 Session 可以分别提交 Evaluation Run。
- 历史 Session 可查询。
- 数据库迁移不会破坏已有 Session。

### 前端

- 点击新建对话后进入空白 Session。
- 历史 Session 列表正确显示。
- 切换 Session 能恢复历史消息。
- 新建对话不会删除旧 Session。
- 页面刷新后状态恢复正确。
- 不同 Session 的契约和评测结果不串联。
- 异步响应和轮询不会跨 Session 更新界面。

### 端到端验收

使用同一个 `jhsjk_article` Dataset Version：

1. 创建 Session A。
2. 完成指标推荐、契约确认与评测。
3. 点击新建对话，创建 Session B。
4. 验证 Session B 没有 Session A 的消息和工作流状态。
5. 在 Session B 重新完成推荐、契约确认与评测。
6. 验证产生新的 Evaluation Run。
7. 切回 Session A，确认历史记录完整。
8. 切回 Session B，确认新评测结果完整。

## 八、并行开发安全约束

当前暂无

## 九、交付要求

请直接完成代码开发、必要的数据库迁移和测试，不要只输出设计方案。

完成后说明：

1. 新建对话入口及交互方式。
2. Session 数据模型与关联关系。
3. 新增或修改的 API。
4. 修改文件清单。
5. 同一 Dataset Version 多 Session 的验证结果。
6. 新旧 Session 的契约与评测隔离方式。
7. 测试命令及结果。
8. 已知限制。

**最终验收标准：同一个数据集的同一个版本，用户可以反复点击「新建对话」，每次获得独立 Agent Session，并重新完成质量契约生成与评测；所有历史对话、契约和评测记录都保留，且可以随时切换查看。**
