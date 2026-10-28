/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-28
 * @Description:
 * P3：说明文档评测任务与报告 API client：创建任务、轮询任务、获取报告。
 */

export type DocumentReportTaskStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'SUCCEEDED'
  | 'PARTIAL_SUCCEEDED'
  | 'FAILED'

export interface DocumentReportTaskMetricProgress {
  metric_code: string
  metric_name: string
  status: 'PENDING' | 'OK' | 'ERROR'
  error?: string | null
}

export interface DocumentReportTask {
  task_id: string
  document_version_id: string
  dataset_id: string
  status: DocumentReportTaskStatus
  error: { phase?: string; message?: string } | null
  report_id: string | null
  metrics: DocumentReportTaskMetricProgress[]
  llm_calls: number
  evaluator_version: string
  standard_version: string
  standard_sha256: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface DocumentEvidence {
  line_start: number
  line_end: number
  excerpt: string
}

export interface DocumentItemSection {
  item_code: string
  item_name: string
  status: 'SATISFIED' | 'INSUFFICIENT' | 'MISSING' | 'UNVERIFIABLE' | 'ERROR'
  document_evidence: DocumentEvidence[]
  inspected_scope: string
  expected_section: string | null
  reason: string
  suggestion: string
  fixability: 'AUTO_FIXABLE' | 'DATA_DERIVABLE' | 'REQUIRES_HUMAN_INPUT'
}

export interface DocumentMetricSection {
  metric: { key: string; version: string }
  metric_name: string
  status: 'OK' | 'ERROR'
  observations: Array<{
    key: string
    value: unknown
    value_type: string
    unit?: string | null
  }>
  items: DocumentItemSection[]
  error?: string | null
}

export interface DocumentQualityReport {
  schema_version: '1.0'
  report_id: string
  task_id: string
  document_version_id: string
  dataset_id: string
  document: {
    version: string
    filename: string
    sha256: string
    line_count: number
    size_bytes: number
  }
  evaluation_standard: {
    name: string
    version: string
    sha256: string
  }
  evaluator: {
    version: string
    model: string
    prompt_version: string
    parameters: Record<string, unknown>
    structured_output_validation: boolean
  }
  execution_status: 'SUCCEEDED' | 'PARTIAL_SUCCEEDED' | 'FAILED'
  metrics: DocumentMetricSection[]
  overall: {
    score: number | null
    scoring_policy: string
    explanation?: string | null
  }
  missing_items: Array<{ item_code: string; item_name: string; expected_section: string }>
  recommendations: Array<{ item_code: string; suggestion: string; fixability: string }>
  limitations: string[]
  generated_at: string
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path)
  if (!response.ok) {
    throw new Error(`请求失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<T>
}

export async function createDocumentReportTask(
  documentVersionId: string,
): Promise<DocumentReportTask> {
  const response = await fetch('/api/v1/document-report-tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_version_id: documentVersionId }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail ?? `创建评测任务失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<DocumentReportTask>
}

export async function listDocumentReportTasks(
  documentVersionId: string,
): Promise<DocumentReportTask[]> {
  return get<DocumentReportTask[]>(
    `/api/v1/document-report-tasks?document_version_id=${encodeURIComponent(documentVersionId)}`,
  )
}

export async function getDocumentReportTask(taskId: string): Promise<DocumentReportTask> {
  return get<DocumentReportTask>(`/api/v1/document-report-tasks/${taskId}`)
}

export async function getDocumentQualityReport(reportId: string): Promise<DocumentQualityReport> {
  return get<DocumentQualityReport>(`/api/v1/document-quality-reports/${reportId}`)
}

export function isTaskFinished(status: DocumentReportTaskStatus): boolean {
  return status === 'SUCCEEDED' || status === 'PARTIAL_SUCCEEDED' || status === 'FAILED'
}

export function taskStatusText(status: DocumentReportTaskStatus): string {
  const map: Record<DocumentReportTaskStatus, string> = {
    PENDING: '等待中',
    RUNNING: '评测中',
    SUCCEEDED: '已完成',
    PARTIAL_SUCCEEDED: '部分完成',
    FAILED: '失败',
  }
  return map[status]
}
