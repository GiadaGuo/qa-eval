import { accountRequest } from '../api/webSession'
import { recordWork, useWorkCheckpoint } from '../api/workspace'
import { useEffect, useState } from 'react'
import { Alert, Button, Card, InputNumber, message, Select, Space } from 'antd'

import { createEvaluationRun, getEvaluationRun, type EvaluationRun } from '../api/evaluationRuns'
import { publishQualityContract } from '../api/qualityContracts'
import { finalizeReport, getQualityReport, ReportNotReadyError, type QualityReport } from '../api/qualityResults'

import type { SemanticRevision } from '../api/datasetVersions'

type SingleDraft = { revision: string | null; minChars: number; threshold: number }

export default function RawVersionEvaluation({ versionId, history, defaultDigest }: {
  versionId: string; history: SemanticRevision[]; defaultDigest: string | null
}) {
  useWorkCheckpoint('single', versionId)
  const [selected, setSelected] = useState<string>()
  const [error, setError] = useState('')
  const rev = selected && history.some(item => item.digest === selected) ? selected : defaultDigest ?? undefined
  const [run, setRun] = useState<EvaluationRun>()
  const [report, setReport] = useState<QualityReport>()
  const [minChars, setMinChars] = useState(50)
  const [threshold, setThreshold] = useState(0.1)
  const [running, setRunning] = useState(false)

  const draftPath = 'workspace/drafts/single/' + versionId
  const [draftReady, setDraftReady] = useState(false)

  useEffect(() => {
    let active = true
    accountRequest<SingleDraft | null>(draftPath).then(saved => {
      if (!active) return
      if (saved) {
        setSelected(saved.revision ?? undefined)
        setMinChars(saved.minChars)
        setThreshold(saved.threshold)
      }
      setDraftReady(true)
    }, (failure: Error) => { if (active) setError(failure.message) })
    return () => { active = false }
  }, [draftPath])

  async function saveDraft() {
    setRunning(true)
    setError('')
    try {
      await accountRequest(draftPath, 'PUT', { revision: rev ?? null, minChars, threshold })
      message.success('单算子配置草稿已保存')
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '保存失败')
    } finally { setRunning(false) }
  }

  async function evaluate() {
    if (!rev) return
    setRunning(true)
    setReport(undefined)
    setRun(undefined)
    setError('')
    try {
      const contract = await publishQualityContract({
        name: '正文完整度', version_label: new Date().toISOString(),
        dataset_version_id: versionId,
        metric_key: '02080104', metric_version: '1.0.0', measurement: 'incomplete_text_ratio',
        calculator_key: 'text_completeness', calculator_version: '1.1.0',
        parameters: { min_char_count: minChars },
        rule: { operator: 'LTE', threshold, severity: 'ERROR', on_missing: 'UNKNOWN' },
      })
      const next = await createEvaluationRun(contract.contract.contract_version_id, rev, 'SINGLE_METRIC')
      setRun(next)
      void recordWork('run', next.run_id)
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
        try {
          const result = await getQualityReport(current.run_id)
          setReport(result)
          void recordWork('report', result.report_id)
        }
        catch (failure) {
          if (!(failure instanceof ReportNotReadyError)) throw failure
          const result = await finalizeReport(current.run_id)
          setReport(result)
          void recordWork('report', result.report_id)
        }
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : '读取报告失败')
    } finally { setRunning(false) }
  }

  return <Space orientation="vertical" style={{ width: '100%' }}>
    {error && <Alert type="error" title={error} />}
    <Select aria-label="评测映射修订" placeholder="选择映射修订" value={rev} disabled={!draftReady} onChange={setSelected}
      style={{ minWidth: 260 }}
      options={history.map(({ digest }) => ({ value: digest, label: digest }))} />
      <Space wrap>
        <label>最小正文字符数 <InputNumber disabled={!draftReady} min={1} precision={0} value={minChars} onChange={(value) => { if (value != null) setMinChars(value) }} /></label>
        <label>最大不完整率 <InputNumber disabled={!draftReady} min={0} max={1} step={0.05} value={threshold} onChange={(value) => { if (value != null) setThreshold(value) }} /></label>
        <Button disabled={!draftReady} loading={running} onClick={() => void saveDraft()}>保存配置草稿</Button>
        <Button disabled={!rev || !draftReady} loading={running} onClick={() => void evaluate()}>发布契约并评测正文完整度</Button>
        {run && <Button loading={running} onClick={() => void refreshReport()}>刷新报告 · {run.status}</Button>}
      </Space>
      {report && <Card size="small" title="质量报告"><pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(report, null, 2)}</pre></Card>}
  </Space>
}
