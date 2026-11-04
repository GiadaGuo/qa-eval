
# Bugfix：修复 Evaluation Proposal 参数校验阻塞与中文可操作错误提示

## 一、任务背景

项目：`qa-eval`

模块：数据集综合质量评测 → Agent 对话 → Evaluation Proposal → 确认并发布多指标 Contract。

当前用户点击「确认并发布多指标 Contract」后，后端返回校验阻塞项：

```text
metric-0201:
Invalid Calculator parameters at $:
Additional properties are not allowed ('content_role' was unexpected)

metric-0203:
Invalid Calculator parameters at $:
Additional properties are not allowed ('content_role' was unexpected)

metric-0207:
Invalid Calculator parameters at $:
'declared_dataset_type' is a required property

Shared inference is missing required metric context
```

目前存在两个问题：

1. Proposal 参数构造、字段绑定与 Calculator Descriptor Schema 不一致，导致用户无法正常发布 Contract。
2. 前端直接展示英文底层校验异常，没有说明具体原因、修复方法和操作入口。

本次任务必须同时修复这两个问题。

**目标不是把英文异常简单翻译成中文，而是消除不合理的系统配置错误，并让真正需要用户补充的信息可以直接在界面上填写。**

## 二、首先定位实际根因

重点检查：

- `src/qa_eval/contracts/proposals.py`
- `src/qa_eval/application/services/evaluation_proposal_service.py`
- `src/qa_eval/application/services/evaluation_recommendation_service.py`
- `src/qa_eval/application/services/logical_planner_service.py`
- `src/qa_eval/calculators/phase2_mvp.py`
- `src/qa_eval/calculators/semantic_inference.py`
- `apps/api/routes/agent_sessions.py`
- `apps/web/src/pages/AgentEvaluationWorkspace.tsx`
- `apps/web/src/api/agentSessions.ts`

以及 Calculator Registry、Descriptor、Parameters Schema、Dataset Binding、Shared Inference Context 的实际实现。

检查以下完整链路：

```text
Agent 推荐
   ↓
Proposal Item 初始化
   ↓
Calculator Parameters
   ↓
Dataset Binding / Content Role
   ↓
Proposal 编辑和保存
   ↓
后端重新校验
   ↓
Contract Item 构造
   ↓
Logical Planner
   ↓
Shared Inference Context
```

重点确定：

- `content_role` 是在哪一层被注入的？
- 为什么 0201 和 0203 都包含这个不允许的字段？
- 0207 的 `declared_dataset_type` 应从哪里输入？
- 0207 的 Shared Inference Context 是否从 Calculator Parameters 正确生成？
- 前端是否能够根据 Descriptor Schema 动态展示必填字段？
- 当前后端是否只返回不可读的英文异常字符串？

先输出简短根因分析，再直接修复。

## 三、修复 0201、0203 的 content_role 错误

当前错误：

```text
Additional properties are not allowed
('content_role' was unexpected)
```

这说明传入 Calculator 的 Parameters 包含 Descriptor Schema 不接受的字段。

### 3.1 正确区分参数和字段绑定

Calculator Parameters 只应包含对应 Calculator Schema 定义的计算参数。

Dataset Binding / Content Role 应由其所属的契约字段或绑定模型承载。

例如，假设现有架构支持：

```json
{
  "calculator_parameters": {},
  "dataset_binding": {
    "body": "plain_text_content"
  }
}
```

则不应额外构造：

```json
{
  "calculator_parameters": {
    "content_role": "body"
  }
}
```

以上只是示意，最终字段结构必须遵循项目现有 canonical Schema。

### 3.2 修复要求

1. 定位公共参数构造函数。
2. 不再将 Dataset Binding 元数据无条件注入 Calculator Parameters。
3. 不允许通过放宽 `additionalProperties: false` 来掩盖错误。
4. 不允许为了消除错误而直接删除 Calculator 实际需要的业务参数。
5. 保证字段绑定在 Proposal → Contract → Planner → Run 全链路正确传递。
6. 如果存在旧 Proposal 草稿包含错误的 `content_role`，提供向后兼容的规范化处理。

旧草稿处理要求：

- 仅迁移已确认属于字段绑定的遗留字段。
- 不得静默丢弃未知参数。
- 无法安全迁移时明确提示用户重新配置。
- 不得破坏已发布 Contract 或历史 Run。

重点验证 0201、0203 不再因 `content_role` 被错误注入而阻塞发布。

## 四、修复 0207 的 declared_dataset_type 缺失

当前错误：

