export type InputSelector = { field_tokens: string[] } | { semantic: string }
export interface ContractInputSlot {
  name: string
  semantic: string
  representation: string
  required: boolean
  selectable?: boolean
}

export type RuleOperator = 'LT' | 'LTE' | 'GT' | 'GTE' | 'EQ' | 'NE'
export type Severity = 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'

export interface QualityContractPublishRequest {
  name: string
  version_label: string
  dataset_version_id: string
  metric_key: string
  metric_version: string
  measurement: string
  calculator_key: string
  calculator_version: string
  parameters: Record<string, unknown>
  mapping_digest?: string
  input_selectors?: Record<string, InputSelector>
  rule: {
    operator: RuleOperator
    threshold: unknown
    severity: Severity
    on_missing: 'UNKNOWN' | 'SKIPPED'
  }
}

export interface PublishedQualityContract {
  target_dataset_version_id: string
  contract: {
    contract_id: string
    contract_version_id: string
    version_label: string
    name: string
    status: 'PUBLISHED'
    fingerprint: string
    published_at: string
    items: Array<{
      key: string
      metric: { key: string; version: string; measurement: string }
      calculation?: { implementation_constraints: { attributes: {
        mapping_digest?: string; input_selectors?: Record<string, InputSelector>
      } } }
      rule: { operator: RuleOperator; threshold: unknown }
    }>
  }
}

async function responseError(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: string }
    if (typeof body.detail === 'string') return body.detail
    if (Array.isArray(body.detail)) return (body.detail as Array<{ msg: string }>).map(item => item.msg).join(' · ')
  } catch {
    // Fall through to the status code.
  }
  return `HTTP ${response.status}`
}

export async function publishQualityContract(
  payload: QualityContractPublishRequest,
): Promise<PublishedQualityContract> {
  const response = await fetch('/api/v1/quality-contract-versions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(`发布质量契约失败：${await responseError(response)}`)
  return response.json() as Promise<PublishedQualityContract>
}

type NativeItem = Pick<QualityContractPublishRequest, 'metric_key' | 'metric_version' | 'calculator_key' | 'calculator_version' | 'parameters'> & {
  key: string
  projections: []
} & (
  { schema_version: '1.0'; strategy: 'OBSERVE_ONLY' }
  | {
    schema_version: '1.1'; strategy: 'EVALUATE'
    statistic_ref: { contract_item_key: string; output_slot_id: string; calculator_version: string; statistics_key: string }
    rule: { operator: RuleOperator; threshold: unknown }
    severity: Severity
  }
)

export async function publishNativeQualityContract(
  identity: Pick<QualityContractPublishRequest, 'name' | 'version_label' | 'dataset_version_id'>,
  item: NativeItem,
): Promise<PublishedQualityContract> {
  const response = await fetch('/api/v1/quality-contract-versions/multi', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...identity, observation_items: [item] }),
  })
  if (!response.ok) throw new Error(`发布质量契约失败：${await responseError(response)}`)
  return response.json()
}

type ContractIdentity = Pick<QualityContractPublishRequest, 'name' | 'version_label' | 'dataset_version_id'>
type ObservationMetricItem = Omit<QualityContractPublishRequest, keyof ContractIdentity | 'rule' | 'calculator_key' | 'calculator_version'> & {
  operator_slot: string
  calculator_key?: string
  calculator_version?: string
  key: string
  rule: { strategy: 'OBSERVE_ONLY'; severity: Severity; on_missing: 'UNKNOWN' | 'SKIPPED' }
}

export async function publishOperatorContract(identity: ContractIdentity, items: ObservationMetricItem[]): Promise<PublishedQualityContract> {
  const response = await fetch('/api/v1/quality-contract-versions/multi', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...identity, items }),
  })
  if (!response.ok) throw new Error(`发布质量契约失败：${await responseError(response)}`)
  return response.json()
}


export async function preflightContractInputs(payload: Pick<QualityContractPublishRequest,
  'dataset_version_id' | 'metric_key' | 'metric_version' | 'calculator_key' | 'calculator_version' |
  'mapping_digest' | 'input_selectors' | 'parameters'>) {
  const response = await fetch('/api/v1/quality-contract-versions/input-preflight', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(`输入预检失败（HTTP ${response.status}）`)
  return await response.json() as {
    status: string; detail?: string; slots?: ContractInputSlot[]
    plan?: { revision_fingerprint: string; slots: Array<{ slot: ContractInputSlot; binding?: {
      field_tokens: string[]; transform?: string | null
    } | null }> }
  }
}


export async function getPublishedQualityContract(id: string): Promise<PublishedQualityContract> {
  const response = await fetch(`/api/v1/quality-contract-versions/${encodeURIComponent(id)}`)
  if (!response.ok) throw new Error(`读取质量契约失败（HTTP ${response.status}）`)
  return await response.json() as PublishedQualityContract
}

export type ContractItemDraft = Omit<QualityContractPublishRequest, keyof ContractIdentity | 'rule'> & {
  key: string
  rule: { strategy: 'THRESHOLD' | 'OBSERVE_ONLY'; operator?: RuleOperator; threshold?: unknown; severity: Severity; on_missing: 'UNKNOWN' }
}
export type ManualContractRequest = ContractIdentity & {
  items: ContractItemDraft[]
  observation_items: NativeItem[]
  excluded_metrics: Record<string, string>
}

export async function prepareQualityContract(payload: ManualContractRequest, publish = false): Promise<PublishedQualityContract> {
  const response = await fetch(`/api/v1/quality-contract-versions/${publish ? 'multi' : 'preview'}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  })
  if (!response.ok) throw new Error(await responseError(response))
  return response.json()
}
