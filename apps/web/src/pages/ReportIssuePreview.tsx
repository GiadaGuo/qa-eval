import { useEffect, useState } from 'react'
import { Alert, Button, Card, Collapse, Space, Spin } from 'antd'
import { DocumentCheckCard } from '../components/DocumentMetricDetail'
import type { DocumentCheck, FormalReport } from '../api/formalDocumentReports'
import type { QualityIssue } from '../api/qualityResults'

type Group = { code: string; total: number; issues: QualityIssue[] }

export default function ReportIssuePreview({ runId, issueCount, openIssue, viewSample }: {
  runId: string
  issueCount: number
  openIssue: (issue: QualityIssue) => void
  viewSample: (sampleId: string) => void
}) {
  const [data, setData] = useState<{ total: number; groups: Group[] }>()
  const [error, setError] = useState('')
  useEffect(() => {
    if (!issueCount) return
    const controller = new AbortController()
    fetch(`/api/v1/evaluation-runs/${runId}/quality-issues?preview=true`, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error(`读取问题预览失败（HTTP ${response.status}）`)
        return response.json()
      })
      .then(result => { if (!controller.signal.aborted) setData(result) })
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [runId, issueCount])
  return <Card size="small" title={`问题追溯（${data?.total ?? issueCount}）`}>
    {!issueCount ? '未发现问题' : error ? <Alert type="warning" title={error} /> : data ? <>
      <p>典型问题预览 · 每类最多 3 条，共最多 50 条</p>
      <Collapse size="small" items={data.groups.map(group => ({
        key: group.code, label: `${group.code} · ${group.total} 条`,
        children: group.issues.map(issue => {
          const sampleId = issue.subject.sample_id
          const pair = ([['查看左侧样本', issue.subject.left_sample_id], ['查看右侧样本', issue.subject.right_sample_id]] as const)
            .flatMap(([label, id]) => typeof id === 'string' ? [{ label, id }] : [])
          const members = new Set((issue.related_subjects ?? []).flatMap(({ subject }) =>
            typeof subject.sample_id === 'string' && subject.sample_id !== sampleId ? [subject.sample_id] : []))
          return <div key={issue.issue_id}>
            <p>{issue.message}</p>
            <Space wrap><Button size="small" onClick={() => openIssue(issue)}>查看证据</Button>
              {typeof sampleId === 'string' && <Button size="small" onClick={() => viewSample(sampleId)}>查看完整样本</Button>}
              {pair.map(({ label, id }) => <Button key={label} size="small" onClick={() => viewSample(id)}>{label}</Button>)}
              {!pair.length && Array.from(members).slice(0, 2).map((id, index) =>
                <Button key={id} size="small" onClick={() => viewSample(id)}>查看关联样本 {index + 1}</Button>)}</Space>
          </div>
        }),
      }))} />
      <div className="single-report-print-only">{data.groups.flatMap(group =>
        group.issues.map(issue => <blockquote key={issue.issue_id}>{group.code} · {issue.message}</blockquote>))}</div>
    </> : <Spin />}
  </Card>
}


export function DocumentIssuePreview({ report }: { report: FormalReport }) {
  const checks = report.dimensions.flatMap(dimension => dimension.metrics.flatMap(metric => metric.checks ?? []))
  const findings = checks.filter(check => check.outcome !== 'SATISFIED' && check.outcome !== 'NOT_APPLICABLE')
  const byStatus = new Map<string, DocumentCheck[]>()
  for (const check of findings) {
    const group = byStatus.get(check.outcome) ?? []
    group.push(check)
    byStatus.set(check.outcome, group)
  }
  const labels: Record<string, string> = {
    MISSING: '缺失', INSUFFICIENT: '信息不足', UNVERIFIABLE: '无法核实', ERROR: '判定失败', NOT_EVALUATED: '未评测',
  }
  return <Card size="small" title={`问题追溯（${findings.length}）`}>
    {findings.length ? <Collapse size="small" items={[...byStatus].map(([status, group]) => ({
      key: status,
      label: `${labels[status] ?? status} · ${group.length} 个检查项`,
      children: group.slice(0, 3).map(check => <DocumentCheckCard key={check.item_code} check={check} />),
    }))} /> : '未发现需复核的检查项'}
    <div className="single-report-print-only">{[...(findings.length ? findings : checks)]
      .flatMap(check => check.quotes.slice(0, 3).map((quote, index) =>
        <blockquote key={`${check.item_code}-${index}`}>{check.item_code} · {quote.excerpt}</blockquote>)).slice(0, 50)}</div>
  </Card>
}
