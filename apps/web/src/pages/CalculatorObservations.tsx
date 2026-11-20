import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, List, Modal, Space, Tag } from 'antd'

import { getCalculatorResults, type CalculatorResult } from '../api/evaluationRuns'
import { getEvidenceMetadata, rebuildEvidenceIndex } from '../api/qualityResults'

type EvidenceRow = Record<string, unknown>
type EvidencePage = { rows: EvidenceRow[]; next_cursor: string | null }
type EvidenceReference = { id: string; version: string; cursor?: string }
type EvidenceResponse = { reference: EvidenceReference; page?: EvidencePage; error?: string }

export default function CalculatorObservations({ runId, status, viewSample }: {
  runId: string
  status: string
  viewSample: (versionId: string, sampleId: string) => void
}) {
  const [results, setResults] = useState<CalculatorResult[]>([])
  const [error, setError] = useState<string>()
  const [reference, setReference] = useState<EvidenceReference>()
  const [evidence, setEvidence] = useState<EvidenceResponse>()
  const currentEvidence = evidence?.reference === reference ? evidence : undefined
  const page = currentEvidence?.page
  const evidenceError = currentEvidence?.error
  const loading = !!reference && !currentEvidence

  useEffect(() => {
    let active = true
    getCalculatorResults(runId).then(rows => {
      if (active) { setResults(rows); setError(undefined) }
    }).catch(reason => { if (active) setError(reason.message) })
    return () => { active = false }
  }, [runId, status])

  useEffect(() => {
    if (!reference) return
    let active = true
    const load = async () => {
      const metadata = await getEvidenceMetadata(reference.id)
      if (metadata.index.status !== 'READY') await rebuildEvidenceIndex(reference.id)
      const params = new URLSearchParams()
      if (reference.cursor) params.set("cursor", reference.cursor)
      const response = await fetch(`/api/v1/evidence/${reference.id}/rows?${params}`)
      if (!response.ok) throw new Error('证据记录读取失败')
      const data: EvidencePage = await response.json()
      if (active) setEvidence({ reference, page: data })
    }
    load().catch(reason => { if (active) setEvidence({ reference, error: reason.message }) })
    return () => { active = false }
  }, [reference])

  if (error) return <Alert type="warning" title={error} />
  if (!results.length) return null

  return <Card title="算子观察结果">
    <List dataSource={results} rowKey="calculator_result_id" renderItem={result => <List.Item>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Space wrap>
          <strong>{result.calculator_key}@{result.calculator_version}</strong>
          <span>{result.contract_item_key}</span>
          <Tag>{result.status}</Tag>
        </Space>
        <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
          {result.output_schema.statistics.filter(stat => stat.key in result.statistics).map(stat => (
            <Descriptions.Item key={stat.key} label={stat.key}>
              <span title={`${stat.population} · ${stat.method}`}>
                {JSON.stringify(result.statistics[stat.key]) === 'null' ? '—' : JSON.stringify(result.statistics[stat.key])} {stat.unit}
              </span>
            </Descriptions.Item>
          ))}
        </Descriptions>
        <Space wrap>
          {result.decision_artifact_refs.map(ref => <Button key={ref.evidence_id} size="small"
            onClick={() => setReference({ id: ref.evidence_id, version: result.dataset_version_id })}>
            查看判定与原文证据
          </Button>)}
          {result.evidence_artifact_refs.map(ref => <Button key={ref.evidence_id} size="small"
            onClick={() => setReference({ id: ref.evidence_id, version: result.dataset_version_id })}>
            查看证据 · {ref.kind}
          </Button>)}
          {(result.output_schema.decision_retention === 'SAMPLE' || result.output_schema.decision_retention === 'EXCEPTIONS')
            && <Tag>部分保留 · 分母取自统计</Tag>}
        </Space>
      </Space>
    </List.Item>} />
    <Modal open={!!reference} title="算子判定与证据" onCancel={() => setReference(undefined)}
      footer={page?.next_cursor && reference && <Button onClick={() => setReference({
        ...reference, cursor: page.next_cursor ?? undefined,
      })}>下一页</Button>}>
      {evidenceError && <Alert type="warning" title={evidenceError} />}
      <List loading={loading} dataSource={page?.rows ?? []} renderItem={row => <List.Item>
        <Space direction="vertical" style={{ width: '100%' }}>
          <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(row, null, 2)}</div>
          {reference && typeof row.sample_id === 'string' && <Button size="small"
            onClick={() => viewSample(reference.version, String(row.sample_id))}>查看完整样本</Button>}
        </Space>
      </List.Item>} />
    </Modal>
  </Card>
}
