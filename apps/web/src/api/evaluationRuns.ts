export interface EvaluationRun {
  run_id: string
  physical_plan_id: string
  dataset_version_id?: string
  document_version_id?: string
  contract_version_id: string
  status: 'PENDING' | 'RUNNING' | 'PARTIAL_SUCCEEDED' | 'SUCCEEDED' | 'FAILED' | 'CANCEL_REQUESTED' | 'CANCELED'
  input_fingerprint: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: Record<string, unknown> | null
  execution: Record<string, unknown>
}

export interface MetricResult {
  metric_result_id: string
  run_id: string
  stage_run_id: string
  status: 'OK' | 'PARTIAL' | 'NOT_APPLICABLE' | 'MISSING' | 'ERROR'
  metric: { key: string; version: string }
  status_detail?: { calculator?: { key: string; version: string } }
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

export async function createEvaluationRun(contractVersionId: string, mappingDigest?: string): Promise<EvaluationRun> {
  const response = await fetch('/api/v1/evaluation-runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ contract_version_id: contractVersionId, mapping_digest: mappingDigest }) })
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

export interface CalculatorResult {
  calculator_result_id: string
  contract_item_key: string
  calculator_key: string
  calculator_version: string
  dataset_version_id: string
  status: string
  statistics: Record<string, unknown>
  output_schema: {
    decision_retention: 'NONE' | 'ALL' | 'EXCEPTIONS' | 'SAMPLE'
    statistics: Array<{ key: string; unit: string; population: string; method: string }>
  }
  decision_artifact_refs: Array<{ evidence_id: string; kind: string }>
  evidence_artifact_refs: Array<{ evidence_id: string; kind: string }>
}

export async function getCalculatorResults(runId: string): Promise<CalculatorResult[]> {
  const response = await fetch(`/api/v1/evaluation-runs/${runId}/calculator-results`)
  if (!response.ok) throw new Error('算子观察结果读取失败')
  return response.json()
}
