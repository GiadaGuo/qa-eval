import { useEffect, useState } from 'react'
import { Alert, Space, Typography } from 'antd'
import { getStages } from '../api/formalDocumentReports'
import type { EvaluationRun } from '../api/evaluationRuns'

export type ReportRun = Pick<EvaluationRun, 'run_id' | 'contract_version_id'> &
  Partial<EvaluationRun>

export function elapsedDuration(run: Pick<ReportRun, 'duration_seconds' | 'elapsed_seconds' | 'elapsed_as_of'>, now: number) {
  if (run.duration_seconds != null) return run.duration_seconds
  if (run.elapsed_seconds == null || !run.elapsed_as_of) return null
  return run.elapsed_seconds + Math.max(0, (now - Date.parse(run.elapsed_as_of)) / 1000)
}

export function formatDuration(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  if (seconds < 1) return `${Number(seconds.toFixed(3))} 秒`
  const whole = Math.floor(seconds)
  const hours = Math.floor(whole / 3600)
  const minutes = Math.floor(whole / 60) % 60
  return [hours ? `${hours} 小时` : '', minutes ? `${minutes} 分` : '', `${whole % 60} 秒`].filter(Boolean).join(' ')
}

export function CopyId({ value }: { value?: string | null }) {
  if (!value) return '—'
  return <Typography.Text title={value} copyable={{ text: value, tooltips: ['复制完整 ID', '已复制'] }}>
    {value.length > 20 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value}
  </Typography.Text>
}

export function RunTarget({ run }: { run: ReportRun }) {
  const version = run.document_version ?? run.dataset_version
  const id = run.document_version_id ?? run.dataset_version_id
  return <Space size={[6, 0]} wrap>
    {run.dataset_name && <Typography.Text strong>{run.dataset_name}</Typography.Text>}
    {run.document_version_id && <span>说明文档</span>}
    {version && <span>{version}</span>}
    <CopyId value={id} />
  </Space>
}

export function RunMetadata({ run }: { run: ReportRun }) {
  return <dl>
    <div><dt>评测对象</dt><dd><RunTarget run={run} /></dd></div>
    <div><dt>评测耗时</dt><dd>{formatDuration(run.duration_seconds)}</dd></div>
    <div><dt>完成时间</dt><dd>{run.finished_at ? new Date(run.finished_at).toLocaleString() : '—'}</dd></div>
    <div><dt>任务 ID</dt><dd><CopyId value={run.run_id} /></dd></div>
    <div><dt>契约版本</dt><dd><CopyId value={run.contract_version_id} /></dd></div>
  </dl>
}

type StageTiming = Awaited<ReturnType<typeof getStages>>[number]

export function RunTimingDetails({ run }: { run: ReportRun }) {
  const [stages, setStages] = useState<StageTiming[]>([])
  const [error, setError] = useState(false)
  useEffect(() => {
    let active = true
    getStages(run.run_id)
      .then(value => { if (active) setStages(value) })
      .catch(() => { if (active) setError(true) })
    return () => { active = false }
  }, [run.run_id, run.finished_at])
  return <Space orientation="vertical">
    {run.queue_duration_seconds != null && <span>排队耗时 {formatDuration(run.queue_duration_seconds)}</span>}
    {stages.filter(stage => stage.duration_seconds != null).map(stage =>
      <span key={stage.stage_run_id}>{stage.stage_id} · 第 {stage.attempt} 次 · {formatDuration(stage.duration_seconds)}</span>)}
    {error && <Alert type="warning" title="阶段耗时读取失败" />}
  </Space>
}
