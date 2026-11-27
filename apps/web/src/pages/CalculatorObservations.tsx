import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Collapse, Descriptions, List, Drawer, Progress, Select, Space, Statistic } from 'antd'

import { getSampleById, type SampleById } from '../api/datasetVersions'
import { object, rows, samplePreview, verifiedExcerpt, type Row } from './samplePresentation'
import DecisionExport from './DecisionExport'
import { getCalculatorResults, type CalculatorResult } from '../api/evaluationRuns'
import { getEvidenceMetadata, rebuildEvidenceIndex } from '../api/qualityResults'

import type { QualityReport } from '../api/qualityResults'
import { formatStatistic, policyFormatPresentation, reasonLabel } from './calculatorPresentation'

type EvidenceRow = Record<string, unknown>
type EvidencePage = { rows: EvidenceRow[]; next_cursor: string | null }
type Summary = { total: number; undecided: number; reasons: { code: string; records: number }[] }
type SamplePreview = ReturnType<typeof samplePreview> & { error?: string; anchors?: { location: Row; text: string }[] }

async function evidenceJson<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal })
  if (!response.ok) {
    const payload = await response.json().catch(() => undefined)
    const detail = payload?.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message ?? detail?.code ?? `HTTP ${response.status}`)
  }
  return response.json()
}

function statusLabel(value: unknown) {
  if (value === 'NOT_EVALUATED') return '未判定'
  if (value === '1') return '合规'
  if (value === '0') return '不合规'
  return reasonLabel(String(value))
}

function comparisons(row: Row, scope = "value") {
  const labels: Record<string, string> = {
    actual: '实际值', actual_value: '实际值', threshold: '阈值', score: '分数',
    observed_score: '观测分数', score_bounds: '分数区间', score_interval: '分数区间',
    coverage: '覆盖率', excerpt: '证据片段',
  }
  return Object.entries(labels).flatMap(([key, label]) => {
    const value = row[key]
    return typeof value === 'string' || typeof value === 'number' || (Array.isArray(value) && value.every(item => typeof item === 'number'))
      ? [<Descriptions.Item key={`${scope}-${key}`} label={label}>{Array.isArray(value) ? value.map(item => formatStatistic(item)).join(' ～ ') : formatStatistic(value, key === 'coverage' ? 'ratio' : undefined)}</Descriptions.Item>]
      : []
  })
}

