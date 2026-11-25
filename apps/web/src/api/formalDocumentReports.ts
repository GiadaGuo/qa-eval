export interface DocumentCheck {
  item_code: string
  outcome: string
  reason: string
  quotes: Array<{ excerpt: string; blocks: Array<{ block_id: string; locator: Record<string, unknown> }> }>
}

export interface FormalReport {
  schema_version: '2.0'
  report_id: string
  run_id: string
  mode: 'DOCUMENT' | 'BASIC'
  document_version_id?: string
  dataset_version_id?: string
  document_snapshot?: { version_id: string; run_id: string; report_id: string }
  dataset_snapshot?: { version_id: string; run_id: string; report_id: string }
  finalized_at: string
  overall_verdict: string
  dimensions: Array<{
    key: string
    score: string | null
    verdict: string
    threshold: number
    reasons: string[]
    metrics: Array<{
      key: string
      score: string | null
      weight: string
      status: string
      satisfied_aspect_count?: number
      required_aspect_count?: number
      checks?: DocumentCheck[]
    }>
  }>
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1${path}`, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => null)
    throw new Error(typeof error?.detail === 'string' ? error.detail : `请求失败（HTTP ${response.status}）`)
  }
  return response.json()
}

export const publishDocumentContract = (documentId: string, metrics = {}) =>
  request<{ contract: { contract_version_id: string } }>('/quality-contract-versions/document',
    { document_version_id: documentId, metrics })

export const listFormalReports = async () =>
  (await request<Array<FormalReport | { schema_version: '1.0' | '1.1' }>>('/quality-reports'))
    .filter((report): report is FormalReport => report.schema_version === '2.0')

export const finalizeDocument = (runId: string) =>
  request<FormalReport>(`/evaluation-runs/${runId}/finalize`, {})

export const getStages = (runId: string) =>
  request<Array<{ stage_id: string; status: string }>>(`/evaluation-runs/${runId}/stage-runs`)

export const combineFormalReports = (runId: string, datasetVersionId: string, documentReportId: string) =>
  request<FormalReport>(`/evaluation-runs/${runId}/combined-quality-report`,
    { dataset_version_id: datasetVersionId, document_report_id: documentReportId, applicability_confirmed: true })
