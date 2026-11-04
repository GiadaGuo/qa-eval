export interface QualityReport {
  schema_version: '1.0' | '1.1'
  report_id: string
  run_id: string
  dataset_version_id: string
  contract_version_id: string
  execution_status: string
  gate_status: string
  coverage: { evaluated: number; partial?: number; not_applicable: number; missing: number; error: number; not_evaluated: number }
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
    stage_run_id?: string | null
    calculator?: { key: string; version: string } | null
    execution_mode?: string | null
    population?: { input_count: number; evaluated_count: number; excluded_count: number } | null
    semantic_coverage?: { level: string; covered: string[]; excluded: string[]; limitations: string[] } | null
    scoring_eligibility?: string[]
    score?: number | null
    scoring_exclusion_reasons?: string[]
  }>
  issue_summary: { confirmed_count: number; candidate_count: number; open_count: number }
  scoring?: {
    overall_score?: number | null
    overall_score_status?: 'COMPLETE' | 'PARTIAL' | 'UNAVAILABLE'
    partial_score?: number | null
    evaluated_score: number | null
    effective_coverage: number
    raw_weight?: number
    applicable_weight?: number
    scored_weight?: number
    unscored_weight?: number
    policy: string
    included_metrics: string[]
    exclusion_reasons?: Record<string, string[]>
    disclaimer: string
  }
  radar?: Array<{ dimension: string; score: number | null; coverage: number }>
  guide_coverage?: Array<{ metric_key: string; metric_version: string; metric_name: string; parent_metric_key: string | null; status: string; score: number | null; reason: string }>
  recommendations?: string[]
  coverage_analysis?: {
    implementation: CoverageRatio
    execution: CoverageRatio
    effective_scoring: CoverageRatio
    observational: CoverageRatio
    data_by_metric: Record<string, CoverageRatio>
    applicability_uncertain: boolean
  } | null
  dimension_scores?: Array<{ dimension: string; score: number | null; effective_weight: number; applicable_weight: number; coverage: number | null; status: string; reasons: string[] }>
  recommendation_details?: Array<{ metric_key: string; issue_code: string | null; certainty: 'DETERMINISTIC' | 'HEURISTIC'; recommendation: string; basis: string }>
  generated_at: string
}

export interface ScoreContribution {
  metric_id: string
  measurement_key: string
  raw_value: number | null
  score: number | null
  sampled_estimate_score: number | null
  contribution_type: 'FULL_RESULT' | 'SAMPLED_ESTIMATE' | 'EXCLUDED'
  weight: number
  scoring_mapping_version: string
  sample_size: number | null
  population_size: number | null
  sampling_method: string | null
  coverage_ratio: number | null
  missing_sampling_metadata: string[]
  exclusion_reasons: string[]
}

export interface OperationalScoringReport {
  report_schema_version: '1.2-mvp'
  scored_report_id: string
  base_report_id: string
  run_id: string
  scoring_profile_id: string
  scoring_profile_version: string
  profile_type: 'MVP_OPERATIONAL' | 'OFFICIAL_GUIDE'
  overall_estimated_score: number | null
  estimated_dimension_coverage: number
  disclaimer: string
  generated_at: string
  radar: Array<{
    dimension: string
    official_score: number | null
    official_coverage: number
    official_status: 'COMPLETE' | 'PARTIAL' | 'UNAVAILABLE'
    estimated_score: number | null
    estimated_coverage: number
    deterministic_score: number | null
    deterministic_coverage: number
    estimate_type: 'FULL_RESULT' | 'SAMPLED_ESTIMATE' | 'MIXED' | 'UNAVAILABLE'
    effective_weight: number
    applicable_weight: number
    sample_size: number | null
    population_size: number | null
    contributing_metrics: ScoreContribution[]
    excluded_metrics: ScoreContribution[]
  }>
}

export interface CoverageRatio {
  numerator: number | null
  denominator: number | null
  ratio: number | null
  basis: string
  unavailable_reason: string | null
}

export interface QualityIssue {
  issue_id: string
  source?: { metric_result_id: string; metric_key: string; metric_version: string; measurement: string; rule_evaluation_id: string | null }
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

export const getOrRebuildOperationalScoring = async (runId: string): Promise<OperationalScoringReport> => {
  const path = `/api/v1/evaluation-runs/${runId}/quality-report/operational-scoring`
  const response = await fetch(path)
  if (response.ok) return response.json() as Promise<OperationalScoringReport>
  if (response.status !== 404) throw new Error(`MVP 评分请求失败：HTTP ${response.status}`)
  const rebuilt = await fetch(`${path}/rebuild`, { method: 'POST' })
  if (!rebuilt.ok) throw new Error(`MVP 评分重建失败：HTTP ${rebuilt.status}`)
  return rebuilt.json() as Promise<OperationalScoringReport>
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
