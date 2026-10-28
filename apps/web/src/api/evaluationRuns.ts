export interface EvaluationRun {
  run_id: string
  physical_plan_id: string
  dataset_version_id: string
  contract_version_id: string
  status: 'PENDING' | 'RUNNING' | 'PARTIAL_SUCCEEDED' | 'SUCCEEDED' | 'FAILED' | 'CANCEL_REQUESTED' | 'CANCELED'
  input_fingerprint: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: Record<string, unknown> | null
}

export interface MetricResult {
  metric_result_id: string
  run_id: string
  stage_run_id: string
  status: 'OK' | 'NOT_APPLICABLE' | 'MISSING' | 'ERROR'
  metric: { key: string; version: string }
  observations: Array<{ key: string; value: unknown; value_type: string; unit?: string }>
  evidence: Array<{ evidence_id: string; kind: string; artifact: { uri: string; checksum: { value: string } } }>
}

async function getError(response: Response): Promise<string> {
  try {
    const value = await response.json() as { detail?: string | { message?: string } }
    if (typeof value.detail === 'string' && value.detail) return value.detail
    if (value.detail && typeof value.detail === 'object' && 'message' in value.detail && value.detail.message) {
      return value.detail.message
    }
    return `HTTP ${response.status}`
  } catch { return `HTTP ${response.status}` }
}

export async function createEvaluationRun(contractVersionId: string): Promise<EvaluationRun> {
  const response = await fetch('/api/v1/evaluation-runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ contract_version_id: contractVersionId }) })
  if (!response.ok) throw new Error(`创建评测任务失败：${await getError(response)}`)
  return response.json() as Promise<EvaluationRun>
}

export async function listEvaluationRuns(): Promise<EvaluationRun[]> {
  const response = await fetch('/api/v1/evaluation-runs')
  if (!response.ok) throw new Error(`查询评测任务失败：${await getError(response)}`)
  return response.json() as Promise<EvaluationRun[]>
}

export async function getEvaluationRun(runId: string): Promise<EvaluationRun> {
  const response = await fetch(`/api/v1/evaluation-runs/${runId}`)
  if (!response.ok) throw new Error(`查询评测任务失败：${await getError(response)}`)
  return response.json() as Promise<EvaluationRun>
}

export async function getRunMetricResults(runId: string): Promise<MetricResult[]> {
  const response = await fetch(`/api/v1/evaluation-runs/${runId}/metric-results`)
  if (!response.ok) throw new Error(`查询指标结果失败：${await getError(response)}`)
  return response.json() as Promise<MetricResult[]>
}

export async function deleteEvaluationRun(runId: string): Promise<void> {
  const response = await fetch(`/api/v1/evaluation-runs/${runId}`, { method: 'DELETE' })
  if (!response.ok) throw new Error(`删除评测任务失败：${await getError(response)}`)
}
