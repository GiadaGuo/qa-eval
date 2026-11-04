
# Bugfix：修复数据集综合质量评测 Agent 无法确认并发布多指标 Contract

## 一、问题描述

项目：`qa-eval`

模块：数据集综合质量评测 → Agent 对话 → Evaluation Proposal → Quality Contract。

当前用户通过 Agent 对话生成多指标 Evaluation Proposal，并完成质量契约配置后，无法通过：

**「确认并发布多指标 Contract」**

按钮进入后续评测环节。

具体表现需要根据实际代码、浏览器交互和后端日志确定，不能预设为某一种原因。

可能包括：

- 按钮处于 disabled。
- 按钮可点击但没有响应。
- 点击后 API 请求失败。
- Contract 发布成功，但 Agent 对话没有进入下一状态。
- Proposal 状态校验失败。
- 前后端状态不同步。
- 后端已创建 Contract，但前端没有正确更新。
- 请求重复提交或异步状态竞争。

**本次任务要求定位真实根因、修复并完成端到端回归测试。**

不要仅修改按钮样式或绕过校验。

## 二、相关背景

Phase 1 已完成：

- Evaluation Proposal 可编辑配置。
- Calculator Parameters、Dataset Binding、Rule Configuration。
- NEEDS_INPUT 缺失原因诊断。
- Proposal Revision 草稿保存。
- 后端重新校验。
- Proposal 发布 Quality Contract。
- Contract → Evaluation Run。
- Agent Session 隔离。

Phase 2 新增：

- SAFETY_INFERENCE
- AGGREGATE
- OBSERVE_ONLY
- PARTIAL / MISSING / NOT_APPLICABLE
- 六个新的 MVP Calculator。

目前部分指标需要：

- 用户填写阈值。
- 用户补充字段绑定。
- 用户配置 Calculator 参数。
- 用户填写 `declared_dataset_type`。
- 用户确认指标是否启用。

这些配置可能影响 Contract 发布前的 READY 判断。

## 三、首先复现问题

请启动或使用项目现有开发环境，复现以下操作：

1. 打开数据集综合质量评测模块。
2. 选择已有 Dataset Version。
3. 进入 Agent 对话。
4. 生成 Evaluation Proposal。
5. 配置至少两个指标。
6. 点击「确认并发布多指标 Contract」。
7. 观察是否进入 Contract 发布成功及后续评测流程。

请记录：

- 当前 Agent Session ID。
- Proposal Revision ID。
- 启用的 Metric ID。
- 每个指标的 READY / NEEDS_INPUT / INVALID 状态。
- 按钮 disabled 状态及计算条件。
- 点击事件是否触发。
- 浏览器 Network 请求。
- 请求 URL / Method / Payload。
- HTTP 状态码。
- 后端错误详情。
- Contract 是否实际创建。
- Agent Session 状态是否变化。

如果无法启动浏览器，请通过代码、API 集成测试和最小复现脚本定位，不要声称已完成真实浏览器验证。

## 四、重点检查前端

重点检查：

`apps/web/src/pages/AgentEvaluationWorkspace.tsx`

以及关联的：

- Proposal 组件。
- Agent Session API。
- Contract 发布 API。
- React State。
- Mutation / Loading 状态。
- Agent 消息和 Workflow 状态。

### 4.1 按钮状态

找到「确认并发布多指标 Contract」按钮的真实代码。

检查：

- disabled 条件。
- loading 条件。
- onClick 回调。
- 是否被父组件状态限制。
- 是否存在旧状态或闭包引用。
- 是否使用了错误的 Proposal Revision ID。
- 是否因为一个未启用的 NEEDS_INPUT 指标阻止发布。
- 是否因为 OBSERVE_ONLY 没有阈值而错误判定为 INVALID。
- 是否因为 PARTIAL 覆盖范围而错误禁用。
- 是否错误要求所有指标都 READY，而不是所有已启用指标满足发布条件。

要求：

**发布资格必须以当前已启用指标的有效配置为准，不能被未启用指标阻塞。**

### 4.2 配置保存与发布顺序

检查：

用户修改配置
→ 前端 State 更新
→ 保存 Proposal Revision
→ 后端校验
→ 发布 Contract。

重点排查：

- 用户刚修改配置就点击发布，是否提交了旧 Revision？
- 保存草稿是否异步尚未完成？
- 前端展示 READY，但后端仍是 NEEDS_INPUT？
- 保存接口与发布接口是否使用同一个 Revision？
- 是否存在 State 更新延迟？
- 是否在发布前丢失 Calculator Parameters、Dataset Binding 或 Rule Strategy？

修复后应确保发布使用**已经持久化且经过后端校验的最新 Revision**。

不得依赖仅存在于前端的配置状态。

### 4.3 错误反馈

如果无法发布：

- 展示具体阻塞指标。
- 展示具体缺失配置。
- 能直接定位到对应指标配置面板。
- 显示后端返回的可读错误。
- 不允许静默失败。
- 不允许按钮无响应。

对于可修复的 NEEDS_INPUT，用户应能够完成配置后再次发布。

## 五、重点检查后端

重点检查：

- `src/qa_eval/contracts/proposals.py`
- `src/qa_eval/application/services/evaluation_proposal_service.py`
- `src/qa_eval/application/services/evaluation_recommendation_service.py`
- `apps/api/routes/agent_sessions.py`
- Quality Contract 发布 Service。
- Contract Schema。
- Rule Strategy Schema。
- Calculator Descriptor。
- Agent Workflow State。

