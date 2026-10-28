/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-27
 * @Description:
 * 闭环 2：Dataset / DatasetVersion / Manifest / Profile API client。
 */

export interface Dataset {
  id: string
  name: string
  modality: string[]
  description: string | null
  created_at: string | null
  updated_at: string | null
}

export interface DatasetCreate {
  name: string
  modality: string[]
  description?: string
}

export interface DatasetVersion {
  id: string
  dataset_id: string
  version: string
  status: string
  created_at: string | null
  published_at: string | null
  has_raw_file: boolean
  has_field_mapping: boolean
}

export type SourcePreviewValue = string | number | boolean | null | unknown[] | Record<string, unknown>
export type SourcePreviewRow = Record<string, SourcePreviewValue>

export interface UploadedSourceInspection {
  source_type: 'uploaded_jsonl'
  filename: string
  size_bytes: number | null
  record_count: number
  fields: Array<{
    name: string
    data_type: string
    nullable: boolean
    is_primary_key: boolean
  }>
  rows: SourcePreviewRow[]
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
    details: Record<string, unknown>
  }
  artifacts: Array<{
    artifact_id: string
    kind: string
    uri: string
    format: string
    media_type: string
    schema_ref: string
    size_bytes: number
    record_count: number
    checksum: { algorithm: string; value: string }
  }>
  field_mapping: {
    schema_version: string
    adapter: { key: string; version: string }
    mappings: Array<{
      source_path: string
      action: string
      target: { kind: string; role?: string; modality?: string; name?: string } | null
      value_type: string | null
      presence_policy: string
      required_for_normalization: boolean
    }>
  }
  declared_context: {
    languages: string[]
    task_types: string[]
    attributes: Record<string, unknown>
  }
  created_at: string
}

export interface SampleRecord {
  schema_version: string
  dataset_version_id: string
  sample_id: string
  contents: Array<{
    content_id: string
    modality: string
    role: string
    presence: string
    payload: { type: string; text?: string; format?: string; language?: string } | null
    attributes: Record<string, unknown>
  }>
  annotations: unknown[]
  metadata: Record<string, unknown>
  source: {
    source_artifact_id: string
    source_record_id: string | null
    source_row: number | null
    locator: string | null
    attributes: Record<string, unknown>
  }
  lineage: {
    input_fingerprint: string
    transforms: Array<{ key: string; version: string; config_fingerprint: string }>
  }
}

export interface RejectedRecord {
  schema_version: string
  dataset_version_id: string
  normalization_run_id: string
  source: {
    source_artifact_id: string
    source_row: number | null
    source_record_id: string | null
  }
  error: {
    code: string
    message: string
    json_pointer: string | null
    retryable: boolean
  }
  redacted_excerpt: unknown
  created_at: string
}

export interface DatasetProfile {
  schema_version: string
  profile_id: string
  dataset_version_id: string
  profiler: { key: string; version: string; config_fingerprint: string }
  status: 'OK' | 'ERROR'
  summary: {
    source_record_count: number
    normalized_record_count: number
    rejected_record_count: number
  }
  identity?: { sample_count: number; format: string; size_bytes: number }
  structure?: { modalities: string[]; content_roles: string[]; schema_summary: Record<string, unknown>; sample_granularity: string }
  statistics?: { missingness: Record<string, Record<string, number>>; text_length: Record<string, number | string | null> }
  content_features?: { language_distribution: Record<string, number>; language_source: string }
  application_context: Record<string, unknown>
  profiling_metadata?: { method: string; generated_at: string }
  field_profiles: Array<{
    field: string
    modality: string
    non_null_count?: number
    present_count?: number
    null_count: number
    missing_count?: number
    empty_count?: number
    total_count: number
    content_length?: {
      min: number
      max: number
      mean: number
      median: number
    }
  }>
  artifacts: Array<{
    artifact_id: string
    kind: string
    uri: string
    format: string
    media_type: string
    schema_ref: string
    size_bytes: number
    record_count: number
    checksum: { algorithm: string; value: string }
  }>
  created_at: string
}

export interface PublishResult {
  version_id: string
  status: string
  published_at: string | null
  manifest: DatasetManifest
  profile: DatasetProfile
  normalization_run: {
    schema_version: string
    normalization_run_id: string
    dataset_version_id: string
    adapter: { key: string; version: string; code_digest: string; config_fingerprint: string }
    input_artifact_ids: string[]
    status: string
    counts: { input: number; normalized: number; rejected: number }
    output_artifact_ids: string[]
    started_at: string
    finished_at: string | null
    error: unknown
  }
  counts: { input: number; normalized: number; rejected: number }
}

export interface PaginatedSamples {
  total: number
  limit: number
  offset: number
  samples: SampleRecord[]
}

export interface FieldMappingSpec {
  schema_version: '2.0'
  adapter: { key: string; version: string }
  mappings: Array<{
    source_path: string
    action: 'MAP' | 'PASSTHROUGH' | 'IGNORE'
    target?: { kind: string; name?: string; role?: string; modality?: string }
    value_type?: string
    presence_policy: 'PRESERVE' | 'DROP_NULL' | 'REJECT_MISSING'
    required_for_normalization: boolean
  }>
}

export async function saveFieldMapping(versionId: string, mapping: FieldMappingSpec): Promise<FieldMappingSpec> {
  const response = await fetch(`/api/v1/dataset-versions/${versionId}/field-mapping`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(mapping) })
  if (!response.ok) throw new Error(await responseError(response, '保存字段映射失败'))
  return response.json() as Promise<FieldMappingSpec>
}

