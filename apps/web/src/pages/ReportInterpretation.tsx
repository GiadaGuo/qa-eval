import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, List, Space, Steps, Tag, Typography } from 'antd'
import { AuditOutlined, BulbOutlined, CheckCircleOutlined, WarningOutlined } from '@ant-design/icons'
import { getCalculatorResults } from '../api/evaluationRuns'
import type { QualityReport } from '../api/qualityResults'
import './ReportInterpretation.css'
import { formatStatistic, policyFormatPresentation, reasonLabel } from './calculatorPresentation'

type Item = { text: string; evidence_keys: string[] }
type LegacyInterpretation = {
  schema_version?: undefined
  sections: { conclusion: Item; causes: Item[]; risks: Item[]; recommendations: Item[] }
  facts: Record<string, unknown>
}
type Point = { title: string; content: string; evidence_refs: string[]; priority?: 'HIGH' | 'MEDIUM' | 'LOW' }
type Fact = { label: string; value: string | number; unit?: string; actual?: number | null; expected?: number | null; operator?: string }
type CompactInterpretation = {
  schema_version: 'r6.1'
  degraded: boolean
  sections: Record<'conclusions' | 'causes' | 'risks' | 'recommendations', Point[]>
  facts: Record<string, Fact>
}
type Interpretation = LegacyInterpretation | CompactInterpretation
const modules = [
  { key: 'conclusions', title: '核心结论', Icon: CheckCircleOutlined },
  { key: 'causes', title: '主要原因', Icon: BulbOutlined },
  { key: 'risks', title: '风险提示', Icon: WarningOutlined },
  { key: 'recommendations', title: '整改建议', Icon: AuditOutlined },
] as const
const priorities = { HIGH: { text: '高优先级', color: 'red' }, MEDIUM: { text: '中优先级', color: 'gold' }, LOW: { text: '低优先级', color: 'blue' } }
const summaryKeys = ['input_count', 'compliant_record_count', 'not_evaluated_count', 'compliant_record_ratio', 'evaluated_compliance_ratio']

function SingleMetricSummary({ report }: { report: QualityReport }) {
  const [statistics, setStatistics] = useState<Record<string, unknown>>({})
  useEffect(() => {
    let active = true
    getCalculatorResults(report.run_id).then(results => {
      const policy = results.find(result => result.calculator_key === 'policy_document_format')
      if (active && policy) setStatistics(policy.statistics)
    }).catch(() => {})
    return () => { active = false }
  }, [report.run_id])

  const gate = report.gate_status === 'PASS' ? '通过' : report.gate_status === 'FAIL' ? '未通过' : '未判定'
  const items = [
    { key: 'gate', label: '报告门禁', children: gate },
    ...summaryKeys.filter(key => typeof statistics[key] === 'number').map(key => ({
      key, label: policyFormatPresentation.statistics[key], children: statisticEvidence(key, statistics[key]),
    })),
    { key: 'violations', label: '确认违规', children: formatStatistic(report.issue_summary.confirmed_count) },
  ]
  const measurement = report.metrics.find(metric => metric.measurement?.value != null)
  if (measurement?.measurement && !summaryKeys.includes(measurement.measurement.key)) {
    items.push({
      key: 'measurement',
      label: measurement.metric.name,
      children: formatStatistic(measurement.measurement.value, measurement.measurement.unit),
    })
  }
  return <Descriptions className="interpretation-summary" aria-label="报告核心指标" size="small" bordered
    column={{ xs: 1, sm: 2, lg: 3 }} items={items} />
}

function CompactSections({ result }: { result: CompactInterpretation }) {
  return <div className="interpretation-modules">
    {result.degraded && <Alert type="info" title="已根据报告数据提供基础解读，可重新生成。" />}
    {modules.map(({ key, title, Icon }, index) => <section key={key} className="interpretation-module" aria-label={title}>
      <h3><Icon aria-hidden /><span>{String(index + 1).padStart(2, '0')}</span>{title}</h3>
      {result.sections[key].length === 0
        ? <p className="interpretation-empty">暂无可补充内容</p>
        : <ul>{result.sections[key].map((point, pointIndex) => <li key={pointIndex}>
          <div className="interpretation-point-title"><strong>{point.title}</strong>
            {point.priority && <Tag color={priorities[point.priority].color}>{priorities[point.priority].text}</Tag>}
          </div>
          <p>{point.content}</p>
          <details>
            <summary>数据依据</summary>
            <ul>{point.evidence_refs.map(ref => {
              const fact = result.facts[ref]
              if (!fact) return null
              return <li key={ref}>
                {fact.label}：{formatStatistic(fact.value, fact.unit)}
                {fact.unit === 'frequency' && ' 次'}
                {fact.expected != null && <>（实际值 {formatStatistic(fact.actual, fact.unit)}，要求 {fact.operator} {formatStatistic(fact.expected, fact.unit)}）</>}
              </li>
            })}</ul>
          </details>
        </li>)}</ul>}
    </section>)}
  </div>
}

type State =
  | { status: 'RUNNING'; phase: 'QUEUED' | 'GENERATING'; elapsed_seconds: number; estimated_remaining_seconds: number | null }
  | { status: 'NOT_REQUESTED' | 'FAILED' | 'READY'; interpretation?: Interpretation; detail?: string }

function formatDuration(seconds: number): string {
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
}

