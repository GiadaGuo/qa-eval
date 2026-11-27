import { useEffect } from 'react'
import { accountRequest } from './webSession'

export const workLabels = {
  operator: '配置指标算子', ingestion: '继续数据接入', contract: '继续已发布契约', issue: '追溯质量问题',
  dataset: '管理数据集', version: '管理版本', mapping: '配置字段映射',
  agent: '继续 Agent 会话', manual: '配置人工评测', single: '配置单算子评测',
  document: '配置说明文档评测', run: '查看任务', report: '查看报告', 'document-report': '查看说明文档报告',
}

export type WorkKind = keyof typeof workLabels
export type WorkItem = {
  kind: WorkKind
  object_id: string
  touched_at: string
  valid: boolean
  name?: string
  version?: string
  status?: string
  dataset_id?: string
  document_id?: string
  version_id?: string
  run_id?: string
  contract_id?: string
  report_id?: string
  category?: string
}

export type Overview = {
  counts: Record<'datasets' | 'pending' | 'running' | 'reports', number>
  items: WorkItem[]
}

export function recordWork(kind: WorkKind, objectId: string) {
  return accountRequest('workspace/checkpoints', 'PUT', { kind, object_id: objectId })
    .catch(error => console.warn('工作记录保存失败', error))
}

export function useWorkCheckpoint(kind: WorkKind, objectId?: string | null) {
  useEffect(() => {
    if (objectId) void recordWork(kind, objectId)
  }, [kind, objectId])
}

export function workPath(item: WorkItem) {
  const id = item.object_id
  const dataset = '/app/datasets'
  const evaluation = '/app/evaluation'
  switch (item.kind) {
    case 'operator': return evaluation + '?tab=metrics&operatorDraft=' + id
    case 'ingestion': return dataset + '/ingest?version=' + id
    case 'contract': return evaluation + '?version=' + item.version_id + '&mode=manual&contract=' + id + (item.run_id ? '&run=' + item.run_id : '')
    case 'issue': return evaluation + '?tab=results&run=' + item.run_id + '&report=' + item.report_id + '&issue=' + id
    case 'dataset': return dataset + '?dataset=' + id
    case 'version': return dataset + '?rawVersion=' + id
    case 'mapping': return dataset + '?rawVersion=' + id + '&view=mapping'
    case 'single': return dataset + '?rawVersion=' + id + '&view=single'
    case 'document': return dataset + '?dataset=' + item.dataset_id + '&document=' + id
    case 'document-report': return dataset + '?documentReport=' + id
    case 'agent': return evaluation + '?version=' + item.version_id + '&session=' + id
    case 'manual': return evaluation + '?version=' + item.version_id + '&mode=manual'
    case 'run': return evaluation + '?tab=tasks&run=' + id
    case 'report': return evaluation + '?tab=results&run=' + item.run_id + '&report=' + id
  }
}

export type RegistryReference = { key: string; version: string }
export type OperatorDraft = {
  version_id: string
  metric: RegistryReference
  calculator: RegistryReference
  operators: RegistryReference[]
  values: Record<string, unknown>
  mapping_digest?: string | null
  slot_choices: Record<string, string>
  contract_id?: string | null
  run_id?: string | null
}
