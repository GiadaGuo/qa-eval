import { useSearchParams } from 'react-router-dom'
import { useWorkCheckpoint } from '../api/workspace'
import { useEffect, useState } from 'react'
import { ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Col, Drawer, Input, message, Popconfirm, Row, Select, Space, Spin, Statistic, Table, Tag, Typography } from 'antd'
import {
  cancelTask, deleteReport, deleteTask, getResultCatalog, getTaskCatalog, retryTask,
  type CatalogPage, type CatalogQuery, type EvaluationTarget, type ReportRow, type TaskRow,
} from '../api/evaluationCatalog'
import { getEvaluationRun, type EvaluationRun } from '../api/evaluationRuns'
import { getStages } from '../api/formalDocumentReports'
import { finalizeRunReport } from '../api/qualityResults'
import { evaluationRunStatusLabel, isEvaluationRunTerminal } from '../utils/evaluationRunStatus'
import { measurementLabel, measurementUnitLabel } from '../utils/displayLabels'
import { formatStatistic, policyFormatPresentation } from './calculatorPresentation'
import { elapsedDuration, formatDuration, RunMetadata } from './RunMetadata'
import EvaluationReportDetail from './EvaluationResultsPage'
import ReportExcel from './ReportExcel'

const categories: Record<string, string> = {
  DATASET: '数据集评测', SINGLE_METRIC: '单指标评测', DOCUMENT: '说明文档评测',
  COMBINED: '综合评测', HISTORICAL_UNCLASSIFIED: '历史未分类',
}
const verdicts = { PASS: '达标', FAIL: '未达标', INDETERMINATE: '不可计算' }
const taskStatuses = ['PENDING', 'RUNNING', 'CANCEL_REQUESTED', 'SUCCEEDED', 'PARTIAL_SUCCEEDED', 'FAILED', 'CANCELED']
const taskStats = { all: '全部任务', active: '运行中', completed: '完成', failed: '失败' }
const resultStats = { all: '报告数', passed: '达标', failed: '未达标', indeterminate: '不可计算' }
type ReportLink = { run_id: string; report_id: string }

async function getRestorableTaskCatalog(query: CatalogQuery, signal: AbortSignal) {
  const result = await getTaskCatalog(query, signal)
  // SQLite stores UUID values without separators in the underlying catalog search column.
  if (!result.total && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(query.search)) {
    return getTaskCatalog({ ...query, search: query.search.replaceAll('-', '') }, signal)
  }
  return result
}

function useCatalog<RowType>(
  fetchPage: (query: CatalogQuery, signal: AbortSignal) => Promise<CatalogPage<RowType>>,
  polling = false,
) {
  const [params] = useSearchParams()
  const [query, setQuery] = useState<CatalogQuery>({
    search: polling ? params.get('run') ?? '' : '', category: '',
    status: polling ? params.get('status') ?? '' : '', limit: 10, offset: 0,
  })
  const [page, setPage] = useState<CatalogPage<RowType>>({ items: [], total: 0, counts: {} })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    let timer: number | undefined
    const load = async () => {
      try {
        const next = await fetchPage(query, controller.signal)
        if (controller.signal.aborted) return
        setPage(next)
        setError('')
        if (query.offset && query.offset >= next.total) {
          setQuery(current => ({ ...current, offset: Math.max(0, Math.ceil(next.total / current.limit) - 1) * current.limit }))
        }
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason))
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false)
          if (polling) timer = window.setTimeout(load, 3000)
        }
      }
    }
    void load()
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [fetchPage, query, revision, polling])
  const update = (changes: Partial<CatalogQuery>) => {
    setLoading(true)
    setQuery(previous => ({ ...previous, offset: 0, ...changes }))
  }
  return { query, page, loading, error, update, refresh: () => { setLoading(true); setRevision(value => value + 1) } }
}

function CatalogHeader({ title, stats, counts, query, update, refresh, results }: {
  title: string
  stats: Record<string, string>
  counts: Record<string, number>
  query: CatalogQuery
  update: (changes: Partial<CatalogQuery>) => void
  refresh: () => void
  results?: boolean
}) {
  return <>
    <Typography.Title level={2}>{title}</Typography.Title>
    <Row gutter={[16, 12]}>
      {Object.entries(stats).map(([key, label]) =>
        <Col key={key} xs={12} md={6}><Statistic title={label} value={counts[key] ?? '—'} /></Col>)}
    </Row>
    <Space wrap>
      <Input.Search aria-label="搜索名称、数据集或 ID" placeholder="搜索名称、数据集或 ID" maxLength={200}
        allowClear defaultValue={query.search} onSearch={search => update({ search })} style={{ width: 260 }} />
      <Select aria-label="分类" value={query.category} style={{ width: 155 }} onChange={category => update({ category })}
        options={[{ value: '', label: '全部分类' }, ...Object.entries(categories)
          .filter(([value]) => results || value !== 'COMBINED').map(([value, label]) => ({ value, label }))]} />
      <Select aria-label={results ? '质量结论' : '执行状态'} value={query.status} style={{ width: 145 }} onChange={status => update({ status })}
        options={[{ value: '', label: results ? '全部结论' : '全部状态' },
          ...(results ? Object.entries(verdicts) : taskStatuses.map(status => [status, evaluationRunStatusLabel(status)]))
            .map(([value, label]) => ({ value, label }))]} />
      <Button icon={<ReloadOutlined />} onClick={refresh}>刷新</Button>
    </Space>
  </>
}

