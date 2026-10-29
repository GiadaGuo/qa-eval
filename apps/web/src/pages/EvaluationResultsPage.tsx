import { useEffect, useRef, useState } from 'react'
import { ArrowLeftOutlined, DeleteOutlined, EyeOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Collapse, Empty, List, message, Modal, Pagination, Popconfirm, Space, Spin, Statistic, Table, Tag, Typography } from 'antd'

import { getSampleById, type SampleRecord } from '../api/datasetVersions'
import { deleteEvaluationRun, listEvaluationRuns, type EvaluationRun } from '../api/evaluationRuns'
import { finalizeReport, getQualityIssues, getQualityReport, ReportNotReadyError, type QualityIssue, type QualityReport } from '../api/qualityResults'

const { Title, Text } = Typography
const statusColor = (value: string) => value === 'SUCCEEDED' || value === 'PASS' ? 'success' : value === 'FAILED' || value === 'FAIL' ? 'error' : 'warning'
const ISSUES_PAGE_SIZE = 10

type RadarAxis = NonNullable<QualityReport['radar']>[number]

const radarPoint = (index: number, radius: number) => {
  const angle = (Math.PI * 2 * index) / 8 - Math.PI / 2
  return { x: 150 + Math.cos(angle) * radius, y: 150 + Math.sin(angle) * radius }
}

