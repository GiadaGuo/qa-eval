import { useEffect, useState } from 'react'
import { Alert, Button, Card, Collapse, Descriptions, List, Modal, Progress, Select, Space, Statistic } from 'antd'

import { getCalculatorResults, type CalculatorResult } from '../api/evaluationRuns'
import { getEvidenceMetadata, rebuildEvidenceIndex } from '../api/qualityResults'

import type { QualityReport } from '../api/qualityResults'
import { formatStatistic, policyFormatPresentation, reasonLabel } from './calculatorPresentation'

type EvidenceRow = Record<string, unknown>
type EvidencePage = { rows: EvidenceRow[]; next_cursor: string | null }
type EvidenceReference = { id: string; version: string; cursor?: string; status?: string; reason?: string; decision?: boolean }
type EvidenceResponse = { reference: EvidenceReference; page?: EvidencePage; error?: string }

export default function CalculatorObservations({ runId, status, nativeEvaluations, viewSample }: {
  runId: string
  status: string
  nativeEvaluations?: QualityReport["native_evaluations"]
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
      if (reference.status) params.set("status", reference.status)
      if (reference.reason) params.set("reason", reference.reason)
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

  return <Card title="算子评测明细">
    <List dataSource={results} rowKey="calculator_result_id" renderItem={result => {
      const policy = result.calculator_key === 'policy_document_format'
      const metadata = policy ? policyFormatPresentation : undefined
      const label = (key: string) => metadata?.statistics[key] ?? key
      const stats = result.statistics
      const decisions = result.decision_artifact_refs[0]
      const drill = (reason?: string) => {
        if (decisions) setReference({ id: decisions.evidence_id, version: result.dataset_version_id, status: 'NOT_EVALUATED', reason, decision: true })
      }
      const headline = policy ? ['compliant_record_ratio', 'evaluated_compliance_ratio', 'evaluable_rate'] : []
      const fields = result.output_schema.statistics.filter(stat => stat.key in stats)
      const rules = nativeEvaluations?.filter(({ evaluation }) => evaluation.calculator_result_id === result.calculator_result_id) ?? []
      return <List.Item><Space orientation="vertical" style={{ width: '100%' }}>
        <strong>{policy ? '政策公文格式' : result.contract_item_key}</strong>
        <Space wrap size="large">{headline.filter(key => key in stats).map(key =>
          <Statistic key={key} title={label(key)} value={formatStatistic(stats[key], 'ratio')} />)}</Space>
        {rules.map(({ evaluation: rule }) => <Alert key={rule.rule_evaluation_id}
          type={rule.status === 'FAIL' ? 'warning' : 'info'}
          title={rule.status === 'FAIL' ? '门禁未通过' : rule.status === 'PASS' ? '门禁通过' : '门禁未判定'}
          description={`${label(rule.statistic_ref.statistics_key)} ${formatStatistic(rule.metric_value, rule.statistic.unit)}，阈值 ${({ GTE: '≥', GT: '>', LTE: '≤', LT: '<', EQ: '=' } as Record<string, string>)[rule.rule.operator] ?? rule.rule.operator} ${formatStatistic(rule.rule.expected, rule.statistic.unit)}。 ${rule.numerator != null && rule.denominator != null ? `分子 ${formatStatistic(rule.numerator)} / 分母 ${formatStatistic(rule.denominator)}。` : ''} ${rule.status === 'FAIL' ? reasonLabel(rule.reason_code) : ''}`} />)}
        {policy && Number(stats.not_evaluated_count) > 0 && <Alert type="info" title={Number(stats.non_compliant_record_count) === 0 ? "未能判定 · 未发现不合规记录" : "未能判定"}
          description={`${formatStatistic(stats.not_evaluated_count)} 条记录未能判定。总体合规率以全部输入为分母，未判定记录会拉低总体合规率。已评测合规率仅以已判定记录为分母。${Number(stats.non_compliant_record_count) === 0 && rules.some(({ evaluation }) => evaluation.status === "FAIL" && evaluation.statistic_ref.statistics_key === "compliant_record_ratio") ? "本次门禁未达标源于未判定记录拉低总体合规率，已判定记录全部合规。" : ""}`}
          action={decisions && <Button size="small" onClick={() => drill()}>查看未判定记录</Button>} />}
        <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
          {fields.filter(stat => stats[stat.key] === null || typeof stats[stat.key] !== 'object').map(stat => {
            const formula = metadata?.formulas[stat.key] ?? (stat.numerator_key && stat.denominator_key ? [stat.numerator_key, stat.denominator_key] : null)
            return <Descriptions.Item key={stat.key} label={label(stat.key)}>
              <span title={`${stat.method} · ${stat.population}`}>{headline.includes(stat.key) ? null : formatStatistic(stats[stat.key], stat.unit)}
                {formula && ` ${label(formula[0])} ÷ ${label(formula[1])}（${formatStatistic(stats[formula[0]])} / ${formatStatistic(stats[formula[1]])}）`}
                {formula && stats[stat.key] === null && '，分母为零时无法计算'}
              </span>
            </Descriptions.Item>
          })}
        </Descriptions>
        {fields.filter(stat => stats[stat.key] && typeof stats[stat.key] === 'object' && !Array.isArray(stats[stat.key])).map(stat => {
          const rows = Object.entries(stats[stat.key] as Record<string, unknown>)
            .flatMap(([code, count]) => typeof count === 'number' ? [{ code, count }] : [])
            .sort((a, b) => b.count - a.count || a.code.localeCompare(b.code))
          if (!rows.length) return null
          const reasons = stat.key === 'unknown_reasons'
          return <section key={stat.key} aria-label={label(stat.key)}>
            <strong>{label(stat.key)}</strong>
            {reasons && <div>下列频次按算子统计累计，同一记录可贡献多次。点击原因可筛出未判定记录。</div>}
            {rows.slice(0, 5).map(({ code, count }) => <div key={code}>
              {reasons && decisions ? <Button type="link" onClick={() => drill(code)}>{reasonLabel(code)}</Button> : reasonLabel(code)}
              <Progress percent={count / (rows[0].count || 1) * 100} format={() => formatStatistic(count)} />
            </div>)}
            <Collapse items={[{ key: 'all', label: '查看全部分类', children: <table>
              <thead><tr><th>{reasons ? '原因' : '分类'}</th>{reasons && <th>类别</th>}<th>次数</th></tr></thead>
              <tbody>{rows.map(({ code, count }) => <tr key={code}><td>{reasons && decisions
                ? <Button type="link" onClick={() => drill(code)}>{reasonLabel(code)}</Button> : reasonLabel(code)}</td>{reasons && <td>{code.startsWith('N') && /^N[1-8]\//.test(code) ? '规范核验' : code.includes('/') ? '模板要素' : '记录判定'}</td>}<td>{formatStatistic(count)}</td></tr>)}</tbody>
            </table> }]} />
          </section>
        })}
        {policy && Number(stats.not_evaluated_count) > 0 && <Alert type="info" title="整改建议"
          description="优先抽查排名靠前的未判定原因，对照原文核实公文类型、模板路径和关键格式字段，再复核记录覆盖率。" />}
        <Space wrap>
          {result.decision_artifact_refs.map(ref => <Button key={ref.evidence_id} size="small"
            onClick={() => setReference({ id: ref.evidence_id, version: result.dataset_version_id, decision: true })}>查看判定与原文证据</Button>)}
          {result.evidence_artifact_refs.map(ref => <Button key={ref.evidence_id} size="small"
            onClick={() => setReference({ id: ref.evidence_id, version: result.dataset_version_id })}>查看证据</Button>)}
        </Space>
        <Collapse items={[{ key: 'technical', label: '技术详情', children: <>
          <div>{result.calculator_key}@{result.calculator_version} · {result.status} · 展示版本 {metadata?.version ?? '通用'}</div>
          <pre className="evidence-row-json">{JSON.stringify(stats, null, 2)}</pre>
        </> }]} />
      </Space></List.Item>
    }} />
    <Modal open={!!reference} title={reference?.reason ? reasonLabel(reference.reason) : reference?.status ? "未判定记录" : "算子判定与证据"} onCancel={() => setReference(undefined)}
      footer={page?.next_cursor && reference && <Button onClick={() => setReference({
        ...reference, cursor: page.next_cursor ?? undefined,
      })}>下一页</Button>}>
      {reference?.decision && <Space wrap style={{ marginBottom: 12 }}>
        <Select aria-label="记录判定状态" value={reference.status ?? ''} options={[
          { value: '', label: '全部判定' }, { value: 'NOT_EVALUATED', label: '未判定' },
          { value: '1', label: '合规' }, { value: '0', label: '不合规' },
        ]} onChange={status => setReference({ ...reference, status: status || undefined, reason: undefined, cursor: undefined })} />
        {reference.reason && <Button onClick={() => setReference({ ...reference, reason: undefined, cursor: undefined })}>清除原因筛选</Button>}
      </Space>}
      {evidenceError && <Alert type="warning" title={evidenceError} action={reference && <Button onClick={() => setReference({ ...reference })}>重试</Button>} />}
      <List loading={loading} dataSource={page?.rows ?? []} renderItem={row => <List.Item>
        <Space direction="vertical" style={{ width: '100%' }}>
          <div>{String(row.sample_id ?? row.row_id ?? '—')} · {row.status === 'NOT_EVALUATED' ? '未判定' : row.status === '1' ? '合规' : row.status === '0' ? '不合规' : String(row.status ?? '')}</div>
          {typeof row.reason === 'string' && <div>{row.reason.split('|').map(reasonLabel).join('、')}</div>}
          <Collapse items={[{ key: 'row', label: '判定技术详情', children: <pre className="evidence-row-json">{JSON.stringify(row, null, 2)}</pre> }]} />
          {reference && typeof row.sample_id === 'string' && <Button size="small"
            onClick={() => viewSample(reference.version, String(row.sample_id))}>查看完整样本</Button>}
        </Space>
      </List.Item>} />
    </Modal>
  </Card>
}
