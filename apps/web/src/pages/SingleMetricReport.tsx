import { fetchCalculators, type CalculatorDescriptor } from '../api/registry'
import { useEffect, useState, type ReactNode } from 'react'
import { flushSync } from 'react-dom'
import { Alert, Button, Card, Collapse, Space, Statistic, Tag } from 'antd'
import { getCalculatorResults, getRunMetricResults, type CalculatorResult } from '../api/evaluationRuns'
import type { FormalReport } from '../api/formalDocumentReports'
import { getSampleById } from '../api/datasetVersions'
import { samplePreview, type Row } from './samplePresentation'
import type { QualityReport } from '../api/qualityResults'
import { evaluationRunStatusLabel } from '../utils/evaluationRunStatus'
import { measurementLabel, passFailStatusLabel } from '../utils/displayLabels'
import { formatStatistic, policyFormatPresentation, reasonLabel } from './calculatorPresentation'
import { CopyId, RunMetadata, RunTimingDetails, type ReportRun } from './RunMetadata'
import ReportExcel from './ReportExcel'
import './SingleMetricReport.css'

const documents: Record<string, string> = {
  '0101': '基本信息完整性', '0102': '内容特征完整性', '0103': '建设过程完整性', '0104': '应用说明完整性',
}
const operators: Record<string, string> = { GTE: '≥', GT: '>', LTE: '≤', LT: '<', EQ: '=', NE: '≠' }
type PersistedRule = { rule_evaluation_id: string; contract_item_key: string; metric_result_id: string; status: string; observed?: { value: unknown; unit?: string }; rule: { operator?: string; expected?: unknown; strategy?: string } }
const colors = ['#4b8a63', '#bd4c53', '#87929c']

export function Distribution({ title, parts }: {
  title: string
  parts: { label: string; count: number; color: string }[]
}) {
  const total = parts.reduce((sum, part) => sum + part.count, 0)
  if (!total) return null
  return <Card size="small" title={title}>
    <div className="single-report-bar" role="img" aria-label={parts.map(part => `${part.label} ${part.count}`).join('，')}>
      {parts.filter(part => part.count > 0).map(part => <span key={part.label}
        style={{ flexGrow: part.count, background: part.color }} />)}
    </div>
    <Space wrap>{parts.map(part => <span key={part.label}><i style={{ background: part.color }} />{part.label} {formatStatistic(part.count)}</span>)}</Space>
  </Card>
}