export async function inspectUploadedSource(
  versionId: string,
  limit = 5,
  maxTextLength = 300,
): Promise<UploadedSourceInspection> {
  const response = await fetch(
    `/api/v1/dataset-versions/${versionId}/source-inspection?limit=${limit}&max_text_length=${maxTextLength}`,
  )
  if (!response.ok) throw new Error(await responseError(response, '读取已上传 JSONL 失败'))
  return response.json() as Promise<UploadedSourceInspection>
}

export async function listDatasets(): Promise<Dataset[]> {
  const response = await fetch('/api/v1/datasets')
  if (!response.ok) {
    throw new Error(`获取数据集列表失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<Dataset[]>
}

export async function createDataset(payload: DatasetCreate): Promise<Dataset> {
  const response = await fetch('/api/v1/datasets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    throw new Error(`创建数据集失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<Dataset>
}

export async function listDatasetVersions(datasetId: string): Promise<DatasetVersion[]> {
  const response = await fetch(`/api/v1/datasets/${datasetId}/versions`)
  if (!response.ok) {
    throw new Error(`获取版本列表失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<DatasetVersion[]>
}

export async function deleteDataset(datasetId: string): Promise<void> {
  const response = await fetch(`/api/v1/datasets/${datasetId}`, { method: 'DELETE' })
  if (!response.ok) {
    throw new Error(await responseError(response, '删除数据集失败'))
  }
}

export async function deleteDatasetVersion(datasetId: string, versionId: string): Promise<void> {
  const response = await fetch(`/api/v1/datasets/${datasetId}/versions/${versionId}`, { method: 'DELETE' })
  if (!response.ok) {
    throw new Error(await responseError(response, '删除版本失败'))
  }
}

export async function createDatasetVersion(
  datasetId: string,
  versionLabel: string,
): Promise<DatasetVersion> {
  const response = await fetch(`/api/v1/datasets/${datasetId}/versions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version_label: versionLabel }),
  })
  if (!response.ok) {
    throw new Error(`创建版本失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<DatasetVersion>
}

export async function uploadRawFile(
  versionId: string,
  file: File,
): Promise<{ version_id: string; filename: string; uri: string; size_bytes: number; sha256: string }> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`/api/v1/dataset-versions/${versionId}/files`, {
    method: 'POST',
    body: formData,
  })
  if (!response.ok) {
    throw new Error(await responseError(response, '上传文件失败'))
  }
  return response.json() as Promise<{
    version_id: string
    filename: string
    uri: string
    size_bytes: number
    sha256: string
  }>
}

export async function publishVersion(versionId: string): Promise<PublishResult> {
  const response = await fetch(`/api/v1/dataset-versions/${versionId}/publish`, {
    method: 'POST',
  })
  if (!response.ok) {
    throw new Error(await responseError(response, '发布版本失败'))
  }
  return response.json() as Promise<PublishResult>
}

/** 409 冲突响应里依赖键到中文标签的映射。 */
const dependencyLabels: Record<string, string> = {
  physical_evaluation_plans: '评测计划',
  evaluation_runs: '评测运行',
  metric_results: '指标结果',
  quality_reports: '质量报告',
  quality_contract_versions: '质量契约',
  protected_versions: '受保护版本',
}

interface StructuredErrorDetail {
  code?: string
  message?: string
  dependencies?: Record<string, number>
}

function formatStructuredDetail(detail: StructuredErrorDetail): string | null {
  const parts: string[] = []
  if (detail.dependencies) {
    for (const [key, count] of Object.entries(detail.dependencies)) {
      if (!count) continue
      parts.push(`${dependencyLabels[key] ?? key} ${count} 个`)
    }
  }
  if (!detail.message && parts.length === 0) return null
  const summary = parts.length > 0 ? `（${parts.join('、')}）` : ''
  return `${detail.message ?? ''}${summary}`.trim() || null
}

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json() as { detail?: string | StructuredErrorDetail }
    if (typeof payload.detail === 'string' && payload.detail) {
      return `${fallback}：${payload.detail}`
    }
    if (payload.detail && typeof payload.detail === 'object') {
      const formatted = formatStructuredDetail(payload.detail)
      if (formatted) return `${fallback}：${formatted}`
    }
  } catch {
    // Non-JSON error responses fall through to the HTTP status.
  }
  return `${fallback}：HTTP ${response.status}`
}

export async function getDatasetVersion(versionId: string): Promise<DatasetVersion> {
  const response = await fetch(`/api/v1/dataset-versions/${versionId}`)
  if (!response.ok) {
    throw new Error(`获取版本详情失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<DatasetVersion>
}

export async function getManifest(versionId: string): Promise<DatasetManifest> {
  const response = await fetch(`/api/v1/dataset-versions/${versionId}/manifest`)
  if (!response.ok) {
    throw new Error(`获取 Manifest 失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<DatasetManifest>
}

export async function getSamples(
  versionId: string,
  limit = 5,
  offset = 0,
): Promise<PaginatedSamples> {
  const response = await fetch(
    `/api/v1/dataset-versions/${versionId}/samples?limit=${limit}&offset=${offset}`,
  )
  if (!response.ok) {
    throw new Error(`获取样本失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<PaginatedSamples>
}

export async function getSampleById(versionId: string, sampleId: string): Promise<SampleRecord> {
  const response = await fetch(`/api/v1/dataset-versions/${versionId}/samples/${sampleId}`)
  if (!response.ok) {
    throw new Error(await responseError(response, '获取样本原文失败'))
  }
  return response.json() as Promise<SampleRecord>
}

export async function getProfile(versionId: string): Promise<DatasetProfile> {
  const response = await fetch(`/api/v1/dataset-versions/${versionId}/profile`)
  if (!response.ok) {
    throw new Error(`获取 Profile 失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<DatasetProfile>
}