function QualityRadar({ axes }: { axes: RadarAxis[] }) {
  const ordered = Array.from({ length: 8 }, (_, index) => {
    const dimension = `020${index + 1}`
    return axes.find(item => item.dimension === dimension) ?? { dimension, score: null, coverage: 0 }
  })
  const grid = [0.25, 0.5, 0.75, 1].map(scale =>
    ordered.map((_, index) => radarPoint(index, 92 * scale)).map(point => `${point.x},${point.y}`).join(' '),
  )
  const scored = ordered.flatMap((axis, index) => axis.score === null ? [] : [{ axis, point: radarPoint(index, 92 * axis.score / 100) }])
  const scorePoints = scored.map(item => `${item.point.x},${item.point.y}`).join(' ')

  if (scored.length < 3) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`仅 ${scored.length} 个维度具备完整正式评分，至少 3 个维度后绘制雷达图；缺失维度不会补 0。`} />
  }

  return <div className="quality-radar-wrap">
    <svg className="quality-radar" viewBox="0 0 300 300" role="img" aria-label="数据质量 0201 至 0208 雷达图；无评分的维度显示为 N/A">
      {grid.map((points, index) => <polygon key={index} points={points} className="radar-grid" />)}
      {ordered.map((_, index) => {
        const endpoint = radarPoint(index, 92)
        return <line key={index} x1="150" y1="150" x2={endpoint.x} y2={endpoint.y} className="radar-axis" />
      })}
      <polygon points={scorePoints} className="radar-score-area" />
      {scored.map(({ axis, point }) => <circle key={axis.dimension} cx={point.x} cy={point.y} r="4" className="radar-score-point"><title>{axis.dimension}: {axis.score?.toFixed(1)} 分，覆盖率 {(axis.coverage * 100).toFixed(1)}%</title></circle>)}
      {ordered.map((axis, index) => {
        const label = radarPoint(index, 122)
        return <g key={axis.dimension}>
          <text x={label.x} y={label.y - 3} textAnchor="middle" className="radar-label">{axis.dimension}</text>
          <text x={label.x} y={label.y + 12} textAnchor="middle" className={axis.score === null ? 'radar-value radar-value-na' : 'radar-value'}>{axis.score === null ? 'N/A' : axis.score.toFixed(1)}</text>
        </g>
      })}
    </svg>
    <Text type="secondary">空缺维度按 N/A 展示，不以 0 分填充；图形仅连接有可评分结果的维度。</Text>
  </div>
}

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
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [reportPending, setReportPending] = useState(false)
  const [finalizing, setFinalizing] = useState(false)
  const [selectedMetricResultId, setSelectedMetricResultId] = useState<string | null>(null)
  const [sampleModalOpen, setSampleModalOpen] = useState(false)
  const [sampleLoading, setSampleLoading] = useState(false)
  const [sampleRecord, setSampleRecord] = useState<SampleRecord | null>(null)
  const [sampleError, setSampleError] = useState<string | null>(null)

  const loadRuns = () => {
    setLoading(true); setError(null)
    listEvaluationRuns().then(setRuns).catch(reason => setError(reason instanceof Error ? reason.message : '加载失败')).finally(() => setLoading(false))
  }

  const handleDelete = async (run: EvaluationRun) => {
    setDeletingId(run.run_id)
    try {
      await deleteEvaluationRun(run.run_id)
      message.success(`评测任务 ${run.run_id} 已删除`)
      if (selected?.run_id === run.run_id) {
        setSelected(null); setReport(null); setIssues([]); setIssuesPage(1); setIssuesTotal(0); setIssuesError(null); setError(null); setReportPending(false); setSelectedMetricResultId(null)
      }
      loadRuns()
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : '删除失败')
    } finally {
      setDeletingId(null)
    }
  }

  useEffect(() => {
    let cancelled = false
    listEvaluationRuns().then(value => { if (!cancelled) setRuns(value) }).catch(reason => { if (!cancelled) setError(reason instanceof Error ? reason.message : '加载失败') }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const loadIssues = async (run: EvaluationRun, page: number, metricResultId?: string | null) => {
    const requestId = ++issuesRequestId.current
    setIssuesLoading(true); setIssuesError(null)
    try {
      const body = await getQualityIssues(run.run_id, {
        limit: ISSUES_PAGE_SIZE,
        offset: (page - 1) * ISSUES_PAGE_SIZE,
        metric_result_id: metricResultId ?? undefined,
      })
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
    setSelected(run); setLoading(true); setError(null); setReportPending(false); setIssues([]); setIssuesTotal(0); setIssuesError(null); setSelectedMetricResultId(null)
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

  const selectMetric = (metricResultId: string | null) => {
    setSelectedMetricResultId(metricResultId)
    if (selected) {
      void loadIssues(selected, 1, metricResultId)
    }
  }

  const viewSample = async (datasetVersionId: string, sampleId: string) => {
    setSampleModalOpen(true)
    setSampleLoading(true)
    setSampleRecord(null)
    setSampleError(null)
    try {
      const record = await getSampleById(datasetVersionId, sampleId)
      setSampleRecord(record)
    } catch (reason) {
      setSampleError(reason instanceof Error ? reason.message : '加载样本原文失败')
    } finally {
      setSampleLoading(false)
    }
  }

  const closeSampleModal = () => {
    setSampleModalOpen(false)
    setSampleRecord(null)
    setSampleError(null)
  }

  const renderSampleContent = (record: SampleRecord | null) => {
    if (!record) return null
    const textContents = record.contents
      .filter(item => item.payload?.type === 'inline_text' && item.payload.text)
      .map(item => ({ role: item.role, text: item.payload!.text! }))
    return (
      <Space direction="vertical" style={{ width: '100%' }}>
        <div><Text type="secondary">sample_id:</Text> <Text copyable>{record.sample_id}</Text></div>
        {textContents.length === 0
          ? <Empty description="该样本没有可展示的文本内容" />
          : textContents.map((item, index) => (
            <div key={index}>
              <Text type="secondary">role: {item.role}</Text>
              <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginTop: 8, padding: 12, background: '#f5f5f5', borderRadius: 4, maxHeight: 400, overflow: 'auto' }}>
                {item.text}
              </div>
            </div>
          ))}
      </Space>
    )
  }

  if (selected) return <div style={{ width: '100%', maxWidth: 1440, margin: '0 auto', textAlign: 'left' }}><Space direction="vertical" size="large" style={{ width: '100%' }}>
    <Button icon={<ArrowLeftOutlined />} onClick={() => { setSelected(null); setReport(null); setIssues([]); setIssuesPage(1); setIssuesTotal(0); setIssuesError(null); setError(null); setReportPending(false); setSelectedMetricResultId(null); closeSampleModal() }}>返回评测结果</Button>
    <div><Title level={2}>质量报告</Title><Text type="secondary">正式平台 EvaluationRun：{selected.run_id}</Text></div>
    {loading && <Spin />}{error && <Alert type="warning" showIcon title="报告读取失败" description={error} />}
    {reportPending && !loading && <Alert type="info" showIcon title="报告尚未生成" description="该评测任务到达终态时报告生成未完成，可重新生成。"
      action={<Button size="small" type="primary" loading={finalizing} onClick={() => void generateReport(selected)}>生成报告</Button>} />}
    {report && <>
      <Card title="报告概览"><Space wrap size="large"><Statistic title="执行状态" value={report.execution_status} /><Statistic title="质量门禁" value={report.gate_status} /><Statistic title="已评测指标" value={report.coverage.evaluated} /><Statistic title="部分覆盖" value={report.coverage.partial ?? 0} /><Statistic title="不适用" value={report.coverage.not_applicable} /><Statistic title="错误" value={report.coverage.error} /><Statistic title="质量问题" value={report.issue_summary.confirmed_count + report.issue_summary.candidate_count} /></Space></Card>
      {report.scoring && report.radar && <div className="quality-summary-grid">
        <Card title="评分与指南覆盖" className="quality-score-card">
          <div className="quality-score-stats">
            <Statistic title="完整综合评分" value={report.scoring.overall_score ?? 'N/A'} precision={report.scoring.overall_score == null ? undefined : 1} suffix={report.scoring.overall_score == null ? undefined : '分'} />
            <Statistic title="部分覆盖分数" value={report.scoring.partial_score ?? report.scoring.evaluated_score ?? 'N/A'} precision={report.scoring.partial_score == null && report.scoring.evaluated_score == null ? undefined : 1} suffix={report.scoring.partial_score == null && report.scoring.evaluated_score == null ? undefined : '分'} />
            <Statistic title="有效覆盖率" value={report.scoring.effective_coverage * 100} precision={1} suffix="%" />
          </div>
          <Alert type={report.scoring.overall_score_status === 'COMPLETE' ? 'success' : 'warning'} showIcon title={report.scoring.overall_score_status === 'COMPLETE' ? '已形成完整综合评分' : '当前不具备完整综合评分条件'} description={`${report.scoring.disclaimer} 计分范围：${report.scoring.included_metrics.length ? report.scoring.included_metrics.join('、') : '无可评分指标'}；未覆盖权重：${report.scoring.unscored_weight ?? '未知'}。`} />
          <QualityRadar axes={report.radar} />
        </Card>
        <Card title="整改建议" extra={<Tag color="blue">确定性规则</Tag>}>
          {report.recommendation_details?.length
            ? <List dataSource={report.recommendation_details} renderItem={(item, index) => <List.Item><div className="recommendation-item"><span className="recommendation-index">{index + 1}</span><div><Space><Tag>{item.metric_key}</Tag><Tag color={item.certainty === 'DETERMINISTIC' ? 'green' : 'gold'}>{item.certainty}</Tag>{item.issue_code && <Tag>{item.issue_code}</Tag>}</Space><div><Text>{item.recommendation}</Text></div><Text type="secondary">依据：{item.basis}</Text></div></div></List.Item>} />
            : report.recommendations?.length
            ? <List dataSource={report.recommendations} renderItem={(item, index) => <List.Item><div className="recommendation-item"><span className="recommendation-index">{index + 1}</span><Text>{item}</Text></div></List.Item>} />
            : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有可生成的整改建议" />}
          <Text type="secondary">建议优先级：规则 FAIL → 执行 ERROR → 未覆盖指标；具体样本结论仅在存在证据时给出。</Text>
        </Card>
      </div>}
      {report.coverage_analysis && <Card title="覆盖率分析"><Table pagination={false} rowKey="name" dataSource={[
        { name: '指标实现覆盖率', ...report.coverage_analysis.implementation },
        { name: '指标执行覆盖率', ...report.coverage_analysis.execution },
        { name: '有效评分覆盖率', ...report.coverage_analysis.effective_scoring },
        { name: '观察性覆盖率', ...report.coverage_analysis.observational },
      ]} columns={[
        { title: '口径', dataIndex: 'name' },
        { title: '分子 / 分母', render: (_, item) => item.numerator == null || item.denominator == null ? 'N/A' : `${item.numerator} / ${item.denominator}` },
        { title: '比例', dataIndex: 'ratio', render: value => value == null ? 'N/A' : `${(value * 100).toFixed(1)}%` },
        { title: '计算依据', dataIndex: 'basis' },
        { title: '不可计算原因', dataIndex: 'unavailable_reason', render: value => value ?? '—' },
      ]} /></Card>}
      {report.dimension_scores && report.dimension_scores.length > 0 && <Card title="维度评分"><Table pagination={false} rowKey="dimension" dataSource={report.dimension_scores} columns={[
        { title: '维度', dataIndex: 'dimension' },
        { title: '正式分数', dataIndex: 'score', render: value => value == null ? 'N/A' : value.toFixed(1) },
        { title: '状态', dataIndex: 'status', render: value => <Tag>{value}</Tag> },
        { title: '有效 / 适用权重', render: (_, item) => `${item.effective_weight} / ${item.applicable_weight}` },
        { title: '覆盖率', dataIndex: 'coverage', render: value => value == null ? 'N/A' : `${(value * 100).toFixed(1)}%` },
        { title: '未评分原因', dataIndex: 'reasons', render: value => value.length ? value.join('、') : '—' },
      ]} /></Card>}
      {!report.scoring && <Alert type="info" showIcon title="历史报告未包含综合评分" description="该报告使用 1.0 协议，可重新生成 1.1 报告后查看雷达图、有效覆盖率和整改建议。" />}
      {report.metrics.map(metric => {
        // 旧版报告快照没有 measurement/diagnostics 字段：回退为平铺观测表。
        const isLegacySnapshot = metric.measurement === undefined && metric.diagnostics === undefined
        const diagnostics = isLegacySnapshot ? [] : (metric.diagnostics ?? [])
        const primaryRows = isLegacySnapshot ? metric.observations : (metric.measurement ? [metric.measurement] : [])
        return <Card key={metric.metric_result_id} title={`${metric.metric.key} · ${metric.metric.name}`} extra={<Tag color={statusColor(metric.status)}>{metric.status}</Tag>}>
          <Space wrap style={{ marginBottom: 12 }}>
            {(metric.scoring_eligibility ?? []).map(item => <Tag key={item} color={item === 'SCOREABLE' ? 'green' : 'gold'}>{item}</Tag>)}
            <Tag>正式评分：{metric.score == null ? 'N/A' : metric.score.toFixed(1)}</Tag>
            {metric.calculator && <Tag>Calculator：{metric.calculator.key}@{metric.calculator.version}</Tag>}
            {metric.execution_mode && <Tag>执行模式：{metric.execution_mode}</Tag>}
            {metric.population && <Tag>数据覆盖：{metric.population.evaluated_count}/{metric.population.input_count}</Tag>}
          </Space>
          {(metric.scoring_exclusion_reasons?.length ?? 0) > 0 && <Alert style={{ marginBottom: 12 }} type="info" showIcon title="未纳入正式评分" description={metric.scoring_exclusion_reasons?.join('、')} />}
          <Table rowKey="key" pagination={false} dataSource={primaryRows} locale={{ emptyText: '契约测量口径无观测值（结果状态异常）' }} columns={[{ title: '计算方法', dataIndex: 'key' }, { title: '值', dataIndex: 'value', render: value => String(value) }, { title: '单位', dataIndex: 'unit', render: value => value ?? '—' }]} />
          {diagnostics.length > 0 && <Collapse ghost size="small" style={{ marginTop: 8 }} items={[{ key: 'diagnostics', label: `诊断字段（${diagnostics.length}，同一次执行附带输出，非独立计算方法）`, children: <Table rowKey="key" pagination={false} dataSource={diagnostics} columns={[{ title: 'Measurement', dataIndex: 'key' }, { title: '值', dataIndex: 'value', render: value => String(value) }, { title: '单位', dataIndex: 'unit', render: value => value ?? '—' }]} /> }]} />}
          <Space wrap style={{ marginTop: 16 }}>{metric.rule_evaluations.map(rule => <Tag key={rule.rule_evaluation_id} color={statusColor(rule.status)}>{rule.contract_item_key} · {rule.status}</Tag>)}</Space>
          <Collapse ghost size="small" style={{ marginTop: 8 }} items={[{ key: 'trace', label: '评测溯源与证据', children: <Space direction="vertical"><Text>Metric Result：{metric.metric_result_id}</Text><Text>Stage Run：{metric.stage_run_id ?? '历史报告未记录'}</Text><Text>Evidence：{metric.evidence_ids.length ? metric.evidence_ids.join('、') : '无'}</Text></Space> }]} />
        </Card>
      })}
      <Card title={`质量问题（共 ${issuesTotal} 条）`}>
        {report.metrics.length > 0 && (
          <Space wrap style={{ marginBottom: 16 }}>
            <Button type={selectedMetricResultId === null ? 'primary' : 'default'} onClick={() => selectMetric(null)}>
              全部 ({report.issue_summary.confirmed_count + report.issue_summary.candidate_count})
            </Button>
            {report.metrics.map(metric => {
              const issueCount = metric.issue_summary.confirmed_count + metric.issue_summary.candidate_count
              return (
                <Button
                  key={metric.metric_result_id}
                  type={selectedMetricResultId === metric.metric_result_id ? 'primary' : 'default'}
                  onClick={() => selectMetric(metric.metric_result_id)}
                >
                  {metric.metric.name} ({issueCount})
                </Button>
              )
            })}
          </Space>
        )}
        {issuesError
          ? <Alert type="warning" showIcon title="质量问题加载失败" description={issuesError} action={<Button size="small" onClick={() => void loadIssues(selected, issuesPage, selectedMetricResultId)}>重试</Button>} />
          : <List loading={issuesLoading} dataSource={issues} locale={{ emptyText: '没有质量问题' }} renderItem={issue => {
            const sampleId = issue.subject?.type === 'SAMPLE' && typeof issue.subject?.sample_id === 'string' ? issue.subject.sample_id : null
            return <List.Item><div style={{ width: '100%' }}><Space><Tag color={issue.determination === 'CONFIRMED' ? 'error' : 'warning'}>{issue.determination}</Tag><Tag>{issue.severity}</Tag><Tag>{issue.code}</Tag></Space><div style={{ marginTop: 8 }}><Text strong>{issue.message}</Text></div><div><Text type="secondary">{JSON.stringify(issue.subject)} · evidence: {issue.evidence.map(item => item.evidence_id).join(', ')}</Text></div>{sampleId && report && <div style={{ marginTop: 8 }}><Button size="small" icon={<EyeOutlined />} onClick={() => void viewSample(report.dataset_version_id, sampleId)}>查看样本原文</Button></div>}</div></List.Item>
          }} />}
        {!issuesError && <div style={{ marginTop: 16, textAlign: 'right' }}>
          <Pagination current={issuesPage} pageSize={ISSUES_PAGE_SIZE} total={issuesTotal} showSizeChanger={false} hideOnSinglePage showTotal={total => `共 ${total} 条`} onChange={page => void loadIssues(selected, page, selectedMetricResultId)} />
        </div>}
      </Card>
    </>}
    <Modal
      title="样本原文"
      open={sampleModalOpen}
      onCancel={closeSampleModal}
      footer={[<Button key="close" onClick={closeSampleModal}>关闭</Button>]}
      width={800}
    >
      {sampleLoading && <Spin />}
      {sampleError && <Alert type="warning" showIcon title="加载失败" description={sampleError} />}
      {!sampleLoading && !sampleError && renderSampleContent(sampleRecord)}
    </Modal>
  </Space></div>

  return <div style={{ width: '100%', maxWidth: 1440, margin: '0 auto', textAlign: 'left' }}><Space direction="vertical" size="large" style={{ width: '100%' }}>
    <div><Title level={2}>评测结果</Title><Text type="secondary">正式 EvaluationRun、MetricResult、规则判定、质量问题和报告。</Text></div>
    <Button icon={<ReloadOutlined />} onClick={loadRuns}>刷新</Button>
    {error && <Alert type="error" showIcon title="加载失败" description={error} />}
    {loading ? <Spin /> : runs.length === 0 ? <Empty description="暂无正式评测任务" /> : <Table rowKey="run_id" dataSource={runs} columns={[{ title: 'Run ID', dataIndex: 'run_id' }, { title: 'Dataset Version', dataIndex: 'dataset_version_id' }, { title: 'Contract Version', dataIndex: 'contract_version_id' }, { title: '状态', dataIndex: 'status', render: value => <Tag color={statusColor(value)}>{value}</Tag> }, { title: '完成时间', dataIndex: 'finished_at', render: value => value ? new Date(value).toLocaleString() : '—' }, { title: '操作', render: (_, run) => {
      const cannotDelete = ['PENDING', 'RUNNING', 'CANCEL_REQUESTED'].includes(run.status)
      return <Space>
        <Button type="link" disabled={['PENDING', 'RUNNING'].includes(run.status)} onClick={() => void openRun(run)}>查看报告</Button>
        <Popconfirm
          title={`删除评测任务 ${run.run_id}？`}
          description="删除后不可恢复，将同时移除该任务产生的指标结果、规则判定、质量问题和报告。"
          okText="删除"
          cancelText="取消"
          okButtonProps={{ danger: true, loading: deletingId === run.run_id }}
          disabled={cannotDelete || deletingId !== null}
          onConfirm={() => void handleDelete(run)}
        >
          <Button danger type="link" icon={<DeleteOutlined />} loading={deletingId === run.run_id} disabled={cannotDelete || (deletingId !== null && deletingId !== run.run_id)}>删除</Button>
        </Popconfirm>
      </Space>
    } }]} />}
  </Space></div>
}
