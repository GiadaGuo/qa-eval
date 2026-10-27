/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-27
 * @Description:
 * 闭环 1：LOCAL_VALIDATION 产物只读 API client。
 */

export interface LocalValidationSummary {
  authority: string
  not_a_platform_run: boolean
  run_dir: string
  dataset_version_id: string
  dataset_name: string
  metric_key: string
  metric_version: string
  contract_version_id: string
  report_id: string
  gate_status: 'PASS' | 'FAIL' | 'UNKNOWN'
  execution_status: string
  issue_summary: {
    confirmed_count: number
    candidate_count: number
    open_count: number
  }
  links: Record<string, string>
}

export interface LocalValidationReportResponse {
  authority: string
  not_a_platform_run: boolean
  report: QualityReport
}

export interface QualityReport {
  schema_version: string
  report_id: string
  run_id: string
  dataset_version_id: string
  contract_version_id: string
  generator: { key: string; version: string }
  execution_status: string
  gate_status: 'PASS' | 'FAIL' | 'UNKNOWN'
  coverage: {
    evaluated: number
    not_applicable: number
    missing: number
    error: number
    not_evaluated: number
  }
  dimensions: ReportDimension[]
  metrics: ReportMetricSection[]
  issue_summary: {
    confirmed_count: number
    candidate_count: number
    open_count: number
  }
  generated_at: string
}

export interface ReportDimension {
  key: string
  status: string
  evaluated_metric_count: number
  total_metric_count: number
}

export interface ReportMetricSection {
  metric: { key: string; version: string; name: string }
  metric_result_id: string
  status: string
  observations: Array<{ key: string; value: number | string; unit: string | null }>
  rule_evaluations: Array<{
    rule_evaluation_id: string
    contract_item_key: string
    status: 'PASS' | 'FAIL' | 'UNKNOWN' | 'SKIPPED'
  }>
  issue_summary: {
    confirmed_count: number
    candidate_count: number
    by_code: Record<string, number>
    by_scope: Record<string, number>
  }
  issue_index_id: string
  evidence_ids: string[]
}

export interface LocalValidationIssuesResponse {
  authority: string
  not_a_platform_run: boolean
  total: number
  limit: number
  offset: number
  issues: QualityIssue[]
}

export interface QualityIssue {
  issue_id: string
  run_id: string
  dataset_version_id: string
  source: {
    metric_result_id: string
    metric_key: string
    metric_version: string
    measurement: string
    rule_evaluation_id: string | null
  }
  detector: { key: string; version: string }
  category: string
  code: string
  determination: 'CONFIRMED' | 'CANDIDATE'
  severity: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
  scope: string
  subject: IssueSubject
  related_subjects: Array<{ relation: string; subject: IssueSubject }>
  message: string
  evidence: Array<{ evidence_id: string; locator: Record<string, unknown> }>
  dedup_key: string
  status: string
  created_at: string
  updated_at: string
}

export type IssueSubject =
  | { type: 'DATASET'; dataset_version_id: string }
  | { type: 'GROUP'; group_id: string; member_count: number }
  | { type: 'SAMPLE'; sample_id: string }
  | { type: 'PAIR'; pair_id: string; left_sample_id: string; right_sample_id: string }
  | { type: 'CONTENT'; sample_id: string; content_id: string }
  | { type: 'ANNOTATION'; sample_id: string; annotation_id: string }

export interface LocalValidationManifestResponse {
  authority: string
  not_a_platform_run: boolean
  manifest: DatasetManifest
}

export interface DatasetManifest {
  schema_version: string
  dataset_id: string
  dataset_version_id: string
  name: string
  modalities: string[]
  source_snapshot: {
    source_type: string
    source_uri: string
    captured_at: string
    consistency: string
    record_count: number
  }
  artifacts: Array<{
    kind: string
    format: string
    record_count: number
    uri: string
    checksum: { algorithm: string; value: string }
  }>
  created_at: string
}

export async function fetchLocalValidationSummary(): Promise<LocalValidationSummary> {
  const response = await fetch('/api/v1/local-validation')
  if (!response.ok) {
    throw new Error(`获取本地验证摘要失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<LocalValidationSummary>
}

export async function fetchLocalValidationReport(): Promise<LocalValidationReportResponse> {
  const response = await fetch('/api/v1/local-validation/report')
  if (!response.ok) {
    throw new Error(`获取质量报告失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<LocalValidationReportResponse>
}

export async function fetchLocalValidationManifest(): Promise<LocalValidationManifestResponse> {
  const response = await fetch('/api/v1/local-validation/manifest')
  if (!response.ok) {
    throw new Error(`获取 Manifest 失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<LocalValidationManifestResponse>
}

export interface IssuesQuery {
  determination?: 'CONFIRMED' | 'CANDIDATE'
  severity?: string
  scope?: string
  code?: string
  limit?: number
  offset?: number
}

export async function fetchLocalValidationIssues(
  query: IssuesQuery = {},
): Promise<LocalValidationIssuesResponse> {
  const params = new URLSearchParams()
  if (query.determination) params.set('determination', query.determination)
  if (query.severity) params.set('severity', query.severity)
  if (query.scope) params.set('scope', query.scope)
  if (query.code) params.set('code', query.code)
  if (query.limit !== undefined) params.set('limit', String(query.limit))
  if (query.offset !== undefined) params.set('offset', String(query.offset))

  const queryString = params.toString()
  const response = await fetch(
    `/api/v1/local-validation/issues${queryString ? `?${queryString}` : ''}`,
  )
  if (!response.ok) {
    throw new Error(`获取问题列表失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<LocalValidationIssuesResponse>
}