export default function SingleMetricReport({ run, report, specialized, issues, interpretation }: {
  run: ReportRun
  report: QualityReport | FormalReport
  specialized: (results: CalculatorResult[]) => ReactNode
  issues: ReactNode
  interpretation: ReactNode
}) {
  const [loadedResults, setResults] = useState<CalculatorResult[]>()
  const results = loadedResults ?? []
  const [calculators, setCalculators] = useState<CalculatorDescriptor[]>([])
  useEffect(() => {
    if (!loadedResults?.length) return
    let active = true
    fetchCalculators().then(value => { if (active) setCalculators(value) }).catch(() => {})
    return () => { active = false }
  }, [loadedResults])
  const calculatorName = (result: CalculatorResult) =>
    calculators.find(item => item.calculator_key === result.calculator_key && item.version === result.calculator_version)?.name ??
    (result.calculator_key === 'policy_document_format' ? '政策公文格式' : result.calculator_key)
  const [storedRules, setStoredRules] = useState<PersistedRule[]>([])
  const hasStoredRules = !!(report.schema_version !== '2.0' && report.metrics.some(metric => metric.rule_evaluations.length))
  const [error, setError] = useState('')
  const [technicalLoading, setTechnicalLoading] = useState(false)
  const [technicalError, setTechnicalError] = useState('')
  const [printing, setPrinting] = useState(false)
  const [printError, setPrintError] = useState('')
  const [printSamples, setPrintSamples] = useState<{ title?: string; summary?: string; reason?: string }[]>([])
  const formal = report.schema_version === '2.0' ? report : undefined
  const dataset = report.schema_version !== '2.0' ? report : undefined
  useEffect(() => {
    let active = true
    getCalculatorResults(run.run_id).then(value => { if (active) setResults(value) })
      .catch(failure => { if (active) { setError(failure.message); setResults([]) } })
    return () => { active = false }
  }, [run.run_id])
  useEffect(() => {
    if (!hasStoredRules) return
    const controller = new AbortController()
    fetch(`/api/v1/evaluation-runs/${run.run_id}/rule-evaluations`, { signal: controller.signal })
      .then(response => { if (!response.ok) throw new Error(`读取门禁依据失败（HTTP ${response.status}）`); return response.json() })
      .then((items: PersistedRule[]) => { if (!controller.signal.aborted) setStoredRules(items.filter(item => !!item.metric_result_id)) })
      .catch(failure => { if (!controller.signal.aborted) setError(failure.message) })
    return () => controller.abort()
  }, [run.run_id, hasStoredRules])
  const formalMetrics = formal?.dimensions.flatMap(dimension => dimension.metrics) ?? []
  const isDocument = formal?.mode === 'DOCUMENT'
  const documentMetrics = isDocument ? formalMetrics : []
  const versionResults = isDocument ? [...results].sort((left, right) =>
    (left.metric?.key ?? left.contract_item_key).localeCompare(right.metric?.key ?? right.contract_item_key) ||
    left.calculator_key.localeCompare(right.calculator_key)) : results
  const points = (score: string) => {
    const [numerator, denominator = '1'] = String(score).split('/')
    return formatStatistic(Number(numerator) / Number(denominator))
  }
  const titles = [...(dataset?.metrics.map(metric => `${metric.metric.key} · ${metric.metric.name}`) ?? []),
    ...documentMetrics.map(metric => `${metric.key} · ${documents[metric.key] ?? metric.key}`)]
  const rules = dataset?.native_evaluations ?? []
  const statisticsLabel = (key: string) => policyFormatPresentation.statistics[key] ?? measurementLabel(key)
  const dimensionScores = formal?.dimensions.flatMap(dimension => dimension.score == null
    ? [] : [{ title: '维度得分', value: points(dimension.score) }]) ?? []
  const metricScores = formalMetrics.flatMap(metric => metric.score == null
    ? [] : [{ title: `${metric.key} 得分`, value: points(metric.score) }])
  const core = isDocument ? [...dimensionScores, ...metricScores] : [
    ...formal?.dimensions.flatMap(dimension => [
      ...(dimension.score == null ? [] : [{ title: '维度得分', value: points(dimension.score) }]),
      { title: '达标线', value: dimension.threshold },
    ]) ?? [],
    ...metricScores,
    ...dataset?.metrics.flatMap(metric => [
      ...(metric.score == null ? [] : [{ title: `${metric.metric.name}得分`, value: formatStatistic(metric.score) }]),
      ...(metric.measurement ? [metric.measurement] : metric.observations.filter(item => typeof item.value === 'number').slice(0, 3)).map(item => ({
        title: measurementLabel(item.key), value: formatStatistic(item.value, item.unit),
      })),
    ]) ?? [],
    ...storedRules.filter(item => item.rule.operator && item.rule.expected != null).map(item => ({
      title: item.contract_item_key, value: formatStatistic(item.observed?.value, item.observed?.unit),
      threshold: `${operators[item.rule.operator!] ?? item.rule.operator} ${formatStatistic(item.rule.expected, item.observed?.unit)}`,
    })),
    ...rules.map(({ evaluation }) => ({
      title: statisticsLabel(evaluation.statistic_ref.statistics_key),
      value: formatStatistic(evaluation.metric_value, evaluation.statistic.unit),
      threshold: `${operators[evaluation.rule.operator] ?? evaluation.rule.operator} ${formatStatistic(evaluation.rule.expected, evaluation.statistic.unit)}`,
    })),
    ...results.flatMap(result => result.output_schema.statistics
      .filter(stat => typeof result.statistics[stat.key] === 'number' &&
        (['evaluable_rate', 'evaluated_compliance_ratio'].includes(stat.key) || (!rules.length && !dataset?.metrics.length && ['ratio', 'scalar'].includes(stat.kind ?? ''))) &&
        !rules.some(({ evaluation }) => evaluation.statistic_ref.statistics_key === stat.key))
      .slice(0, 4)
      .map(stat => ({ title: statisticsLabel(stat.key), value: formatStatistic(result.statistics[stat.key], stat.unit) }))),
  ]
  const recordDistributions = results.flatMap(result => {
    const keys = ['compliant_record_count', 'non_compliant_record_count', 'not_evaluated_count']
    if (!keys.every(key => typeof result.statistics[key] === 'number' && result.output_schema.statistics.some(stat => stat.key === key))) return []
    return [<Distribution key={result.calculator_result_id} title="记录判定分布"
      parts={keys.map((key, index) => ({ label: ['合规', '不合规', '未判定'][index], color: colors[index], count: Number(result.statistics[key]) }))} />]
  })
  const checks = documentMetrics.flatMap(metric => metric.checks ?? [])
  const countByOutcome = Object.entries(checks.reduce<Record<string, number>>((counts, check) => {
    counts[check.outcome] = (counts[check.outcome] ?? 0) + 1
    return counts
  }, {}))
  const title = titles.join('、') || results.map(result => [result.metric?.key, calculatorName(result)].filter(Boolean).join(' · ')).join('、') || '单指标评测报告'
  const print = async () => {
    setPrinting(true)
    setPrintError('')
    try {
      const ref = results.find(result => Number(result.statistics.not_evaluated_count) > 0)
      const artifact = ref?.decision_artifact_refs[0]
      const samples: typeof printSamples = []
      if (ref && artifact) {
        const root = `/api/v1/evidence/${artifact.evidence_id}/decision-preview`
        const json = async (url: string) => {
          const response = await fetch(url)
          if (!response.ok) throw new Error(`典型证据读取失败（HTTP ${response.status}）`)
          return response.json()
        }
        const plan = await json(`${root}?plan=true`) as { groups: { code: string }[] }
        for (const { code } of plan.groups.slice(0, 3)) {
          const group = await json(`${root}?group=${encodeURIComponent(code)}`) as { groups: { rows: Row[] }[] }
          const row = group.groups[0]?.rows[0]
          if (typeof row?.sample_id !== 'string') continue
          const record = await getSampleById(ref.dataset_version_id, row.sample_id, undefined, true)
          samples.push({ ...samplePreview(record, row), reason: reasonLabel(code) })
        }
      }
      flushSync(() => setPrintSamples(samples))
      window.print()
    } catch (failure) {
      flushSync(() => { setPrintSamples([]); setPrintError(failure instanceof Error ? failure.message : String(failure)) })
      window.print()
    } finally {
      setPrinting(false)
    }
  }
  const json = async () => {
    setTechnicalLoading(true)
    setTechnicalError('')
    try {
      const metricResults = await getRunMetricResults(run.run_id)
      const payload = { report, calculator_results: results, metric_results: metricResults }
      const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }))
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `quality-report-${run.run_id}.json`
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (failure) {
      setTechnicalError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      setTechnicalLoading(false)
    }
  }
  return <article className="single-report" aria-label="单指标评测报告">
    <Card size="small" className="single-report-header">
      <div className="single-report-title"><div><h1>{title}</h1>{run.status && <Tag>{evaluationRunStatusLabel(run.status)}</Tag>}</div>
        <Space wrap className="single-report-controls"><Button loading={printing} disabled={!loadedResults} onClick={() => void print()}>导出 PDF</Button><ReportExcel runId={run.run_id} reportId={report.report_id} /></Space></div>
      <dl className={isDocument ? 'single-report-document-versions' : undefined}>
        {versionResults.map(result => <div key={result.calculator_result_id}><dt>算子版本</dt><dd>{result.metric && `${result.metric.key}@${result.metric.version} · `}{calculatorName(result)} · {result.calculator_version}</dd></div>)}
        {dataset?.metrics.map(metric => <div key={metric.metric_result_id}><dt>指标版本</dt><dd>{metric.metric.key}@{metric.metric.version}{metric.calculator && ` · ${metric.calculator.key}@${metric.calculator.version}`}</dd></div>)}
      </dl>
      <RunMetadata run={run} />
    </Card>
    {printError && <Alert type="warning" title={printError} />}
    {error && <Alert type="warning" title={error} />}
    {!!core.length && <Card size="small" title="核心指标"><div className="single-report-metrics">{core.map((item, index) =>
      <Card key={index} size="small"><Statistic title={item.title} value={item.value} />{'threshold' in item && <span>阈值 {String(item.threshold)}</span>}</Card>
    )}</div></Card>}
    <Card size="small" title="评测结论">
      <strong>{formal ? ({ PASS: '达标', FAIL: '未达标', INDETERMINATE: '待判定' }[formal.overall_verdict] ?? formal.overall_verdict) : passFailStatusLabel(dataset!.gate_status)}</strong>
      {rules.map(({ evaluation }) => <div key={evaluation.rule_evaluation_id}>
        {statisticsLabel(evaluation.statistic_ref.statistics_key)} · {passFailStatusLabel(evaluation.status)}
        {evaluation.numerator != null && evaluation.denominator != null && ` · 分子 ${formatStatistic(evaluation.numerator)} / 分母 ${formatStatistic(evaluation.denominator)}`}
        {evaluation.status !== 'PASS' && evaluation.reason_code && ` · ${reasonLabel(evaluation.reason_code)}`}
      </div>)}
      {storedRules.map(rule => <div key={rule.rule_evaluation_id}>{rule.contract_item_key} · {passFailStatusLabel(rule.status)}</div>)}
      {!!recordDistributions.length && <div>不合规和未判定分别计数。未判定表示该记录未形成合规结论。</div>}
    </Card>
    {recordDistributions}
    {!!checks.length && <Distribution title="说明文档检查项分布" parts={countByOutcome.map(([outcome, count]) => ({
      label: ({ SATISFIED: '满足', MISSING: '缺失', UNVERIFIABLE: '无法核实', ERROR: '判定失败', NOT_EVALUATED: '未评测', INSUFFICIENT: '证据不足', NOT_APPLICABLE: '不适用' } as Record<string, string>)[outcome] ?? outcome,
      count, color: ({ SATISFIED: '#8b9aab', MISSING: '#536879', UNVERIFIABLE: '#aab4bd', INSUFFICIENT: '#6f8495' } as Record<string, string>)[outcome] ?? '#bbc3cb',
    }))} />}
    {specialized(results)}
    {issues}
    {!!printSamples.length && <Card size="small" className="single-report-print-only" title="典型证据">
      {printSamples.map((sample, index) => <blockquote key={index}><strong>{sample.title ?? '未判定样本'}</strong>
        <div>{sample.reason}</div><div>{sample.summary}</div></blockquote>)}
    </Card>}
    {interpretation}
    {!!printSamples.length && <Card className="single-report-print-only" size="small" title="整改建议">优先复核未判定样本，结合原文与冻结规则确认判定条件。</Card>}
    <div className="single-report-technical"><Collapse size="small" items={[{
      key: 'details', label: '技术详情', children: <Space wrap><Button disabled={!loadedResults} loading={technicalLoading} onClick={() => void json()}>下载 JSON</Button>{technicalError && <span role="alert">{technicalError}</span>}<span>报告 <CopyId value={report.report_id} /> · 协议 {report.schema_version}</span><RunTimingDetails run={run} /></Space>,
    }]} /></div>
  </article>
}
