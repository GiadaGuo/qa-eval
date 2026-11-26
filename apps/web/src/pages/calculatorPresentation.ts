export const policyFormatPresentation = {
  version: '1.0',
  statistics: {
    input_count: '输入记录',
    compliant_record_count: '合规记录',
    non_compliant_record_count: '不合规记录',
    not_evaluated_count: '未判定记录',
    evaluable_record_count: '已评测记录',
    normative_violation_count: '规范违规记录',
    evaluable_rate: '记录评测覆盖率',
    evaluated_compliance_ratio: '已评测合规率',
    compliant_record_ratio: '合规率',
    normative_violation_rate: '规范违规率',
    document_types: '公文类型分布',
    unknown_reasons: '未判定原因排行',
    rule_outcomes: '规则判定分布',
  } as Record<string, string>,
  formulas: {
    evaluable_rate: ['evaluable_record_count', 'input_count'],
    evaluated_compliance_ratio: ['compliant_record_count', 'evaluable_record_count'],
    compliant_record_ratio: ['compliant_record_count', 'input_count'],
    normative_violation_rate: ['normative_violation_count', 'input_count'],
  } as Record<string, [string, string]>,
}

const reasons: Record<string, string> = {
  TEMPLATE_PATH_UNCONFIRMED: '模板路径未确认',
  BELOW_MIN_COVERAGE: '可判定内容低于最低覆盖要求',
  TYPE_UNCONFIRMED: '公文类型未确认',
  THRESHOLD_STRADDLED: '分数区间跨越判定阈值',
  WEIGHTED_TEMPLATE_DECISION: '公文模板加权判定',
  THRESHOLD_VIOLATED: '实际值未达到阈值',
}

const ruleNames: Record<string, string> = {
  N1: '标题', N2: '发文字号', N3: '成文日期', N4: '附件',
  N5: '规范检查 N5', N6: '规范检查 N6', N7: '规范检查 N7', N8: '版面',
  action: '行动要求', addressee: '主送对象', adopted: '审议通过', appointment: '任免事项',
  attachment_boundary: '附件边界', brief: '摘要', collective: '集体决定', contact: '联系方式',
  decision: '决定事项', deliberation: '审议程序', duties: '职责', effective: '生效时间',
  first_notice: '首次通知', followup: '后续安排', goal: '目标', guide: '办理指引',
  hierarchy: '层级结构', letter_end: '函件结语', optional_formula: '可选程式用语', order: '命令事项',
  parent_basis: '上位依据', position: '职务', procedure: '程序', public_item: '公开事项',
  purpose: '目的', reference_received: '来文依据', reply: '答复', reporting: '报告事项',
  request: '请示事项', scope: '范围', sequencing: '顺序', signoff: '落款',
  subject_title: '主题标题', tasks: '任务', transfer: '转发说明',
}

Object.assign(reasons, {
  BODY_INTEGRITY_UNVERIFIED: '正文完整性未核实', BODY_SIZE_LIMIT: '正文超过处理大小限制',
  DRAFT_OR_INTERPRETATION: '疑似草案或解读', INSUFFICIENT_INDEPENDENT_EVIDENCE: '独立证据不足',
  MANDATORY_REVIEW_PENDING: '待人工复核', MISSING_BODY: '缺少正文',
  REQUIRED_ATTACHMENT_UNRESOLVED: '必要附件未定位', TEMPLATE_NOT_CALIBRATED: '模板尚未校准',
  TYPE_CONFLICT: '公文类型信号冲突', BODY_UNAVAILABLE: '正文不可用', REVIEW_REQUIRED: '需人工复核',
  NO_CORROBORATED_ISSUE_NUMBER: '未找到可印证的发文字号', SIGNED_DATE_NOT_CORROBORATED: '成文日期未获印证',
  ATTACHMENT_PAIR_UNVERIFIED: '附件对应关系未核实', DOCUMENT_SCOPE_UNCONFIRMED: '文档范围未确认',
  PHYSICAL_LAYOUT_UNOBSERVABLE: '版面不可观测', SIGNAL_NOT_LOCATED: '格式信号未定位',
  SOURCE_COORDINATE_UNVERIFIED: '原文位置未核实', ATTACHMENT_BOUNDARY_UNRESOLVED: '附件边界未定位',
  SIGNOFF_NOT_CORROBORATED: '落款未获印证', PASS: '通过', FAIL: '未通过', UNKNOWN: '未知',
  NOT_APPLICABLE: '不适用',
})

export function reasonLabel(code: string): string {
  const [rule, reason] = code.split('/')
  if (reason && ruleNames[rule] && reasons[reason]) return `${ruleNames[rule]} · ${reasons[reason]}`
  return reasons[code] ?? ruleNames[code] ?? code
}

export function formatStatistic(value: unknown, unit?: string | null) {
  if (typeof value !== 'number') return value == null ? '—' : String(value)
  if (unit === 'ratio') return value.toLocaleString('zh-CN', { style: 'percent', maximumFractionDigits: 2, minimumFractionDigits: 2 })
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}
