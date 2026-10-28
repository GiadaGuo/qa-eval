/*
 * @Author: Daphne.Guo
 * @Date: 2026-10-28
 * @Description:
 * P1：说明文档版本（DocumentVersion）API client：上传、列表、元数据、下载、删除。
 */

export interface DocumentVersionInfo {
  id: string
  dataset_id: string
  version: string
  filename: string
  format: string
  size_bytes: number
  sha256: string
  created_at: string | null
}

export interface DocumentVersionMetadata extends DocumentVersionInfo {
  line_count: number | null
  parse_status: 'NOT_PARSED' | 'PARSED'
}

export const DOCUMENT_MAX_SIZE_BYTES = 2 * 1024 * 1024

interface StructuredErrorDetail {
  code?: string
  message?: string
  dependencies?: Record<string, number>
}

const dependencyLabels: Record<string, string> = {
  document_report_tasks: '评测任务',
}

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json() as { detail?: string | StructuredErrorDetail }
    if (typeof payload.detail === 'string' && payload.detail) {
      return `${fallback}：${payload.detail}`
    }
    if (payload.detail && typeof payload.detail === 'object') {
      const parts: string[] = []
      if (payload.detail.dependencies) {
        for (const [key, count] of Object.entries(payload.detail.dependencies)) {
          if (!count) continue
          parts.push(`${dependencyLabels[key] ?? key} ${count} 个`)
        }
      }
      const summary = parts.length > 0 ? `（${parts.join('、')}）` : ''
      const text = `${payload.detail.message ?? ''}${summary}`.trim()
      if (text) return `${fallback}：${text}`
    }
  } catch {
    // Non-JSON error responses fall through to the HTTP status.
  }
  return `${fallback}：HTTP ${response.status}`
}

export async function listDocumentVersions(datasetId: string): Promise<DocumentVersionInfo[]> {
  const response = await fetch(`/api/v1/datasets/${datasetId}/document-versions`)
  if (!response.ok) {
    throw new Error(await responseError(response, '获取说明文档列表失败'))
  }
  return response.json() as Promise<DocumentVersionInfo[]>
}

export async function uploadDocumentVersion(
  datasetId: string,
  file: File,
): Promise<DocumentVersionInfo> {
  const formData = new FormData()
  formData.append('file', file)
  const response = await fetch(`/api/v1/datasets/${datasetId}/document-versions`, {
    method: 'POST',
    body: formData,
  })
  if (!response.ok) {
    throw new Error(await responseError(response, '上传说明文档失败'))
  }
  return response.json() as Promise<DocumentVersionInfo>
}

export async function getDocumentVersionMetadata(
  documentVersionId: string,
): Promise<DocumentVersionMetadata> {
  const response = await fetch(`/api/v1/document-versions/${documentVersionId}`)
  if (!response.ok) {
    throw new Error(await responseError(response, '获取说明文档元数据失败'))
  }
  return response.json() as Promise<DocumentVersionMetadata>
}

export async function downloadDocumentVersion(
  documentVersionId: string,
  filename: string,
): Promise<void> {
  const response = await fetch(`/api/v1/document-versions/${documentVersionId}/download`)
  if (!response.ok) {
    throw new Error(await responseError(response, '下载说明文档失败'))
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

export async function deleteDocumentVersion(documentVersionId: string): Promise<void> {
  const response = await fetch(`/api/v1/document-versions/${documentVersionId}`, {
    method: 'DELETE',
  })
  if (!response.ok) {
    throw new Error(await responseError(response, '删除说明文档失败'))
  }
}
