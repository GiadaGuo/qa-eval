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
      rule: { operator: RuleOperator; threshold: unknown }
    }>
  }
}

async function responseError(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: string }
    if (body.detail) return body.detail
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