function DecisionBasis({ row, preview, locate }: { row: Row; preview?: SamplePreview; locate: (location: Row) => void }) {
  const attributes = object(row.attributes)
  const groups = [row, attributes, ...rows(attributes.segments)]
  return <Space orientation="vertical" style={{ width: '100%' }}>
    <Descriptions size="small" column={2}>
      {row.status != null && <Descriptions.Item key="最终状态" label="最终状态">{statusLabel(row.status)}</Descriptions.Item>}
      {typeof row.reason === 'string' && row.reason && <Descriptions.Item key="reason" label={row.status === 'NOT_EVALUATED' ? '未判定原因' : '判定原因'}>{row.reason.split('|').map(reasonLabel).join('、')}</Descriptions.Item>}
      {comparisons(row)}
      {comparisons(attributes, "attributes")}
    </Descriptions>
    {groups.flatMap((group, index) => {
      const rules = rows(group.rules)
      const values = index > 1 ? comparisons(group) : []
      if (!rules.length && !values.length) return []
      return [<section key={`group-${index}`}>
        {values.length > 0 && <Descriptions size="small" column={2}>{values}</Descriptions>}
        {rules.length > 0 && <Collapse size="small" items={[{ key: 'rules', label: `规则结果（${rules.length}）`, children: rules.map((rule, ruleIndex) =>
          <Descriptions key={ruleIndex} size="small" column={2}>
            {typeof rule.id === 'string' && <Descriptions.Item key="规则" label="规则">{reasonLabel(rule.id)}</Descriptions.Item>}
            {rule.status != null && <Descriptions.Item key="结果" label="结果">{statusLabel(rule.status)}</Descriptions.Item>}
            {typeof rule.reason === 'string' && <Descriptions.Item key="原因" label="原因">{reasonLabel(rule.reason)}</Descriptions.Item>}
            {comparisons(rule)}
          </Descriptions>) }]} />}
      </section>]
    })}
    {rows(attributes.relations).filter(edge => edge.method === 'embedding_match').map(edge => {
      const fields = object(attributes.fields)
      const details = object(edge.details)
      const fieldName = (alias: unknown) => {
        const source = object(object(fields[String(alias)]).source)
        const tokens = object(source.source_origin).field_tokens
        return Array.isArray(tokens) ? tokens.join(' / ') : String(alias)
      }
      return <section key={String(edge.id)}>
        <strong>{fieldName(edge.left)} ↔ {fieldName(edge.right)}</strong>
        <Descriptions size="small" column={2}>
          <Descriptions.Item label="匹配结果">{statusLabel(edge.status)}</Descriptions.Item>
          <Descriptions.Item label="判定原因">{reasonLabel(String(edge.reason))}</Descriptions.Item>
          {typeof edge.raw_score === 'number' && <Descriptions.Item label="相似度">{formatStatistic(edge.raw_score)}</Descriptions.Item>}
          {typeof details.fail_below === 'number' && <Descriptions.Item label="不一致阈值">{formatStatistic(details.fail_below)}</Descriptions.Item>}
          {typeof details.pass_at === 'number' && <Descriptions.Item label="一致阈值">{formatStatistic(details.pass_at)}</Descriptions.Item>}
        </Descriptions>
        {(['left', 'right'] as const).map(side => {
          const fragment = object(details[`${side}_fragment`])
          return typeof fragment.excerpt === 'string' && <div key={side}>
            <strong>{fieldName(edge[side])}</strong> · {fragment.excerpt}
          </div>
        })}
      </section>
    })}
    {rows(attributes.hits).slice(0, 5).map((hit, index) => <div key={`hit-${index}`}>
      <strong>{String(hit.term ?? '')}</strong> · {String(hit.dictionary_id ?? '')}@{String(hit.dictionary_version ?? '')}
      {Array.isArray(hit.field_tokens) && <span> · {hit.field_tokens.join(' / ')}</span>}
      {typeof hit.excerpt === 'string' && <div>{hit.excerpt}</div>}
    </div>)}
    {preview?.anchors?.map(({ location, text }, index) => <div key={`anchor-${index}`}>
      <Button type="link" onClick={() => locate(location)}>定位原文</Button><span>{text}</span>
    </div>)}
    <Collapse items={[{ key: 'debug', label: '高级调试', children: <pre className="evidence-row-json">{JSON.stringify(row, null, 2)}</pre> }]} />
  </Space>
}
type EvidenceReference = { id: string; version: string; cursor?: string; status?: string; reason?: string; decision?: boolean; frequencies?: Row; previous?: (string | undefined)[] }
type DecisionGroup = { code: string; rows?: EvidenceRow[] }
type EvidenceResponse = { reference: EvidenceReference; page?: EvidencePage; groups?: DecisionGroup[]; error?: string }