定位实际发布接口及调用链。

### 5.1 Proposal 校验

检查：

- 是否正确识别 enabled 指标。
- 是否允许未启用指标保持 NEEDS_INPUT。
- 是否要求至少一个启用且有效的指标。
- OBSERVE_ONLY 是否错误要求 threshold。
- THRESHOLD 是否正确校验阈值。
- 0207 是否正确检查 declared_dataset_type。
- DIRECT、SHARED_INFERENCE、SAFETY_INFERENCE、AGGREGATE 是否均被支持。
- PARTIAL 是否错误地被视为不可发布。
- NOT_APPLICABLE 是否按实际契约语义处理。
- Calculator Readiness 与 Proposal Readiness 是否被混淆。

不得通过删除必要校验或强制改成 READY 来修复。

### 5.2 Contract 生成

检查 Proposal → Contract 转换是否完整保留：

- Metric ID / Version。
- Calculator ID / Version。
- Calculator Parameters。
- Dataset Binding。
- Rule Strategy。
- Threshold（仅适用时）。
- Content Role。
- Coverage。
- 启用状态。

确认生成的 Contract 能通过 canonical Schema 和现有 Planner 校验。

### 5.3 发布事务与幂等性

检查：

- 是否在数据库事务中创建 Contract。
- 是否存在唯一约束冲突。
- 是否出现部分提交。
- 重复点击是否创建多个 Contract。
- 同一 Revision 重试是否能正确返回已有发布结果。
- 是否存在已发布 Contract 但响应失败的问题。

修复时保留已有幂等和重复提交保护。

## 六、Agent 工作流状态推进

发布成功后，应正确推进现有工作流：

Evaluation Proposal
→ Quality Contract Published
→ Evaluation Run Ready / Submitted。

具体状态名称和转换条件以现有代码为准。

检查：

1. 后端发布成功后是否返回 Contract ID。
2. 前端是否保存 Contract ID。
3. Agent 对话是否追加成功消息。
4. 是否展示下一步操作入口。
5. 是否正确刷新当前 Session。
6. 是否因为旧的异步响应覆盖新状态。
7. 是否存在发布成功但页面仍停留在 NEEDS_INPUT 的情况。

**不要因为按钮点击就提前显示发布成功，必须以服务端确认结果为准。**

如果当前业务设计是发布 Contract 后自动提交 Run，应保持现有行为。

如果需要用户再次确认运行，则应展示下一步提交入口。

不得自行改变既有工作流语义。

## 七、回归测试

请至少增加以下测试。

### 7.1 前端

- 所有启用指标 READY → 按钮可点击。
- 未启用指标 NEEDS_INPUT → 不阻塞。
- 启用指标 NEEDS_INPUT → 正确提示缺失配置。
- OBSERVE_ONLY 无 threshold → 可发布。
- PARTIAL 但配置完整 → 可发布。
- 用户修改配置后立即点击发布 → 使用最新 Revision。
- 发布 API 返回错误 → 展示错误。
- 发布成功 → 更新 Contract ID 和 Agent 状态。
- 重复点击 → 不重复创建 Contract。
- 切换 Agent Session → 不串用 Revision。

### 7.2 后端

- 多指标 Contract 发布成功。
- 至少一个有效启用指标。
- 未启用指标不参与发布校验。
- THRESHOLD 校验。
- OBSERVE_ONLY 校验。
- 0207 必填参数校验。
- 多种 Execution Mode 共存。
- Contract 参数完整传递。
- 发布事务正确。
- 幂等性正确。

### 7.3 端到端

使用现有 Fixture 或测试数据库，验证：

1. 创建 Agent Session。
2. 生成多指标 Proposal。
3. 配置两个或以上指标。
4. 保存最新 Revision。
5. 发布 Contract。
6. 获得真实 Contract ID。
7. Session 状态更新。
8. 可以进入现有 Evaluation Run 流程。

本次无需调用真实外部 LLM。

不得把 Mock Provider 结果当作真实数据质量评测结果。

## 八、开发边界

本次只修复：

**Evaluation Proposal → 确认并发布多指标 Contract → 后续工作流。**

不开发：

- 新 Calculator。
- 新评分模型。
- 新 Report。
- 新 Agent Runtime。
- 新 Evaluation Proposal 推荐算法。
- 新的前端页面。

允许对相关 API、Schema、Service、前端状态管理进行必要修复。

不得绕过正式参数和规则校验。

不得修改或覆盖历史 Evaluation Run。

## 九、最终交付

请直接定位并修复问题，不要只给排查建议。

最终汇报：

1. 复现步骤。
2. 根因及对应代码位置。
3. 修改文件。
4. 按钮 disabled / onClick 的修复方式。
5. Proposal Revision 保存与发布一致性。
6. Contract 发布 API 验证。
7. 发布成功后的 Agent 工作流状态。
8. 测试结果。
9. 是否完成真实浏览器验证。
10. 仍存在的阻塞项。

**验收标准：用户在数据集综合质量评测 Agent 对话中，配置并确认至少两个有效指标后，能够点击「确认并发布多指标 Contract」，成功发布多指标 Quality Contract，并正确进入后续 Evaluation Run 流程。**

如果存在合法的配置阻塞，必须明确展示具体原因和修复入口，而不是让按钮无法点击或静默失败。