function statisticEvidence(key: string, value: unknown): string {
  if (value && typeof value === 'object') {
    return Object.entries(value).map(([field, count]) => `${reasonLabel(field)} ${formatStatistic(count)}`).join('，')
  }
  const statistic = key.split('.').at(-1) ?? key
  return formatStatistic(value, policyFormatPresentation.formulas[statistic] ? 'ratio' : undefined)
}

export default function ReportInterpretation({ runId, singleMetricReport, compact }: { runId: string; singleMetricReport?: QualityReport; compact?: boolean }) {
  const endpoint = `/api/v1/evaluation-runs/${runId}/report-interpretation`
  const [state, setState] = useState<State>({ status: 'NOT_REQUESTED' })
  const [submitting, setSubmitting] = useState(false)
  const [connection, setConnection] = useState<'idle' | 'checking' | 'connected' | 'failed' | 'unconfigured'>('idle')
  const [model, setModel] = useState<string>()
  const [statusUnavailable, setStatusUnavailable] = useState(false)
  const [loadingStatus, setLoadingStatus] = useState(true)
  const running = state.status === 'RUNNING'

  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const response = await fetch(endpoint)
        if (!response.ok) throw new Error()
        const next: State = await response.json()
        if (!active) return
        setLoadingStatus(false)
        setStatusUnavailable(false)
        setState(next)
        if (next.status === 'RUNNING') timer = setTimeout(refresh, 1500)
      } catch {
        if (active) {
          setStatusUnavailable(true)
          timer = setTimeout(refresh, 3000)
        }
      }
    }
    void refresh()
    return () => { active = false; clearTimeout(timer) }
  }, [endpoint, running])

  const checkModel = async () => {
    setConnection('checking')
    try {
      const response = await fetch(`${endpoint}/model-check`, { method: 'POST' })
      if (!response.ok) {
        setConnection(response.status === 503 ? 'unconfigured' : 'failed')
        return
      }
      const result: { status: string; model?: string } = await response.json()
      setModel(result.model)
      setConnection('connected')
    } catch {
      setConnection('failed')
    }
  }

  const generate = async () => {
    if (connection !== 'connected' || running || submitting) return
    setSubmitting(true)
    try {
      const response = await fetch(`${endpoint}?regenerate=${state.status !== 'NOT_REQUESTED'}`, { method: 'POST' })
      if (!response.ok) throw new Error()
      setState(await response.json())
    } catch {
      setState({ status: 'FAILED' })
    } finally {
      setSubmitting(false)
    }
  }
  const result = state.status === 'RUNNING' ? undefined : state.interpretation
  return <Card title="智能解读" extra={<Space wrap>
    <Button onClick={() => void checkModel()} disabled={submitting || running} loading={connection === 'checking'}>
      测试模型连通性
    </Button>
    <Button onClick={() => void generate()} disabled={connection !== 'connected' || loadingStatus || statusUnavailable}
      loading={submitting || running}>
      {state.status === 'FAILED' || state.status === 'READY' ? '重新生成' : '生成解读'}
    </Button>
  </Space>}>
    {singleMetricReport && !compact && <SingleMetricSummary report={singleMetricReport} />}
    <div role="status">
      {connection === 'idle' && !running && <Typography.Text type="secondary">请先测试模型连通性。</Typography.Text>}
      {connection === 'connected' && <Typography.Text type="success">{model ? `${model} 连通性测试通过` : '模型连通性测试通过'}</Typography.Text>}
      {(connection === 'failed' || connection === 'unconfigured') && <Alert type="error"
        title={connection === 'unconfigured' ? '智能解读模型尚未配置。' : '模型调用失败，请检查模型配置或重试。'} />}
      {statusUnavailable && <Alert type="warning" title="暂时无法获取解读状态，正在重试。" />}
    </div>
    {state.status === 'FAILED' && <Alert type="error" title={state.detail ?? '生成解读请求失败，请重试。'} />}
    {state.status === 'RUNNING' && <Space orientation="vertical" style={{ width: '100%' }}>
      <Steps size="small" current={state.phase === 'QUEUED' ? 0 : 1}
        items={[{ title: '排队' }, { title: '模型生成' }, { title: '完成' }]} />
      <Typography.Text>
        已用时 {formatDuration(state.elapsed_seconds)} · {state.phase === 'QUEUED'
          ? '等待可用执行位置'
          : state.estimated_remaining_seconds == null
            ? '已超过预计用时，仍在生成'
            : `预计还需约 ${formatDuration(state.estimated_remaining_seconds)}`}
      </Typography.Text>
    </Space>}
    {result?.schema_version === 'r6.1' && <CompactSections result={result} />}
    {singleMetricReport && result && !result.schema_version && <Alert type="info" title="已有历史解读，可重新生成新版四模块解读。" />}
    {!singleMetricReport && result && !result.schema_version && ([
      ['核心结论', [result.sections.conclusion]], ['主要原因', result.sections.causes],
      ['风险提示', result.sections.risks], ['整改建议', result.sections.recommendations],
    ] as [string, Item[]][]).map(([title, items]) => <List key={String(title)} header={title} dataSource={items}
      renderItem={item => <List.Item><div>{item.text}<br /><Typography.Text type="secondary">
        依据 {item.evidence_keys.map(key => `${policyFormatPresentation.statistics[key.split(".").at(-1) ?? key] ?? key} ${statisticEvidence(key, result.facts[key])}`).join('、')}
      </Typography.Text></div></List.Item>} />)}
  </Card>
}
