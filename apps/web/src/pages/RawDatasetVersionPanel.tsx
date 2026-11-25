import { useEffect, useState } from 'react'
import { Alert, Button, Card, InputNumber, Select, Space, Table, Typography } from 'antd'

import { createEvaluationRun, getEvaluationRun, type EvaluationRun } from '../api/evaluationRuns'
import { publishQualityContract } from '../api/qualityContracts'
import { finalizeReport, getQualityReport, ReportNotReadyError, type QualityReport } from '../api/qualityResults'

export interface RawManifest {
  schema_version: '3.0'
  dataset_version_id: string
  schema_fingerprint: string
}

interface RawPage {
  total: number
  next_cursor: string | null
  samples: Array<{ id: string; data: Record<string, unknown> }>
}

interface MappingEntry {
  digest: string
  revision: { bindings: Array<{ field_tokens: string[]; transform?: string | null }> }
}

export default function RawDatasetVersionPanel({ manifest, onBack }: { manifest: RawManifest; onBack: () => void }) {
  const path = `/api/v1/dataset-versions/${manifest.dataset_version_id}`
  const [page, setPage] = useState<RawPage>()
  const [history, setHistory] = useState<MappingEntry[]>([])
  const [field, setField] = useState<string>()
  const [representation, setRepresentation] = useState('plain')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [cursor, setCursor] = useState<string>()
  const [rev, setRev] = useState<string>()
  const [run, setRun] = useState<EvaluationRun>()
  const [report, setReport] = useState<QualityReport>()
  const [minChars, setMinChars] = useState(50)
  const [threshold, setThreshold] = useState(0.1)
  const [running, setRunning] = useState(false)

  async function evaluate() {
    if (!rev) return
    setRunning(true)
    setReport(undefined)
    setRun(undefined)
    setError('')
    try {
      const contract = await publishQualityContract({
        name: '正文完整度', version_label: new Date().toISOString(),
        dataset_version_id: manifest.dataset_version_id,
        metric_key: '02080104', metric_version: '1.0.0', measurement: 'incomplete_text_ratio',
        calculator_key: 'text_completeness', calculator_version: '1.1.0',
        parameters: { min_char_count: minChars },
        rule: { operator: 'LTE', threshold, severity: 'ERROR', on_missing: 'UNKNOWN' },
      })
      setRun(await createEvaluationRun(contract.contract.contract_version_id, rev))
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '启动评测失败')
    } finally { setRunning(false) }
  }

  async function refreshReport() {
    if (!run) return
    setRunning(true)
    setError('')
    try {
      const current = await getEvaluationRun(run.run_id)
      setRun(current)
      if (current.status === 'SUCCEEDED' || current.status === 'PARTIAL_SUCCEEDED') {
        try { setReport(await getQualityReport(current.run_id)) }
        catch (failure) {
          if (!(failure instanceof ReportNotReadyError)) throw failure
          setReport(await finalizeReport(current.run_id))
        }
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '读取报告失败')
    } finally { setRunning(false) }
  }

  useEffect(() => {
    const controller = new AbortController()
    const params = new URLSearchParams({ limit: '20' })
    if (cursor) params.set('cursor', cursor)
    fetch(`${path}/samples?${params}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error('样本读取失败')
        setPage(await response.json() as RawPage)
      })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [path, cursor])

  useEffect(() => {
    const controller = new AbortController()
    fetch(`${path}/mapping-revisions`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error('修订读取失败')
        setHistory(await response.json() as MappingEntry[])
      })
      .catch((failure: Error) => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [path])

  const fields = [...new Set(page?.samples.flatMap((sample) => Object.keys(sample.data).filter((key) => typeof sample.data[key] === 'string')))]
  const preview = page?.samples.find((sample) => field && typeof sample.data[field] === 'string')?.data
  const suggestion = fields.includes('plain_text_content') ? 'plain_text_content' : fields.find((key) => key === 'content')

  async function confirm() {
    if (field === undefined) return
    setSaving(true)
    setError('')
    try {
      const response = await fetch(`${path}/mapping-revisions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          dataset_version_id: manifest.dataset_version_id,
          schema_fingerprint: manifest.schema_fingerprint,
          bindings: [{
            semantic: 'document.body.plain', field_tokens: [field], representation: 'text/plain',
            ...(representation === 'html' ? { source_representation: 'text/html', transform: 'html_to_plain@1.0.0' } : {}),
          }],
        }),
      })
      if (!response.ok) throw new Error('映射确认失败')
      const entry = await response.json() as MappingEntry
      setHistory((previous) => [entry, ...previous.filter((item) => item.digest !== entry.digest)])
      setRev(entry.digest)
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '映射确认失败')
    } finally {
      setSaving(false)
    }
  }

  return <Card title="原始数据版本" extra={<Button onClick={onBack}>返回</Button>}>
    <Space direction="vertical" style={{ width: '100%' }}>
      {error && <Alert type="error" title={error} />}
      <Typography.Text>正文语义映射</Typography.Text>
      <Space wrap>
        <Select aria-label="正文来源字段" placeholder="选择正文来源字段" style={{ minWidth: 200 }}
          value={field} onChange={setField} options={fields.map((key) => ({ value: key, label: key }))} />
        <Select aria-label="源文本格式" value={representation} onChange={setRepresentation}
          options={[{ value: 'plain', label: '原生纯文本' }, { value: 'html', label: '从 HTML 提取纯文本' }]} />
        {suggestion && <Button onClick={() => {
          setField(suggestion)
          setRepresentation(suggestion === 'plain_text_content' ? 'plain' : 'html')
        }}>采用建议字段</Button>}
        <Button type="primary" disabled={field === undefined} loading={saving} onClick={() => void confirm()}>确认修订</Button>
      </Space>
      {field && preview && <Typography.Paragraph ellipsis={{ rows: 2, expandable: true }}>{String(preview[field])}</Typography.Paragraph>}
      <Select aria-label="已确认的修订" placeholder="已确认的修订" value={rev} onChange={(id) => {
        setRev(id)
        const binding = history.find((entry) => entry.digest === id)?.revision.bindings[0]
        setField(binding?.field_tokens[0])
        setRepresentation(binding?.transform ? 'html' : 'plain')
      }} options={history.map((item) => ({ value: item.digest, label: `${item.revision.bindings[0]?.field_tokens.join(' / ') ?? '空映射'} · ${item.digest.slice(7, 19)}` }))} />
      <Space wrap>
        <label>最小正文字符数 <InputNumber min={1} precision={0} value={minChars} onChange={(value) => { if (value != null) setMinChars(value) }} /></label>
        <label>最大不完整率 <InputNumber min={0} max={1} step={0.05} value={threshold} onChange={(value) => { if (value != null) setThreshold(value) }} /></label>
        <Button disabled={!rev} loading={running} onClick={() => void evaluate()}>发布契约并评测正文完整度</Button>
        {run && <Button loading={running} onClick={() => void refreshReport()}>刷新报告 · {run.status}</Button>}
      </Space>
      {report && <Card size="small" title="质量报告"><pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(report, null, 2)}</pre></Card>}
      <Typography.Text>原始样本 · {page?.total ?? '—'} 条</Typography.Text>
      <Table rowKey="id" size="small" pagination={false} dataSource={page?.samples}
        columns={[{ title: '样本 ID', dataIndex: 'id' }]}
        expandable={{ expandedRowRender: (sample) => <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(sample.data, null, 2)}</pre> }} />
      <Space>
        <Button disabled={!cursor} onClick={() => setCursor(undefined)}>第一页</Button>
        <Button disabled={!page?.next_cursor} onClick={() => setCursor(page?.next_cursor ?? undefined)}>下一页</Button>
      </Space>
    </Space>
  </Card>
}
