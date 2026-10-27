import { useEffect, useRef, useState } from 'react'
import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Empty, List, Pagination, Space, Spin, Statistic, Table, Tag, Typography } from 'antd'

import { listEvaluationRuns, type EvaluationRun } from '../api/evaluationRuns'
import { finalizeReport, getQualityIssues, getQualityReport, ReportNotReadyError, type QualityIssue, type QualityReport } from '../api/qualityResults'

const { Title, Text } = Typography
const statusColor = (value: string) => value === 'SUCCEEDED' || value === 'PASS' ? 'success' : value === 'FAILED' || value === 'FAIL' ? 'error' : 'warning'
const ISSUES_PAGE_SIZE = 10

export default function EvaluationResultsPage() {
  const [runs, setRuns] = useState<EvaluationRun[]>([])
  const [selected, setSelected] = useState<EvaluationRun | null>(null)
  const [report, setReport] = useState<QualityReport | null>(null)
  const [issues, setIssues] = useState<QualityIssue[]>([])
  const [issuesPage, setIssuesPage] = useState(1)
  const [issuesTotal, setIssuesTotal] = useState(0)
  const [issuesLoading, setIssuesLoading] = useState(false)
  const [issuesError, setIssuesError] = useState<string | null>(null)
  const issuesRequestId = useRef(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reportPending, setReportPending] = useState(false)
  const [finalizing, setFinalizing] = useState(false)

  const loadRuns = () => {
    setLoading(true); setError(null)
    listEvaluationRuns().then(setRuns).catch(reason => setError(reason instanceof Error ? reason.message : '加载失败')).finally(() => setLoading(false))
  }
  useEffect(() => {
    let cancelled = false
    listEvaluationRuns().then(value => { if (!cancelled) setRuns(value) }).catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : '加载失败') }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const loadIssues = async (run: EvaluationRun, page: number) => {
    const requestId = ++issuesRequestId.current
    setIssuesLoading(true); setIssuesError(null)
    try {
      const body = await getQualityIssues(run.run_id, { limit: ISSUES_PAGE_SIZE, offset: (page - 1) * ISSUES_PAGE_SIZE })
      if (requestId !== issuesRequestId.current) return // 旧响应不覆盖新请求
      setIssues(body.issues); setIssuesTotal(body.total); setIssuesPage(page)
    } catch (reason) {
      if (requestId !== issuesRequestId.current) return
      setIssues([]); setIssuesTotal(0)
      setIssuesError(reason instanceof Error ? reason.message : '质量问题加载失败')
    } finally {
      if (requestId === issuesRequestId.current) setIssuesLoading(false)
    }
  }

  const openRun = async (run: EvaluationRun) => {
    setSelected(run); setLoading(true); setError(null); setReportPending(false); setIssues([]); setIssuesTotal(0); setIssuesError(null)
    // 报告与问题列表独立请求：报告未生成不应连带清空问题列表；问题列表默认第 1 页。
    const reportPromise = getQualityReport(run.run_id).then(setReport)
      .catch(reason => {
        if (reason instanceof ReportNotReadyError) { setReport(null); setReportPending(true) }
        else { setReport(null); setError(reason instanceof Error ? reason.message : '报告读取失败') }
      })
    await Promise.all([reportPromise, loadIssues(run, 1)])
    setLoading(false)
  }

  const generateReport = async (run: EvaluationRun) => {
    setFinalizing(true); setError(null)
    try {
      const nextReport = await finalizeReport(run.run_id)
      setReport(nextReport); setReportPending(false)
      setSelected({ ...run, status: nextReport.execution_status as EvaluationRun['status'] })
      await loadIssues(run, 1)
    } catch (reason) { setError(reason instanceof Error ? reason.message : '报告生成失败') }
    finally { setFinalizing(false) }
  }

  if (selected) return <div style={{ width: '100%', maxWidth: 1440, margin: '0 auto', textAlign: 'left' }}><Space direction="vertical" size="large" style={{ width: '100%' }}>
    <Button icon={<ArrowLeftOutlined />} onClick={() => { setSelected(null); setReport(null); setIssues([]); setIssuesPage(1); setIssuesTotal(0); setIssuesError(null); setError(null); setReportPending(false) }}>返回评测结果</Button>
    <div><Title level={2}>质量报告</Title><Text type="secondary">正式平台 EvaluationRun：{selected.run_id}</Text></div>
    {loading && <Spin />}{error && <Alert type="warning" showIcon title="报告读取失败" description={error} />}
    {reportPending && !loading && <Alert type="info" showIcon title="报告尚未生成" description="该评测任务到达终态时报告生成未完成，可重新生成。"
      action={<Button size="small" type="primary" loading={finalizing} onClick={() => void generateReport(selected)}>生成报告</Button>} />}
    {report && <>
      <Card title="报告概览"><Space wrap size="large"><Statistic title="执行状态" value={report.execution_status} /><Statistic title="质量门禁" value={report.gate_status} /><Statistic title="已评测指标" value={report.coverage.evaluated} /><Statistic title="错误" value={report.coverage.error} /><Statistic title="CONFIRMED" value={report.issue_summary.confirmed_count} /><Statistic title="CANDIDATE" value={report.issue_summary.candidate_count} /></Space></Card>
      {report.metrics.map(metric => <Card key={metric.metric_result_id} title={`${metric.metric.key} · ${metric.metric.name}`} extra={<Tag color={statusColor(metric.status)}>{metric.status}</Tag>}>
        <Table rowKey="key" pagination={false} dataSource={metric.observations} columns={[{ title: 'Measurement', dataIndex: 'key' }, { title: '值', dataIndex: 'value', render: value => String(value) }, { title: '单位', dataIndex: 'unit', render: value => value ?? '—' }]} />
        <Space wrap style={{ marginTop: 16 }}>{metric.rule_evaluations.map(rule => <Tag key={rule.rule_evaluation_id} color={statusColor(rule.status)}>{rule.contract_item_key} · {rule.status}</Tag>)}</Space>
      </Card>)}
      <Card title={`质量问题（共 ${issuesTotal} 条）`}>
        {issuesError
          ? <Alert type="warning" showIcon title="质量问题加载失败" description={issuesError} action={<Button size="small" onClick={() => void loadIssues(selected, issuesPage)}>重试</Button>} />
          : <List loading={issuesLoading} dataSource={issues} locale={{ emptyText: '没有质量问题' }} renderItem={issue => <List.Item><div><Space><Tag color={issue.determination === 'CONFIRMED' ? 'error' : 'warning'}>{issue.determination}</Tag><Tag>{issue.severity}</Tag><Tag>{issue.code}</Tag></Space><div style={{ marginTop: 8 }}><Text strong>{issue.message}</Text></div><div><Text type="secondary">{JSON.stringify(issue.subject)} · evidence: {issue.evidence.map(item => item.evidence_id).join(', ')}</Text></div></div></List.Item>} />}
        {!issuesError && <div style={{ marginTop: 16, textAlign: 'right' }}>
          <Pagination current={issuesPage} pageSize={ISSUES_PAGE_SIZE} total={issuesTotal} showSizeChanger={false} hideOnSinglePage showTotal={total => `共 ${total} 条`} onChange={page => void loadIssues(selected, page)} />
        </div>}
      </Card>
    </>}
  </Space></div>

  return <div style={{ width: '100%', maxWidth: 1440, margin: '0 auto', textAlign: 'left' }}><Space direction="vertical" size="large" style={{ width: '100%' }}>
    <div><Title level={2}>评测结果</Title><Text type="secondary">正式 EvaluationRun、MetricResult、规则判定、质量问题和报告。</Text></div>
    <Button icon={<ReloadOutlined />} onClick={loadRuns}>刷新</Button>
    {error && <Alert type="error" showIcon title="加载失败" description={error} />}
    {loading ? <Spin /> : runs.length === 0 ? <Empty description="暂无正式评测任务" /> : <Table rowKey="run_id" dataSource={runs} columns={[{ title: 'Run ID', dataIndex: 'run_id' }, { title: 'Dataset Version', dataIndex: 'dataset_version_id' }, { title: 'Contract Version', dataIndex: 'contract_version_id' }, { title: '状态', dataIndex: 'status', render: value => <Tag color={statusColor(value)}>{value}</Tag> }, { title: '完成时间', dataIndex: 'finished_at', render: value => value ? new Date(value).toLocaleString() : '—' }, { title: '操作', render: (_, run) => <Button type="link" disabled={['PENDING', 'RUNNING'].includes(run.status)} onClick={() => void openRun(run)}>查看报告</Button> }]} />}
  </Space></div>
}