```text
'declared_dataset_type' is a required property

Shared inference is missing required metric context
```

### 4.1 明确参数语义

`declared_dataset_type` 是 0207 类型一致性评测的目标类型声明。

它不是让 LLM 自己推测的数据集类型，而是用户或可信数据集元数据提供的评测依据。

例如，如果数据集声明为新闻文章，模型才可以判断记录内容是否符合这一声明。

### 4.2 前端配置入口

在 0207 的 Evaluation Proposal 指标配置面板中提供：

**数据集声明类型（必填）**

输入方式：

- 如果项目存在正式类型枚举或数据集类型元数据，优先使用下拉选择。
- 如果没有正式枚举，允许用户填写文本。
- 不得凭空创造类型分类标准。
- 如 Dataset Profile 中已有可信声明类型，可预填，但必须允许用户核对。
- 如果只是模型推断的类型，不能自动作为正式声明值。

增加中文说明：

「请选择或填写该数据集声明的内容类型，系统将据此判断样本内容是否与声明类型一致。」

缺失时显示：

「尚未填写数据集声明类型，请展开 0207 指标配置，填写后重新保存。」

### 4.3 Shared Inference Context

修复以下数据传递：

```text
Proposal Parameters
    ↓
Quality Contract
    ↓
Logical Evaluation Plan
    ↓
Shared Inference Metric Context
    ↓
LLM Rubric
```

确保：

- `declared_dataset_type` 正确进入 Calculator Parameters。
- Context 构建能够读取该参数。
- Shared Inference 兼容性指纹包含目标类型。
- 不同声明类型不会被错误合并到同一个共享 Stage。
- 缺少类型时保持 NEEDS_INPUT，而不是生成无效 Stage。

不得通过删除该参数的 required 约束来修复。

## 五、建立中文结构化校验错误协议

目前前端直接展示英文底层异常，用户无法理解。

请在后端增加统一的、机器可读的 Proposal Validation Issue 结构。

优先复用已有错误模型，必要时进行最小兼容扩展。

建议至少包含：

```json
{
  "metric_id": "0207",
  "code": "REQUIRED_PARAMETER_MISSING",
  "field_path": "calculator_parameters.declared_dataset_type",
  "message": "缺少数据集声明类型",
  "suggestion": "请展开 0207 类型一致性配置，填写数据集声明类型后保存",
  "action": "EDIT_CALCULATOR_PARAMETERS",
  "severity": "BLOCKING"
}
```

以上是目标结构示例，实际字段名应遵循项目现有 API 规范。

要求：

1. `code` 使用稳定的机器可读错误码。
2. `metric_id` 明确关联具体指标。
3. `field_path` 能定位具体配置字段。
4. `message` 使用用户可理解的中文。
5. `suggestion` 告诉用户如何解决。
6. `action` 支持前端定位到配置入口。
7. 不把 Python、JSON Schema 或 Pydantic 原始异常直接暴露给普通用户。

建议至少覆盖：

| 错误类型             | 中文提示                                     |
| -------------------- | -------------------------------------------- |
| 缺少必填参数         | 请补充必填配置项                             |
| 多余 Calculator 参数 | 系统生成了不受支持的计算参数，请重新保存配置 |
| 字段绑定缺失         | 请为该指标选择数据字段                       |
| 字段不存在           | 绑定字段不存在，请重新选择                   |
| 字段类型不兼容       | 所选字段类型不符合计算要求                   |
| 阈值缺失             | 请设置指标判定阈值                           |
| 阈值范围错误         | 阈值超出允许范围                             |
| Shared Context 缺失  | 缺少语义评测所需的上下文配置                 |
| Calculator 不可用    | 当前计算算子不可用，请更换或检查运行依赖     |

对于系统自动注入参数造成的错误，应该优先在后端修复，而不是要求用户手动删除内部字段。

## 六、前端提供可操作的阻塞项面板

修改 Evaluation Proposal 的校验提示。

不要继续直接展示：

```text
Invalid Calculator parameters at $...
```

改为类似：

**当前有 1 项需要补充配置**

**0207 类型一致性**

缺少数据集声明类型。

请填写数据集声明的内容类型，用于判断文章内容是否与目标类型一致。

操作按钮：

「前往配置」

点击后：

1. 自动定位到 0207 指标。
2. 展开对应配置面板。
3. 滚动到 `declared_dataset_type` 字段。
4. 聚焦该输入控件（如当前组件支持）。
5. 展示字段级错误提示。
6. 用户修改后可重新保存。
7. 保存成功后重新获取后端校验结果。
8. 阻塞项自动消失。