export default function CalculatorObservations({ runId, status, nativeEvaluations, viewSample, sampleOpen, reportResults }: {
  runId: string
  status: string
  nativeEvaluations?: QualityReport["native_evaluations"]
  viewSample: (versionId: string, sampleId: string, decision?: Row, location?: Row, restore?: () => void) => void
  sampleOpen: boolean
  reportResults?: CalculatorResult[]
}) {
  const drawerBody = useRef<HTMLDivElement>(null)
  const [results, setResults] = useState<CalculatorResult[]>([])
  const [error, setError] = useState<string>()
  const [reference, setReference] = useState<EvidenceReference>()
  const [evidence, setEvidence] = useState<EvidenceResponse>()
  const [summaryState, setSummaryState] = useState<{ id: string; summary: Summary }>()
  const [previews, setPreviews] = useState<{ reference: EvidenceReference; items: Record<string, SamplePreview> }>()
  const summary = summaryState?.id === reference?.id ? summaryState?.summary : undefined
  const needsSummary = reference?.status === 'NOT_EVALUATED' && !summary
  const [expanded, setExpanded] = useState<{ reference: EvidenceReference; code?: string }>()
  const currentEvidence = evidence?.reference === reference ? evidence : undefined
  const page = currentEvidence?.page
  const undecided = reference?.status === 'NOT_EVALUATED'
  const groups = currentEvidence?.groups
  const selectedGroup = expanded?.reference === reference ? expanded?.code : groups?.[0]?.code
  const [groupEvidence, setGroupEvidence] = useState<{ reference: EvidenceReference; code: string; rows?: EvidenceRow[]; error?: string }>()
  const currentGroup = groupEvidence?.reference === reference && groupEvidence?.code === selectedGroup ? groupEvidence : undefined
  const plannedGroup = groups?.find(group => group.code === selectedGroup)
  const visibleRows = undecided ? plannedGroup?.rows ?? currentGroup?.rows : page?.rows
  const evidenceError = currentEvidence?.error
  const loading = !!reference && !currentEvidence

  useEffect(() => {
    if (reportResults) return
    let active = true
    getCalculatorResults(runId).then(rows => {
      if (active) { setResults(rows); setError(undefined) }
    }).catch(reason => { if (active) setError(reason.message) })
    return () => { active = false }
  }, [runId, status, reportResults])

  useEffect(() => {
    if (!reference) return
    const controller = new AbortController()
    const load = async () => {
      if (!reference.decision) {
        const metadata = await getEvidenceMetadata(reference.id)
        if (metadata.index.status !== 'READY') await rebuildEvidenceIndex(reference.id)
      }
      const params = new URLSearchParams()
      if (reference.cursor) params.set("cursor", reference.cursor)
      if (reference.status) params.set("status", reference.status)
      if (reference.reason) params.set("reason", reference.reason)
      const root = `/api/v1/evidence/${reference.id}`
      if (reference.status === 'NOT_EVALUATED') {
        const query = reference.reason ? `?reason=${encodeURIComponent(reference.reason)}` : '?plan=true'
        if (needsSummary) {
          const counts = await evidenceJson<Summary>(`${root}/decision-summary`, controller.signal)
          if (!controller.signal.aborted) setSummaryState({ id: reference.id, summary: counts })
        }
        const data = await evidenceJson<{ groups: DecisionGroup[] }>(`${root}/decision-preview${query}`, controller.signal)
        if (!controller.signal.aborted) setEvidence({ reference, groups: data.groups })
      } else {
        const data = await evidenceJson<EvidencePage>(`${root}/rows?${params}`, controller.signal)
        if (!controller.signal.aborted) setEvidence({ reference, page: data })
      }
    }
    load().catch(reason => { if (!controller.signal.aborted) setEvidence({ reference, error: reason.message }) })
    return () => controller.abort()
    // Summary is cached across pages using the artifact ID.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reference])

  useEffect(() => {
    if (!reference || !plannedGroup || plannedGroup.rows) return
    const controller = new AbortController()
    const code = plannedGroup.code
    const url = `/api/v1/evidence/${reference.id}/decision-preview?group=${encodeURIComponent(code)}`
    evidenceJson<{ groups: DecisionGroup[] }>(url, controller.signal).then(data => {
      if (!controller.signal.aborted) setGroupEvidence({ reference, code, rows: data.groups[0]?.rows ?? [] })
    }).catch(error => {
      if (!controller.signal.aborted) setGroupEvidence({ reference, code, error: error.message })
    })
    return () => controller.abort()
  }, [reference, plannedGroup])

  useEffect(() => {
    if (!reference?.decision || !visibleRows) return
    const controller = new AbortController()
    const sources = new Map<string, Promise<SampleById>>()
    visibleRows.forEach(async row => {
      const id = row.sample_id
      if (typeof id !== 'string') return
      let item: SamplePreview
      try {
        let request = sources.get(id)
        if (!request) {
          request = getSampleById(reference.version, id, controller.signal, true)
          sources.set(id, request)
        }
        const sample = await request
        const anchors = await Promise.all(rows(row.locations).slice(0, 5).map(async location => {
          const excerpt = await verifiedExcerpt(sample, row, location)
          return excerpt ? [{ location, text: Array.from(excerpt.text).slice(0, 180).join('') }] : []
        }))
        item = { ...samplePreview(sample, row), anchors: anchors.flat() }
      } catch (error) {
        item = { title: undefined, summary: undefined, error: error instanceof Error ? error.message : String(error) }
      }
      if (!controller.signal.aborted) setPreviews(previous => ({
        reference, items: { ...(previous?.reference === reference ? previous.items : {}), [String(row.row_id ?? id)]: item },
      }))
    })
    return () => controller.abort()
  }, [reference, visibleRows])

  if (error) return <Alert type="warning" title={error} />
  if (!(reportResults ?? results).length) return null

  const sampleList = <List loading={loading || (!!plannedGroup && !visibleRows && !currentGroup?.error)} rowKey={row => String(row.row_id ?? row.sample_id)} dataSource={visibleRows ?? []} renderItem={row => {
    const id = typeof row.sample_id === 'string' ? row.sample_id : undefined
    const preview = previews?.reference === reference ? previews?.items[String(row.row_id ?? id)] : undefined
    const open = (location?: Row) => {
      if (!reference || !id) return
      const scroll = drawerBody.current?.parentElement
      const top = scroll?.scrollTop
      const trigger = document.activeElement
      viewSample(reference.version, id, row, location, () => {
        if (trigger instanceof HTMLElement) trigger.focus({ preventScroll: true })
        if (scroll && top !== undefined) scroll.scrollTop = top
      })
    }
    return <List.Item><Space orientation="vertical" style={{ width: '100%' }}>
      <strong>{reference?.decision ? preview?.title || (preview ? '无可用标题' : '正在加载原文摘要…') : String(row.sample_id ?? row.row_id ?? '—')}</strong>
      {preview?.summary && <div>{preview.summary}</div>}
      {preview?.error && <Alert type="warning" title={preview.error} />}
      {typeof row.reason === 'string' && <div>{row.reason.split('|').map(reasonLabel).join('、')}</div>}
      <Collapse items={[{ key: 'row', label: reference?.decision ? '判定依据' : '证据详情',
        children: reference?.decision ? <DecisionBasis row={row} preview={preview} locate={open} /> : <pre className="evidence-row-json">{JSON.stringify(row, null, 2)}</pre> }]} />
      {id && <Button size="small" onClick={() => open()}>查看完整样本</Button>}
    </Space></List.Item>
  }} />
  const groupCodes = new Set(groups?.map(group => group.code))
  const groupedPreview = <Collapse accordion activeKey={selectedGroup} onChange={keys => reference && setExpanded({ reference, code: keys[0] })}
      items={summary?.reasons.filter(({ code }) => !reference?.reason || code === reference.reason).map(({ code, records }) => ({
        key: code,
        label: `${reasonLabel(code)} · ${formatStatistic(records)} 条去重记录`,
        collapsible: groupCodes.has(code) ? undefined : 'disabled',
        extra: !groupCodes.has(code) && reference ? <Button size="small" onClick={event => {
          event.stopPropagation()
          setReference({ ...reference, reason: code })
        }}>查看此类</Button> : undefined,
        children: selectedGroup === code ? currentGroup?.error
          ? <Alert type="warning" title={currentGroup.error} action={reference && <Button onClick={() => setReference({ ...reference })}>重试</Button>} />
          : sampleList : null,
      }))} />

  return <Card size={reportResults ? "small" : undefined} title={reportResults ? "专项分析" : "算子评测明细"}>
    <List dataSource={reportResults ?? results} rowKey="calculator_result_id" renderItem={result => {
      const policy = result.calculator_key === 'policy_document_format'
      const metadata = policy ? policyFormatPresentation : undefined
      const label = (key: string) => policyFormatPresentation.statistics[key] ?? key
      const stats = result.statistics
      const decisions = result.decision_artifact_refs[0]
      const drill = (reason?: string) => {
        if (decisions) setReference({ id: decisions.evidence_id, version: result.dataset_version_id, status: 'NOT_EVALUATED', reason, decision: true, frequencies: object(stats.unknown_reasons) })
      }
      const headline = policy ? ['compliant_record_ratio', 'evaluated_compliance_ratio', 'evaluable_rate'] : []
      const fields = result.output_schema.statistics.filter(stat => stat.key in stats)
      const rules = nativeEvaluations?.filter(({ evaluation }) => evaluation.calculator_result_id === result.calculator_result_id) ?? []
      return <List.Item><Space orientation="vertical" style={{ width: '100%' }}>
        <strong>{policy ? '政策公文格式' : result.contract_item_key}</strong>
        {!reportResults && <Space wrap size="large">{headline.filter(key => key in stats).map(key =>
          <Statistic key={key} title={label(key)} value={formatStatistic(stats[key], 'ratio')} />)}</Space>}
        {!reportResults && rules.map(({ evaluation: rule }) => <Alert key={rule.rule_evaluation_id}
          type={rule.status === 'FAIL' ? 'warning' : 'info'}
          title={rule.status === 'FAIL' ? '门禁未通过' : rule.status === 'PASS' ? '门禁通过' : '门禁未判定'}
          description={`${label(rule.statistic_ref.statistics_key)} ${formatStatistic(rule.metric_value, rule.statistic.unit)}，阈值 ${({ GTE: '≥', GT: '>', LTE: '≤', LT: '<', EQ: '=' } as Record<string, string>)[rule.rule.operator] ?? rule.rule.operator} ${formatStatistic(rule.rule.expected, rule.statistic.unit)}。 ${rule.numerator != null && rule.denominator != null ? `分子 ${formatStatistic(rule.numerator)} / 分母 ${formatStatistic(rule.denominator)}。` : ''} ${rule.status === 'FAIL' ? reasonLabel(rule.reason_code) : ''}`} />)}
        {!reportResults && policy && Number(stats.not_evaluated_count) > 0 && <Alert type="info" title={Number(stats.non_compliant_record_count) === 0 ? "未能判定 · 未发现不合规记录" : "未能判定"}
          description={`${formatStatistic(stats.not_evaluated_count)} 条记录未能判定。总体合规率以全部输入为分母，未判定记录会拉低总体合规率。已评测合规率仅以已判定记录为分母。${Number(stats.non_compliant_record_count) === 0 && rules.some(({ evaluation }) => evaluation.status === "FAIL" && evaluation.statistic_ref.statistics_key === "compliant_record_ratio") ? "本次门禁未达标源于未判定记录拉低总体合规率，已判定记录全部合规。" : ""}`}
          action={decisions && <Button size="small" onClick={() => drill()}>查看未判定记录</Button>} />}
        {(reportResults || !policy) && decisions && <Button onClick={() => drill()}>查看未判定记录</Button>}
        <Descriptions size="small" column={{ xs: 1, sm: 2 }}>
          {fields.filter(stat => (stats[stat.key] === null || typeof stats[stat.key] !== 'object') &&
            (!reportResults || (!['compliant_record_count', 'non_compliant_record_count', 'not_evaluated_count', 'evaluable_rate'].includes(stat.key) &&
              !rules.some(({ evaluation }) => evaluation.statistic_ref.statistics_key === stat.key)))).map(stat => {
            const formula = metadata?.formulas[stat.key] ?? (stat.numerator_key && stat.denominator_key ? [stat.numerator_key, stat.denominator_key] : null)
            return <Descriptions.Item key={stat.key} label={label(stat.key)}>
              <span title={`${stat.method} · ${stat.population}`}>{headline.includes(stat.key) ? null : formatStatistic(stats[stat.key], stat.unit)}
                {formula && ` ${label(formula[0])} ÷ ${label(formula[1])}（${formatStatistic(stats[formula[0]])} / ${formatStatistic(stats[formula[1]])}）`}
                {formula && stats[stat.key] === null && '，分母为零时无法计算'}
              </span>
            </Descriptions.Item>
          })}
        </Descriptions>
        <div className={reportResults ? "single-report-specialized" : undefined}>{fields.filter(stat => stats[stat.key] && typeof stats[stat.key] === 'object' && !Array.isArray(stats[stat.key])).map(stat => {
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
              <Progress percent={count / (rows[0].count || 1) * 100} strokeColor="#788b9c" format={() => formatStatistic(count)} />
            </div>)}
            <Collapse items={[{ key: 'all', label: reportResults ? '展开全部' : '查看全部分类', children: <table>
              <thead><tr><th>{reasons ? '原因' : '分类'}</th>{reasons && <th>类别</th>}<th>次数</th></tr></thead>
              <tbody>{rows.map(({ code, count }) => <tr key={code}><td>{reasons && decisions
                ? <Button type="link" onClick={() => drill(code)}>{reasonLabel(code)}</Button> : reasonLabel(code)}</td>{reasons && <td>{code.startsWith('N') && /^N[1-8]\//.test(code) ? '规范核验' : code.includes('/') ? '模板要素' : '记录判定'}</td>}<td>{formatStatistic(count)}</td></tr>)}</tbody>
            </table> }]} />
          </section>
        })}</div>
        {!reportResults && policy && Number(stats.not_evaluated_count) > 0 && <Alert type="info" title="整改建议"
          description="优先抽查排名靠前的未判定原因，对照原文核实公文类型、模板路径和关键格式字段，再复核记录覆盖率。" />}
        <Space wrap>
          {result.decision_artifact_refs.map(ref => <Button key={ref.evidence_id} size="small"
            onClick={() => setReference({ id: ref.evidence_id, version: result.dataset_version_id, decision: true, frequencies: object(stats.unknown_reasons) })}>查看判定与原文证据</Button>)}
          {result.evidence_artifact_refs.map(ref => <Button key={ref.evidence_id} size="small"
            onClick={() => setReference({ id: ref.evidence_id, version: result.dataset_version_id })}>查看证据</Button>)}
        </Space>
        {!reportResults && <Collapse items={[{ key: 'technical', label: '技术详情', children: <>
          <div>{result.calculator_key}@{result.calculator_version} · {result.status} · 展示版本 {metadata?.version ?? '通用'}</div>
          <pre className="evidence-row-json">{JSON.stringify(stats, null, 2)}</pre>
        </> }]} />}
      </Space></List.Item>
    }} />
    <Drawer open={!!reference} title={reference?.status === 'NOT_EVALUATED' ? '未判定记录' : '算子判定与证据'}
      size="min(1024px, 100vw)" keyboard={!sampleOpen} onClose={() => setReference(undefined)}
      footer={reference && !undecided && <Space>
        {!!reference.previous?.length && <Button onClick={() => setReference({
          ...reference, cursor: reference.previous?.at(-1), previous: reference.previous?.slice(0, -1),
        })}>上一页</Button>}
        {page?.next_cursor && <Button onClick={() => setReference({
          ...reference, cursor: page.next_cursor ?? undefined, previous: [...(reference.previous ?? []), reference.cursor],
        })}>下一页</Button>}
      </Space>}>
      <div ref={drawerBody}>
        {reference?.decision && <Space wrap style={{ marginBottom: 12 }}>
          <Select aria-label="记录判定状态" value={reference.status ?? ''} options={[
            { value: '', label: '全部判定' }, { value: 'NOT_EVALUATED', label: '未判定' },
            { value: '1', label: '合规' }, { value: '0', label: '不合规' },
          ]} onChange={status => setReference({ ...reference, status: status || undefined, reason: undefined, cursor: undefined, previous: undefined })} />
        </Space>}
        {reference?.status === 'NOT_EVALUATED' && summary && <Space orientation="vertical" style={{ width: '100%', marginBottom: 12 }}>
          <Space wrap size="large">
            <Statistic title="未判定记录" value={formatStatistic(summary.undecided)} />
            <Statistic title="占索引内去重记录比例" value={summary.total ? formatStatistic(summary.undecided / summary.total, 'ratio') : '—'} />
          </Space>
          <div>各原因分类可重叠。同一记录在每类只计一次，出现频次沿用算子统计。</div>
          <Select aria-label="未判定原因" virtual={false} style={{ width: '100%' }} value={reference.reason ?? ''} options={[
            { value: '', label: '全部原因' },
            ...summary.reasons.map(({ code, records }) => ({
              value: code, label: `${reasonLabel(code)} · ${formatStatistic(records)} 条去重记录${typeof reference.frequencies?.[code] === 'number' ? ` · ${formatStatistic(reference.frequencies[code])} 次出现` : ''}`,
            })),
          ]} onChange={reason => setReference({ ...reference, reason: reason || undefined, cursor: undefined, previous: undefined })} />
          <strong>典型样本预览</strong>
          <div>每类最多 3 条，共最多 50 条。优先覆盖不同原因，展开分组时读取原文摘要。</div>
          <DecisionExport key={reference.id} evidenceId={reference.id} reason={reference.reason ?? selectedGroup}
            reasonName={reasonLabel(reference.reason ?? selectedGroup ?? '')} />
        </Space>}
        {evidenceError && <Alert type="warning" title={evidenceError} action={reference && <Button onClick={() => setReference({ ...reference })}>重试</Button>} />}
        {undecided && !loading && groups?.length ? groupedPreview : sampleList}
      </div>
    </Drawer>
  </Card>
}
