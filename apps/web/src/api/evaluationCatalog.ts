import type { EvaluationRun } from './evaluationRuns'

export type EvaluationTarget = {
  dataset_name: string
  dataset_version: string | null
  document_version: string | null
}

export type TaskRow = {
  run_id: string
  name: string
  category: string
  target: EvaluationTarget
  status: EvaluationRun['status']
  settled_stages: number | null
  planned_stages: number | null
  duration_seconds: number | null
  elapsed_seconds: number | null
  elapsed_as_of: string | null
  finished_at: string | null
  report_id: string | null
  actions: Array<'DETAIL' | 'CANCEL' | 'RETRY' | 'REPORT' | 'DELETE'>
}

export type ReportRow = {
  run_id: string
  report_id: string
  name: string
  category: string
  target: EvaluationTarget
  measurements: Array<{ label: string; value: string | number | boolean; unit: string | null }>
  verdict: 'PASS' | 'FAIL' | 'INDETERMINATE'
  generated_at: string
}

export type CatalogPage<Row> = {
  items: Row[]
  total: number
  counts: Record<string, number>
}

export type CatalogQuery = {
  search: string
  category: string
  status: string
  limit: number
  offset: number
  duration_order?: 'asc' | 'desc'
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/v1' + path, init)
  if (!response.ok) {
    const error = await response.json().catch(() => null)
    throw new Error(error?.detail?.message ?? (typeof error?.detail === 'string' ? error.detail : `请求失败（HTTP ${response.status}）`))
  }
  return response.status === 204 ? undefined as T : response.json()
}

function catalog<Row>(path: string, query: CatalogQuery, signal: AbortSignal) {
  const params = new URLSearchParams()
  Object.entries(query).forEach(([key, value]) => { if (value !== '' && value != null) params.set(key, String(value)) })
  return request<CatalogPage<Row>>(`${path}?${params}`, { signal })
}

export const getTaskCatalog = (query: CatalogQuery, signal: AbortSignal) =>
  catalog<TaskRow>('/evaluation-runs/task-catalog', query, signal)

export const getResultCatalog = (query: CatalogQuery, signal: AbortSignal) =>
  catalog<ReportRow>('/quality-reports/catalog', query, signal)

export const cancelTask = (id: string) =>
  request<EvaluationRun>(`/evaluation-runs/${id}/cancel`, { method: 'POST' })

export const retryTask = (id: string) =>
  request<EvaluationRun>(`/evaluation-runs/${id}/retry`, { method: 'POST' })

export const deleteTask = (id: string) =>
  request<void>(`/evaluation-runs/${id}`, { method: 'DELETE' })

export const deleteReport = (id: string) =>
  request<void>(`/quality-reports/${id}`, { method: 'DELETE' })
