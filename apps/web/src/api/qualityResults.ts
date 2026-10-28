export interface QualityReport {
  schema_version: '1.0' | '1.1'
  report_id: string
  run_id: string
  dataset_version_id: string
  contract_version_id: string
  execution_status: string
  gate_status: string
  coverage: { evaluated: number; not_applicable: number; missing: number; error: number; not_evaluated: number }
  dimensions: Array<{ key: string; status: string; evaluated_metric_count: number; total_metric_count: number }>
  metrics: Array<{
    metric: { key: string; version: string; name: string }
    metric_result_id: string
    status: string
    /** 契约绑定的主测量；旧报告快照可能缺失该字段 */
    measurement?: { key: string; value: unknown; unit: string | null } | null
    /** 同一次执行附带输出的诊断字段（不含主测量）；旧报告快照可能缺失 */
    diagnostics?: Array<{ key: string; value: unknown; unit: string | null }>
    observations: Array<{ key: string; value: unknown; unit: string | null }>
    rule_evaluations: Array<{ rule_evaluation_id: string; contract_item_key: string; status: string }>
    issue_summary: { confirmed_count: number; candidate_count: number; by_code: Record<string, number>; by_scope: Record<string, number> }
    evidence_ids: string[]
  }>
  issue_summary: { confirmed_count: number; candidate_count: number; open_count: number }
  scoring?: { evaluated_score: number | null; effective_coverage: number; policy: string; included_metrics: string[]; disclaimer: string }
  radar?: Array<{ dimension: string; score: number | null; coverage: number }>
  guide_coverage?: Array<{ metric_key: string; metric_version: string; metric_name: string; parent_metric_key: string | null; status: string; score: number | null; reason: string }>
  recommendations?: string[]
  generated_at: string
}

export interface QualityIssue {
  issue_id: string
  determination: 'CONFIRMED' | 'CANDIDATE'
  severity: string
  code: string
  scope: string
  subject: Record<string, unknown>
  message: string
  evidence: Array<{ evidence_id: string; locator: Record<string, unknown> }>
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`评测结果请求失败：HTTP ${response.status}`)
  return response.json() as Promise<T>
}

/** Report endpoint 404 = 该 Run 已到终态但报告尚未生成（区别于其他请求失败）。 */
export class ReportNotReadyError extends Error {}

export const getQualityReport = async (runId: string): Promise<QualityReport> => {
  const response = await fetch(`/api/v1/evaluation-runs/${runId}/quality-report`)
  if (response.status === 404) throw new ReportNotReadyError('该评测任务的报告尚未生成')
  if (!response.ok) throw new Error(`评测结果请求失败：HTTP ${response.status}`)
  return response.json() as Promise<QualityReport>
}

export const finalizeReport = async (runId: string): Promise<QualityReport> => {
  const response = await fetch(`/api/v1/evaluation-runs/${runId}/finalize`, { method: 'POST' })
  if (!response.ok) throw new Error(`报告生成失败：HTTP ${response.status}`)
  return response.json() as Promise<QualityReport>
}

export interface QualityIssuesPage {
  total: number
  limit: number
  offset: number
  issues: QualityIssue[]
}

export const getQualityIssues = (
  runId: string,
  options?: { determination?: string; metric_result_id?: string; limit?: number; offset?: number },
) => {
  const params = new URLSearchParams({ limit: String(options?.limit ?? 100) })
  if (options?.offset !== undefined) params.set('offset', String(options.offset))
  if (options?.determination) params.set('determination', options.determination)
  if (options?.metric_result_id) params.set('metric_result_id', options.metric_result_id)
  return get<QualityIssuesPage>(`/api/v1/evaluation-runs/${runId}/quality-issues?${params.toString()}`)
}