function TargetCell({ target }: { target: EvaluationTarget }) {
  return <Space orientation="vertical" size={2}>
    <Typography.Text strong>{target.dataset_name}</Typography.Text>
    <Typography.Text type="secondary">{[
      target.dataset_version && `数据 ${target.dataset_version}`,
      target.document_version && `文档 ${target.document_version}`,
    ].filter(Boolean).join(' · ') || '—'}</Typography.Text>
  </Space>
}

function NameCell({ name, category }: { name: string; category: string }) {
  return <Space orientation="vertical" size={4}><Typography.Text strong>{name}</Typography.Text>
    <Tag>{categories[category] ?? category}</Tag></Space>
}

function TaskDetails({ task, onClose, onReport, onFinalized }: {
  task: TaskRow
  onClose: () => void
  onReport: (link: ReportLink) => void
  onFinalized: () => void
}) {
  const [run, setRun] = useState<EvaluationRun>()
  const [stages, setStages] = useState<Awaited<ReturnType<typeof getStages>>>([])
  const [error, setError] = useState('')
  const [finalizing, setFinalizing] = useState(false)
  useEffect(() => {
    let active = true
    let timer: number | undefined
    const load = async () => {
      const [runResult, stageResult] = await Promise.allSettled([
        getEvaluationRun(task.run_id), getStages(task.run_id),
      ] as const)
      if (!active) return
      if (runResult.status === 'fulfilled') setRun(runResult.value)
      if (stageResult.status === 'fulfilled') setStages(stageResult.value)
      setError(runResult.status === 'rejected' ? String(runResult.reason)
        : stageResult.status === 'rejected' ? String(stageResult.reason) : '')
      const status = runResult.status === 'fulfilled' ? runResult.value.status : task.status
      if (!isEvaluationRunTerminal(status)) timer = window.setTimeout(load, 3000)
    }
    void load()
    return () => { active = false; window.clearTimeout(timer) }
  }, [task.run_id, task.status])
  const finalize = async () => {
    setFinalizing(true)
    try {
      const report = await finalizeRunReport(task.run_id)
      onFinalized()
      onReport({ run_id: task.run_id, report_id: report.report_id })
    } catch (reason) {
      setError(String(reason))
    } finally {
      setFinalizing(false)
    }
  }
  return <Drawer title={task.name} open onClose={onClose} size="large">
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Tag>{evaluationRunStatusLabel(run?.status ?? task.status)}</Tag>
      {error && <Alert type="error" showIcon title="任务详情" description={error} />}
      {!run && !error && <Spin />}
      {run && <div className="single-report-header"><RunMetadata run={run} /></div>}
      {run?.error && <Alert type="error" title="失败原因" showIcon description={
        <Space orientation="vertical" size={0}>{['message', 'error_category', 'phase', 'failed_stage_id', 'type']
          .flatMap(key => typeof run.error?.[key] === 'string' ? [<span key={key}>{String(run.error[key])}</span>] : [])}</Space>
      } />}
      {run?.queue_duration_seconds != null && <Typography.Text>排队耗时 {formatDuration(run.queue_duration_seconds)}</Typography.Text>}
      {!!stages.length && <Table size="small" rowKey="stage_run_id" dataSource={stages} pagination={{ pageSize: 8 }} columns={[
        { title: '执行阶段', render: (_, stage) => stage.calculator?.key || stage.stage_id },
        { title: '尝试', dataIndex: 'attempt' },
        { title: '状态', render: (_, stage) => evaluationRunStatusLabel(stage.status) },
        { title: '已处理', render: (_, stage) => typeof stage.stats?.processed === 'number' ? stage.stats.processed : '—' },
        { title: '耗时', render: (_, stage) => formatDuration(stage.duration_seconds) },
        { title: '失败原因', render: (_, stage) => stage.error?.message ?? stage.error?.type ?? '—' },
      ]} />}
      {task.report_id
        ? <Button onClick={() => onReport({ run_id: task.run_id, report_id: task.report_id! })}>查看报告</Button>
        : ['SUCCEEDED', 'PARTIAL_SUCCEEDED'].includes(run?.status ?? '') && !run?.execution.native_observation_only &&
          <Button loading={finalizing} onClick={() => void finalize()}>生成报告</Button>}
    </Space>
  </Drawer>
}

