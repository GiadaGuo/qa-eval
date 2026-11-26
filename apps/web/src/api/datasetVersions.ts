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
  data_type?: string | null
  owner?: string | null
  source_kind?: string | null
  storage_path?: string | null
  enabled?: boolean
}

export interface DatasetCreate {
  name: string
  modality: string[]
  description?: string | null
  data_type?: string | null
  owner?: string | null
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
  raw_format?: string | null
  record_schema?: string | null
  record_count?: number | null
  snapshot_at?: string | null
  publication?: FilePublication | null
}

export type SourcePreviewValue = string | number | boolean | null | unknown[] | Record<string, unknown>
export type SourcePreviewRow = Record<string, SourcePreviewValue>

export interface UploadedSourceInspection {
  source_type: 'jsonl_upload' | 'json_upload' | 'parquet_upload' | 'csv_upload'
  format: 'jsonl' | 'json' | 'parquet' | 'csv'
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
  mapping_recommendation: {
    mapping: FieldMappingSpec
    provenance: {
      source: 'MAPPING_TEMPLATE'
      template_id: string
      template_version: string
      match_score: number
    }
    adapter_parameters: Record<string, unknown>
  } | null
  mapping_match: {
    matched: boolean
    reason: string
  }
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

export async function getFieldMapping(versionId: string): Promise<FieldMappingSpec | null> {
  const response = await fetch(`/api/v1/dataset-versions/${versionId}/field-mapping`)
  if (!response.ok) throw new Error(await responseError(response, '读取已保存字段映射失败'))
  return response.json() as Promise<FieldMappingSpec | null>
}

export async function inspectUploadedSource(
  versionId: string,
  limit = 5,
  maxTextLength = 300,
): Promise<UploadedSourceInspection> {
  const response = await fetch(
    `/api/v1/dataset-versions/${versionId}/source-inspection?limit=${limit}&max_text_length=${maxTextLength}`,
  )
  if (!response.ok) throw new Error(await responseError(response, '读取已上传数据文件失败'))
  return response.json() as Promise<UploadedSourceInspection>
}

export async function getDataset(id: string): Promise<Dataset> {
  const response = await fetch(`/api/v1/datasets/${id}`)
  if (!response.ok) throw new Error(await responseError(response, '加载数据集失败'))
  return response.json()
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

export interface DatasetDeletionPreview {
  dataset: { id: string; name: string }
  versions: Array<{ id: string; version: string; status: string }>
  resources: Record<string, number>
  blockers: Array<{ resource: string; count: number; reason: string }>
  can_cascade_delete: boolean
}

export async function getDatasetDeletionPreview(datasetId: string): Promise<DatasetDeletionPreview> {
  const response = await fetch(`/api/v1/datasets/${datasetId}/deletion-preview`)
  if (!response.ok) {
    throw new Error(await responseError(response, '获取删除影响失败'))
  }
  return response.json() as Promise<DatasetDeletionPreview>
}

export async function deleteDataset(datasetId: string, cascade = false): Promise<void> {
  const query = cascade ? '?cascade=true' : ''
  const response = await fetch(`/api/v1/datasets/${datasetId}${query}`, { method: 'DELETE' })
  if (!response.ok) {
    throw new Error(await responseError(response, '删除数据集失败'))
  }
}

export interface VersionDeletionPreview {
  agent_sessions: number
  contracts: Array<{ contract_version_id: string; name: string; version_label: string; status: string }>
  blockers: Record<string, number>
}

export async function previewVersionDeletion(datasetId: string, versionId: string): Promise<VersionDeletionPreview> {
  const response = await fetch(`/api/v1/datasets/${datasetId}/versions/${versionId}/deletion-preview`)
  if (!response.ok) throw new Error(await responseError(response, '读取删除影响失败'))
  return response.json()
}

export async function deleteDatasetVersion(datasetId: string, versionId: string, contractIds: string[] = []): Promise<void> {
  const response = await fetch(`/api/v1/datasets/${datasetId}/versions/${versionId}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ contract_version_ids: contractIds }),
  })
  if (!response.ok) {
    throw new Error(await responseError(response, '删除版本失败'))
  }
}

export async function createDatasetVersion(
  datasetId: string,
  versionLabel?: string,
): Promise<DatasetVersion> {
  const response = await fetch(`/api/v1/datasets/${datasetId}/versions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: versionLabel === undefined ? undefined : JSON.stringify({ version_label: versionLabel }),
  })
  if (!response.ok) {
    throw new Error(`创建版本失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<DatasetVersion>
}

export async function uploadRawFile(
  versionId: string,
  file: File,
  onProgress?: (percent: number) => void,
): Promise<{ version_id: string; filename: string; uri: string; size_bytes: number; sha256: string }> {
  type UploadResult = {
    version_id: string
    filename: string
    uri: string
    size_bytes: number
    sha256: string
  }
  return new Promise<UploadResult>((resolve, reject) => {
    const formData = new FormData()
    formData.append('file', file)
    const request = new XMLHttpRequest()
    request.open('POST', `/api/v1/dataset-versions/${versionId}/files`)
    request.responseType = 'json'
    request.upload.onprogress = (event) => {
      if (!event.lengthComputable || event.total <= 0) return
      // 99% 表示文件字节已发送，最终 1% 留给后端 Schema 校验和 artifact 提交。
      onProgress?.(Math.min(99, Math.round((event.loaded / event.total) * 100)))
    }
    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        onProgress?.(100)
        resolve(request.response as UploadResult)
        return
      }
      const detail = request.response?.detail
      reject(new Error(typeof detail === 'string' ? detail : `上传文件失败：HTTP ${request.status}`))
    }
    request.onerror = () => reject(new Error('上传文件失败：网络连接异常'))
    request.onabort = () => reject(new Error('上传已取消'))
    request.send(formData)
  })
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
  limit = 50,
): Promise<PaginatedSamples> {
  const response = await fetch(
    `/api/v1/dataset-versions/${versionId}/samples?limit=${limit}`,
  )
  if (!response.ok) {
    throw new Error(`获取样本失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<PaginatedSamples>
}

export type SampleById = SampleRecord | {
  id: string
  dataset_version_id: string
  data: Record<string, unknown>
  meta: { artifact_sha256: string; byte_start: number }
}

export async function getSampleById(versionId: string, sampleId: string, signal?: AbortSignal, indexedOnly = false): Promise<SampleById> {
  const response = await fetch(`/api/v1/dataset-versions/${encodeURIComponent(versionId)}/samples/${encodeURIComponent(sampleId)}${indexedOnly ? '?indexed_only=true' : ''}`, { signal })
  if (!response.ok) {
    throw new Error(await responseError(response, '获取样本原文失败'))
  }
  const record: SampleById = await response.json()
  if (record.dataset_version_id !== versionId ||
    ('data' in record ? record.id !== sampleId || !record.data || !record.meta : record.sample_id !== sampleId || !Array.isArray(record.contents))) {
    throw new Error('样本 API 返回的记录与请求不匹配')
  }
  return record
}

export async function getProfile(versionId: string): Promise<DatasetProfile> {
  const response = await fetch(`/api/v1/dataset-versions/${versionId}/profile`)
  if (!response.ok) {
    throw new Error(`获取数据画像失败：HTTP ${response.status}`)
  }
  return response.json() as Promise<DatasetProfile>
}

export async function searchDatasets(params: URLSearchParams, signal: AbortSignal): Promise<{ items: Dataset[]; total: number }> {
  const response = await fetch(`/api/v1/datasets/search?${params}`, { signal })
  if (!response.ok) throw new Error(await responseError(response, '加载数据集失败'))
  return response.json()
}

export async function updateDataset(id: string, payload: DatasetCreate & { enabled: boolean }): Promise<Dataset> {
  const response = await fetch(`/api/v1/datasets/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(await responseError(response, '更新数据集失败'))
  return response.json()
}

export interface FilePublication {
  stage?: string
  status: string
  record_count?: number | null
  reason?: string
}

async function filePublication(id: string, method: 'GET' | 'POST', signal?: AbortSignal, storage?: 'file' | 'sql'): Promise<FilePublication> {
  const response = await fetch(`/api/v1/dataset-versions/${id}/publication`, { method, signal, headers: storage ? { 'Content-Type': 'application/json' } : undefined, body: storage ? JSON.stringify({ storage }) : undefined })
  if (!response.ok) throw new Error(await responseError(response, '读取发布状态失败'))
  return response.json()
}

export const startFilePublication = (id: string, storage?: 'file' | 'sql') => filePublication(id, 'POST', undefined, storage)
export const getFilePublication = (id: string, signal?: AbortSignal) => filePublication(id, 'GET', signal)

export async function applyDefaultFileMapping(id: string): Promise<{ applied: boolean; reason?: string }> {
  const response = await fetch(`/api/v1/dataset-versions/${id}/apply-default-mapping`, { method: 'POST' })
  if (!response.ok) throw new Error(await responseError(response, '复用默认映射失败'))
  return response.json()
}

export interface FileSourceConfig {
  table?: string | null
  engine?: 'mysql' | 'postgresql' | null
  storage: 'file' | 'sql' | null
  fields: UploadedSourceInspection['fields'] | null
  mapping: FieldMappingSpec | null
  reference_version_id: string | null
}

export async function getFileSourceConfig(id: string, signal?: AbortSignal): Promise<FileSourceConfig> {
  const response = await fetch(`/api/v1/datasets/${id}/source-config`, { signal })
  if (!response.ok) throw new Error(await responseError(response, '加载数据源配置失败'))
  return response.json()
}

export async function updateFileSourceConfig(id: string, reference_version_id: string, mapping: FieldMappingSpec): Promise<FileSourceConfig> {
  const response = await fetch(`/api/v1/datasets/${id}/source-config`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reference_version_id, mapping }),
  })
  if (!response.ok) throw new Error(await responseError(response, '更新默认映射失败'))
  return response.json()
}

export async function startDatabaseSnapshot(datasetId: string, table?: string, retryVersionId?: string, connection?: DatabaseConnection): Promise<FilePublication & { version_id: string }> {
  const response = await fetch(`/api/v1/datasets/${datasetId}/database-snapshot`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ table, retry_version_id: retryVersionId, connection }),
  })
  if (!response.ok) throw new Error(await responseError(response, '生成数据库快照失败'))
  return response.json()
}

export interface DatabaseConnection {
  engine: 'mysql' | 'postgresql'
  host: string
  port: number
  database: string
  user: string
  password: string
}

export async function discoverDatabaseSource(connection: DatabaseConnection, table?: string): Promise<{
  tables: string[]; table?: string; preview?: SourcePreviewRow[]; fields?: UploadedSourceInspection['fields']
}> {
  const response = await fetch('/api/v1/database-source/discover', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ connection, table }),
  })
  if (!response.ok) throw new Error(await responseError(response, '数据库探测失败'))
  return response.json()
}


export async function probeSavedDatabase(id: string): Promise<{
  preview: SourcePreviewRow[]; fields: UploadedSourceInspection['fields']
}> {
  const response = await fetch(`/api/v1/datasets/${id}/database-source/preview`)
  if (!response.ok) throw new Error(await responseError(response, '数据库探测失败'))
  return response.json()
}

export type SemanticRepresentation = 'json' | 'text/plain' | 'text/html'

export interface SemanticDefinition {
  key: string
  version: string
  name: string
  description: string
  type: 'string' | 'object' | 'array' | 'number' | 'boolean' | 'any'
  representation: SemanticRepresentation
  cardinality: 'one' | 'many'
}

export interface SemanticBinding {
  semantic: string
  field_tokens: string[]
  representation: SemanticRepresentation
  source_representation?: SemanticRepresentation | null
  transform?: string | null
  definition?: SemanticDefinition
  transform_digest?: string
}

export interface SemanticDraftItem {
  binding: SemanticBinding
  semantic_version?: string | null
  confirmed: boolean
  rationale?: string | null
}

export interface SemanticDraft {
  schema_fingerprint: string
  base_digest: string | null
  default_digest: string | null
  items: SemanticDraftItem[]
}

export interface SemanticRevision {
  digest: string
  revision: { dataset_version_id: string; schema_fingerprint: string; bindings: SemanticBinding[] }
}

export interface SemanticRecommendations {
  schema_fingerprint: string
  fields: Array<UploadedSourceInspection['fields'][number] & { display_name?: string | null }>
  provenance: NonNullable<UploadedSourceInspection['mapping_recommendation']>['provenance'] | null
  items: SemanticDraftItem[]
}

export interface SemanticPreviewSlot {
  name: string
  semantic: string
  representation: SemanticRepresentation
  value_type: SemanticDefinition['type']
  cardinality: SemanticDefinition['cardinality']
}

export interface SemanticPreviewValue {
  raw_value: unknown
  value: unknown
  field_tokens: string[] | null
  representation: SemanticRepresentation
  source_representation: SemanticRepresentation | null
  status: string
  diagnostics: string[]
}

export interface RawSamplePage {
  total: number
  next_cursor: string | null
  samples: Array<{ id: string; data: Record<string, unknown> }>
}

async function versionRequest<T>(id: string, suffix: string, signal?: AbortSignal, method = 'GET', payload?: unknown): Promise<T> {
  const response = await fetch(`/api/v1/dataset-versions/${encodeURIComponent(id)}/${suffix}`, {
    signal, method,
    ...(payload === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  })
  if (!response.ok) throw new Error(await responseError(response, '数据版本请求失败'))
  return await response.json() as T
}

export const getMappingSemantics = (id: string, signal?: AbortSignal) =>
  versionRequest<SemanticDefinition[]>(id, 'mapping/semantics', signal)
export const registerMappingSemantic = (id: string, definition: SemanticDefinition) =>
  versionRequest<SemanticDefinition>(id, 'mapping/semantics', undefined, 'POST', definition)
export const getMappingRecommendations = (id: string, signal?: AbortSignal) =>
  versionRequest<SemanticRecommendations>(id, 'mapping/recommendations', signal)
export const getMappingDraft = (id: string, signal?: AbortSignal) =>
  versionRequest<SemanticDraft>(id, 'mapping/draft', signal)
export const saveMappingDraft = (id: string, payload: { items: SemanticDraftItem[]; confirm_all?: boolean } | { from_digest: string }) =>
  versionRequest<SemanticDraft>(id, 'mapping/draft', undefined, 'PUT', payload)
export const setMappingConfirmation = (id: string, binding: SemanticBinding, confirmed: boolean) =>
  versionRequest<SemanticDraft>(id, 'mapping/draft/confirmation', undefined, 'PUT', {
    semantic: binding.semantic, representation: binding.representation, confirmed,
  })
export const listMappingRevisions = (id: string, offset = 0, signal?: AbortSignal) =>
  versionRequest<SemanticRevision[]>(id, `mapping-revisions?limit=20&offset=${offset}`, signal)
export const getMappingRevision = (id: string, digest: string, signal?: AbortSignal) =>
  versionRequest<SemanticRevision>(id, `mapping-revisions/${encodeURIComponent(digest)}`, signal)
export const previewMapping = (id: string, sampleId: string, slot: SemanticPreviewSlot, digest?: string) =>
  versionRequest<{ sample_id: string; values: Record<string, SemanticPreviewValue> }>(
    id, 'mapping/preview', undefined, 'POST', { sample_id: sampleId, digest, slots: [slot] },
  )
export const publishMappingDraft = (id: string, makeDefault: boolean) =>
  versionRequest<SemanticRevision & { default_digest: string | null }>(
    id, 'mapping/publish', undefined, 'POST', { make_default: makeDefault },
  )
export const setDefaultMappingRevision = (id: string, digest: string | null) =>
  versionRequest<{ default_digest: string | null }>(id, 'mapping/default', undefined, 'PUT', { digest })
export const getRawSamplePage = (id: string, cursor?: string, signal?: AbortSignal) => {
  const params = new URLSearchParams({ limit: '5' })
  if (cursor) params.set('cursor', cursor)
  return versionRequest<RawSamplePage>(id, `samples?${params}`, signal)
}


export const publishRawDatasetVersion = (id: string) =>
  versionRequest<{ manifest: { schema_version: '3.0'; dataset_version_id: string; schema_fingerprint: string } }>(
    id, 'publish?record_schema=3.0', undefined, 'POST',
  )