如果多个指标同时阻塞，应逐项展示。

不要让用户阅读英文异常后自行猜测修改方法。

### 6.1 状态同步

必须保证：

- 编辑配置后重新校验。
- 保存成功后刷新 Proposal Revision。
- 前端显示的 READY 与后端一致。
- 发布使用最新已保存 Revision。
- 旧请求不会覆盖新校验结果。
- 切换 Agent Session 不会混入其他会话的错误状态。

### 6.2 发布按钮

存在真正的阻塞项时：

- 不允许发布无效 Contract。
- 但必须展示可操作的修复入口。

所有已启用指标通过校验后：

- 「确认并发布多指标 Contract」可以正常执行。
- 发布成功后进入既有 Evaluation Run 流程。

不得通过移除 disabled 条件或强制设置 READY 绕过校验。

## 七、避免重复和冲突提示

当前 0207 同时出现：

```text
declared_dataset_type is required
Shared inference is missing required metric context
```

这两个错误可能来自同一根因。

请在后端错误归一化时进行关联与去重。

用户应优先看到：

「0207 类型一致性：缺少数据集声明类型。」

而不是两个互相重复的技术异常。

但需要保留完整的开发诊断信息，方便日志排查。

如果 Shared Context 缺失由其他原因引起，仍应单独报告，不得一概隐藏。

## 八、回归测试

### 8.1 0201 / 0203

验证：

- Calculator Parameters 不包含非法 `content_role`。
- Dataset Binding 正确保留。
- Descriptor 严格校验仍然有效。
- 已有合法参数不丢失。
- 旧 Proposal 可以安全规范化。
- 不能规范化的参数会返回明确错误。

### 8.2 0207

验证：

- 缺少 declared_dataset_type → NEEDS_INPUT。
- 前端显示中文提示。
- 用户填写后 → READY。
- 参数进入 Contract。
- 参数进入 Shared Inference Context。
- 共享分组指纹正确。
- 不同 declared_dataset_type 不会错误共享。

### 8.3 错误提示

验证：

- 结构化错误码稳定。
- 中文提示正确。
- 字段定位正确。
- 重复错误去重。
- 未知异常有安全、可理解的兜底提示。
- 后端保留详细诊断日志。

### 8.4 端到端

使用现有 Fixture 或测试数据库：

1. 生成包含 0201、0203、0207 的多指标 Proposal。
2. 验证 0201、0203 不再产生非法 content_role。
3. 验证 0207 初始 NEEDS_INPUT。
4. 点击「前往配置」。
5. 填写 declared_dataset_type。
6. 保存最新 Proposal Revision。
7. 后端校验通过。
8. 点击「确认并发布多指标 Contract」。
9. 发布成功。
10. Contract 保留正确参数和字段绑定。
11. 可以进入现有 Evaluation Run 流程。

本次不需要调用真实外部 LLM。

## 九、开发边界

本次只修复：

- Proposal 参数构造错误。
- Calculator Parameters 与 Dataset Binding 混淆。
- 0207 必填参数与 Shared Context 传递。
- 后端结构化校验错误。
- 前端中文可操作提示。
- Contract 发布链路回归。

不开发：

- 新 Calculator。
- 新 Agent Runtime。
- 新评分体系。
- 新质量报告。
- 新指标。
- 新 LLM 推理能力。

不得为了通过测试而放宽正式 Calculator Descriptor Schema。

不得修改历史 Evaluation Run。

## 十、交付要求

请直接修复代码，不要只输出分析报告。

最终汇报：

1. 三个错误的实际根因。
2. content_role 的错误注入位置。
3. 0207 参数缺失及 Context 构造问题。
4. 修改文件清单。
5. 后端结构化错误协议。
6. 前端中文提示和「前往配置」交互。
7. Proposal → Contract 发布验证结果。
8. 单元、集成和前端测试结果。
9. 是否完成真实浏览器验证。
10. 剩余问题。

**最终验收标准：**

用户打开数据集综合质量评测 Agent 的 Evaluation Proposal 时：

- 0201、0203 不再因为系统错误注入 `content_role` 而被阻塞。
- 0207 缺少 `declared_dataset_type` 时显示中文、可操作的配置提示。
- 用户可以直接跳转到对应字段并完成配置。
- 保存后后端校验状态正确更新。
- 所有已启用指标配置有效后，可以成功发布多指标 Quality Contract。
- 不再直接向普通用户展示英文底层 Schema 异常。