export function EvaluationTasksPage() {
  const catalog = useCatalog(getRestorableTaskCatalog, true)
  const { page, query, update, refresh } = catalog
  const [report, setReport] = useState<ReportLink>()
  const [detail, setDetail] = useState<TaskRow>()
  const [params] = useSearchParams()
  const requestedRun = params.get('run')
  const requestedTask = page.items.find(item => item.run_id === requestedRun)
  const [openedRequested, setOpenedRequested] = useState(false)
  useEffect(() => {
    if (!requestedTask || openedRequested) return
    setOpenedRequested(true)
    setDetail(requestedTask)
  }, [requestedTask, openedRequested])
  useWorkCheckpoint('run', detail?.run_id)
  const [busy, setBusy] = useState<string>()
  const [now, setNow] = useState(Date.now)
  const hasElapsed = page.items.some(task => task.elapsed_seconds != null)
  useEffect(() => {
    if (!hasElapsed) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [hasElapsed])
  const act = async (id: string, action: (id: string) => Promise<unknown>, success: string) => {
    setBusy(id)
    try { await action(id); message.success(success); refresh() }
    catch (reason) { message.error(String(reason)); refresh() }
    finally { setBusy(undefined) }
  }
  const openReport = (link: ReportLink) => { setDetail(undefined); setReport(link) }
  if (report) return <EvaluationReportDetail key={report.report_id} runId={report.run_id} reportId={report.report_id} onBack={() => setReport(undefined)} />
  return <Space orientation="vertical" size="large" style={{ width: '100%' }}>
    <CatalogHeader title="评测任务" stats={taskStats} counts={page.counts} {...{ query, update, refresh }} />
    {catalog.error && <Alert type="error" showIcon title="任务查询失败" description={catalog.error} />}
    <Table<TaskRow> rowKey="run_id" scroll={{ x: 1050 }} loading={catalog.loading} dataSource={page.items} pagination={{
      current: query.offset / query.limit + 1, pageSize: query.limit, total: page.total, showSizeChanger: true,
    }} onChange={(pagination, _filters, sorter, extra) => {
      if (extra.action === 'sort') {
        const order = Array.isArray(sorter) ? sorter[0]?.order : sorter.order
        update({ duration_order: order === 'ascend' ? 'asc' : order === 'descend' ? 'desc' : undefined })
      } else {
        const limit = pagination.pageSize ?? query.limit
        update({ limit, offset: ((pagination.current ?? 1) - 1) * limit })
      }
    }} columns={[
      { title: '任务名称 / 类型', key: 'name', render: (_, task) => <NameCell {...task} /> },
      { title: '评测对象 / 版本', key: 'target', render: (_, task) => <TargetCell target={task.target} /> },
      { title: '执行状态', key: 'status', render: (_, task) => <Tag color={task.status === 'FAILED' ? 'error' : task.status === 'SUCCEEDED' ? 'success' : task.status === 'RUNNING' ? 'processing' : undefined}>{evaluationRunStatusLabel(task.status)}</Tag> },
      { title: '执行进度', key: 'progress', render: (_, task) => task.planned_stages == null || task.settled_stages == null ? '—' : `${task.settled_stages} / ${task.planned_stages} 阶段结束` },
      { title: '评测耗时', key: 'duration', sorter: true, sortOrder: query.duration_order === 'asc' ? 'ascend' : query.duration_order === 'desc' ? 'descend' : null,
        render: (_, task) => formatDuration(elapsedDuration(task, now)) },
      { title: '完成时间', key: 'finished', render: (_, task) => task.finished_at ? new Date(task.finished_at).toLocaleString() : '—' },
      { title: '操作', key: 'actions', render: (_, task) => <Space wrap size={4}>
        <Button type="link" size="small" onClick={() => setDetail(task)}>{task.status === 'FAILED' ? '查看原因' : '详情'}</Button>
        {task.report_id && <Button type="link" size="small" onClick={() => openReport({ run_id: task.run_id, report_id: task.report_id! })}>查看报告</Button>}
        {task.actions.includes('CANCEL') && <Popconfirm title="取消尚未开始的任务？" onConfirm={() => act(task.run_id, cancelTask, '任务已取消')}>
          <Button type="link" size="small" disabled={busy != null}>取消任务</Button>
        </Popconfirm>}
        {task.actions.includes('RETRY') && <Button type="link" size="small" disabled={busy != null} onClick={() => void act(task.run_id, retryTask, '任务已重新排队')}>重试</Button>}
        {task.actions.includes('DELETE') && <Popconfirm title="删除该评测任务？" description="将同时删除该任务的报告、执行记录和专属证据。此操作无法撤销。"
          okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
          onConfirm={() => act(task.run_id, deleteTask, '任务已删除')}>
          <Button danger type="link" size="small" disabled={busy != null}>删除任务</Button>
        </Popconfirm>}
      </Space> },
    ]} />
    {detail && <TaskDetails key={detail.run_id} task={page.items.find(item => item.run_id === detail.run_id) ?? detail}
      onClose={() => setDetail(undefined)} onReport={openReport} onFinalized={refresh} />}
  </Space>
}

const dimensions: Record<string, string> = { DOCUMENTATION: '说明文档', DATA_QUALITY: '数据质量', MODEL_APPLICATION: '模型应用' }
function measurementValue(value: ReportRow['measurements'][number]) {
  const label = dimensions[value.label] ?? policyFormatPresentation.statistics[value.label] ?? measurementLabel(value.label)
  if (value.unit === 'points') {
    const [numerator, denominator = '1'] = String(value.value).split('/')
    return `${label} ${formatStatistic(Number(numerator) / Number(denominator))} 分`
  }
  const unit = value.unit && !['ratio', 'none', '1'].includes(value.unit) ? ` ${measurementUnitLabel(value.unit)}` : ''
  return `${label} ${formatStatistic(value.value, value.unit)}${unit}`
}

export function EvaluationResultCatalogPage() {
  const catalog = useCatalog(getResultCatalog)
  const [params] = useSearchParams()
  const reportId = params.get('report')
  const runId = params.get('run')
  const { page, query, update, refresh } = catalog
  const [report, setReport] = useState<ReportLink>()
  useEffect(() => {
    if (reportId && runId) setReport({ report_id: reportId, run_id: runId })
  }, [reportId, runId])
  const [deleting, setDeleting] = useState<string>()
  const removeReport = async (id: string) => {
    setDeleting(id)
    try {
      await deleteReport(id)
      message.success('报告已删除')
      refresh()
    } catch (reason) {
      message.error(String(reason))
    } finally {
      setDeleting(undefined)
    }
  }
  if (report) return <EvaluationReportDetail key={report.report_id} runId={report.run_id} reportId={report.report_id} onBack={() => setReport(undefined)} />
  return <Space orientation="vertical" size="large" style={{ width: '100%' }}>
    <CatalogHeader title="评测结果" stats={resultStats} counts={page.counts} {...{ query, update, refresh }} results />
    {catalog.error && <Alert type="error" showIcon title="报告查询失败" description={catalog.error} />}
    <Table<ReportRow> rowKey="report_id" scroll={{ x: 1000 }} loading={catalog.loading} dataSource={page.items} pagination={{
      current: query.offset / query.limit + 1, pageSize: query.limit, total: page.total, showSizeChanger: true,
      onChange: (current, limit) => update({ limit, offset: (current - 1) * limit }),
    }} columns={[
      { title: '报告名称 / 类型', key: 'name', render: (_, row) => <NameCell {...row} /> },
      { title: '评测对象 / 版本', key: 'target', render: (_, row) => <TargetCell target={row.target} /> },
      { title: '核心测量值 / 维度得分', key: 'measurements', render: (_, row) => row.measurements.length
        ? <Space orientation="vertical" size={0}>{row.measurements.map((value, index) => <span key={index}>{measurementValue(value)}</span>)}</Space> : '—' },
      { title: '质量结论', key: 'verdict', render: (_, row) => <Tag color={row.verdict === 'PASS' ? 'success' : row.verdict === 'FAIL' ? 'error' : 'warning'}>{verdicts[row.verdict]}</Tag> },
      { title: '报告生成时间', key: 'generated', render: (_, row) => new Date(row.generated_at).toLocaleString() },
      { title: '操作', key: 'actions', render: (_, row) => <Space wrap>
        <Button type="link" size="small" onClick={() => setReport(row)}>查看报告</Button>
        <ReportExcel runId={row.run_id} reportId={row.report_id} />
        <Popconfirm title="删除该评测报告？" description="评测任务和原始执行记录将保留。此操作无法撤销。"
          okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
          onConfirm={() => removeReport(row.report_id)}>
          <Button danger type="link" size="small" disabled={deleting != null} loading={deleting === row.report_id}>删除报告</Button>
        </Popconfirm>
      </Space> },
    ]} />
  </Space>
}
