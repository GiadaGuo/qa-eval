/*
 * 全站中文展示文案的统一映射。
 *
 * 仅做展示层转换：所有函数接收后端原始枚举值（wire value），
 * 返回简体中文标签；未收录的值原样返回，避免后端新增枚举时页面空白。
 * 请求参数、JSON Key 等协议值不经过本模块，保持原样传输。
 */

const labelFrom =
  (map: Record<string, string>) =>
  (value: string | null | undefined): string => {
    if (value == null || value === '') return '—'
    return map[value] ?? value
  }

/** 数据集 / 契约版本状态。 */
export const datasetVersionStatusLabel = labelFrom({
  DRAFT: '草稿',
  PUBLISHED: '已发布',
  RETIRED: '已退役',
})

/** 内容模态。 */
export const modalityLabel = labelFrom({
  text: '文本',
  image: '图像',
  audio: '音频',
  video: '视频',
  structured: '结构化',
})

/** 模态列表的展示格式。 */
export const formatModalityList = (modalities: Array<string | null | undefined>): string =>
  modalities.map(modalityLabel).join('、')

/** 文本格式。 */
export const textFormatLabel = labelFrom({
  plain: '纯文本',
  html: 'HTML',
  markdown: 'Markdown',
  code: '代码',
})

/** 测量值类型。 */
export const valueTypeLabel = labelFrom({
  NUMBER: '数值',
  STRING: '字符串',
  BOOLEAN: '布尔',
  OBJECT: '对象',
})

/** 样本内容存在性（PRESENT/NULL/MISSING）。 */
export const presenceLabel = labelFrom({
  PRESENT: '存在',
  NULL: '空值',
  MISSING: '缺失',
})

/** 字段映射存在策略。 */
export const presencePolicyLabel = labelFrom({
  PRESERVE: '保留原样',
  DROP_NULL: '丢弃空值',
  REJECT_MISSING: '缺失即拒绝',
})

/** 字段映射目标类型。 */
export const mappingTargetKindLabel = labelFrom({
  SAMPLE_ID: '样本 ID',
  CONTENT: '内容',
  METADATA: '元数据',
  TEMPORAL: '时间',
  SOURCE_ATTRIBUTE: '来源属性',
  ANNOTATION: '标注',
})

/** 字段映射动作。 */
export const mappingActionLabel = labelFrom({
  MAP: '映射',
  PASSTHROUGH: '透传',
  IGNORE: '忽略',
})

/** 严重度。 */
export const severityLabel = labelFrom({
  INFO: '提示',
  WARNING: '警告',
  ERROR: '错误',
  CRITICAL: '严重',
})

/** 规则结果缺失策略。 */
export const onMissingLabel = labelFrom({
  UNKNOWN: '记为未知',
  SKIPPED: '跳过',
})

/** 判定运算符（下拉展示）。 */
export const ruleOperatorLabel = labelFrom({
  LT: '< 小于',
  LTE: '≤ 小于等于',
  GT: '> 大于',
  GTE: '≥ 大于等于',
  EQ: '= 等于',
  NE: '≠ 不等于',
})

/** 判定运算符（行内符号）。 */
export const ruleOperatorSymbol = labelFrom({
  LT: '<',
  LTE: '≤',
  GT: '>',
  GTE: '≥',
  EQ: '=',
  NE: '≠',
})

/** 门禁 / 规则判定结果（PASS/FAIL/UNKNOWN/SKIPPED）。 */
export const passFailStatusLabel = labelFrom({
  PASS: '通过',
  FAIL: '未通过',
  UNKNOWN: '未知',
  SKIPPED: '跳过',
})

/** 指标结果状态。 */
export const metricResultStatusLabel = labelFrom({
  OK: '正常',
  PARTIAL: '部分覆盖',
  NOT_APPLICABLE: '不适用',
  MISSING: '缺失',
  ERROR: '错误',
})

/** 评分资格标签。 */
export const scoringEligibilityLabel = labelFrom({
  SCOREABLE: '可评分',
  OBSERVATION_ONLY: '仅观察',
  PARTIAL_COVERAGE: '部分覆盖',
  MISSING: '缺失',
  NOT_APPLICABLE: '不适用',
  ERROR: '错误',
  NOT_EXECUTED: '未执行',
})

/** 综合评分 / 维度评分状态。 */
export const scoreStatusTextLabel = labelFrom({
  COMPLETE: '完整',
  PARTIAL: '部分',
  UNAVAILABLE: '不可用',
})

/** MVP 估计类型。 */
export const estimateTypeLabel = labelFrom({
  FULL_RESULT: '完整结果',
  SAMPLED_ESTIMATE: '抽样估计',
  MIXED: '混合',
  UNAVAILABLE: '不可用',
})

/** 整改建议确定性。 */
export const certaintyLabel = labelFrom({
  DETERMINISTIC: '确定性规则',
  HEURISTIC: '启发式',
})

/** 质量问题判定。 */
export const determinationLabel = labelFrom({
  CONFIRMED: '已确认',
  CANDIDATE: '待确认',
})

/** Agent 会话状态。 */
export const agentSessionStatusLabel = labelFrom({
  ACTIVE: '进行中',
  COMPLETED: '已完成',
  FAILED: '已失败',
})

/** Skill / Tool 调用状态。 */
export const invocationStatusLabel = labelFrom({
  PENDING: '等待中',
  RUNNING: '运行中',
  SUCCEEDED: '已成功',
  FAILED: '失败',
})

/** 指南覆盖状态。 */
export const guideCoverageStatusLabel = labelFrom({
  APPLICABLE: '适用',
  NOT_APPLICABLE: '不适用',
  NEEDS_INPUT: '待补充输入',
  UNAVAILABLE: '暂不可用',
})

/** 指标执行模式。 */
export const executionModeLabel = labelFrom({
  DIRECT: '直接执行',
  SHARED_INFERENCE: '共享推理',
  SAFETY_INFERENCE: '安全推理',
  AGGREGATE: '聚合执行',
})

/** 语义覆盖级别。 */
export const semanticCoverageLevelLabel = labelFrom({
  FULL: '完全覆盖',
  PARTIAL: '部分覆盖',
  UNKNOWN: '未知',
})
