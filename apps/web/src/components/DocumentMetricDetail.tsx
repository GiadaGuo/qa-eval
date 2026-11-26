import { CheckCircleOutlined, DownOutlined, MinusCircleOutlined, WarningOutlined } from '@ant-design/icons'
import { Button, Progress, Tag } from 'antd'
import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { DocumentCheck, FormalReport } from '../api/formalDocumentReports'
import './DocumentMetricDetail.css'

type Metric = FormalReport['dimensions'][number]['metrics'][number]
type Quote = DocumentCheck['quotes'][number]

const outcomes: Record<string, { label: string; tone: 'pass' | 'warning' | 'neutral' }> = {
  SATISFIED: { label: '满足', tone: 'pass' },
  INSUFFICIENT: { label: '信息不足', tone: 'warning' },
  MISSING: { label: '缺失', tone: 'warning' },
  UNVERIFIABLE: { label: '无法核实', tone: 'warning' },
  ERROR: { label: '判定失败', tone: 'warning' },
  NOT_APPLICABLE: { label: '不适用', tone: 'neutral' },
  NOT_EVALUATED: { label: '未评测', tone: 'neutral' },
}

function positionLabels(blocks: Quote['blocks']) {
  const groups = new Map<string, { unit: string; numbers: Set<number> }>()
  const add = (prefix: string, unit: string, value: unknown) => {
    if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 1) return
    const key = prefix + unit
    if (!groups.has(key)) groups.set(key, { unit, numbers: new Set() })
    groups.get(key)!.numbers.add(value)
  }
  for (const { locator } of blocks) {
    if (Array.isArray(locator.table_path) && locator.table_path.length) {
      const path = locator.table_path as number[][]
      const last = path.at(-1)
      if (last) {
        const parents = path.slice(0, -1).map(([table, row, cell]) => `表格${table}·第${row}行·第${cell}列·`)
        add(`${parents.join('')}表格${last[0]}·`, '行', last[1])
      }
    } else if (typeof locator.page === 'number') {
      if (typeof locator.line_on_page === 'number') add(`第${locator.page}页·`, '行', locator.line_on_page)
      else add('', '页', locator.page)
    } else {
      add('', '行', locator.line)
      add('', '段', locator.paragraph)
    }
  }
  return [...groups].flatMap(([key, { unit, numbers }]) => {
    const prefix = key.slice(0, -unit.length)
    const ranges: Array<[number, number]> = []
    for (const value of [...numbers].sort((a, b) => a - b)) {
      const last = ranges.at(-1)
      if (last && last[1] + 1 === value) last[1] = value
      else ranges.push([value, value])
    }
    return ranges.map(([start, end]) => `${prefix}第${start}${start === end ? '' : `–${end}`}${unit}`)
  })
}

function EvidenceQuote({ quote }: { quote: Quote }) {
  const [expanded, setExpanded] = useState(false)
  const [overflow, setOverflow] = useState(false)
  const viewport = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const frame = viewport.current
    const body = content.current
    if (!frame || !body || expanded) return
    const observer = new ResizeObserver(() => {
      if (frame.clientHeight) setOverflow(frame.scrollHeight > frame.clientHeight + 1)
    })
    observer.observe(frame)
    observer.observe(body)
    return () => observer.disconnect()
  }, [expanded])

  const labels = positionLabels(quote.blocks)
  return <div className="doc-check-quote">
    <div ref={viewport} className={expanded ? undefined : 'doc-check-preview'}>
      <div className="doc-check-markdown" ref={content}>
        <Markdown remarkPlugins={[remarkGfm]} components={{
          img: ({ alt }) => alt ?? '',
          table: ({ children }) => <div className="doc-check-table"><table>{children}</table></div>,
        }}>{quote.excerpt}</Markdown>
      </div>
    </div>
    {(overflow || expanded) && <Button type="link" size="small" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
      {expanded ? '收起全文' : '展开全文'}
    </Button>}
    <div className="doc-check-location">
      <span>来源定位</span>
      {labels.length ? labels.map(label => <Tag key={label}>{label}</Tag>) : <span>暂无位置</span>}
    </div>
  </div>
}

function CheckCard({ check, name }: { check: DocumentCheck; name?: string }) {
  const { label, tone } = outcomes[check.outcome] ?? { label: check.outcome, tone: 'neutral' }
  const Icon = tone === 'pass' ? CheckCircleOutlined : tone === 'warning' ? WarningOutlined : MinusCircleOutlined
  return <details className={`doc-check doc-check-${tone}`} open={tone === 'warning'}>
    <summary>
      <Icon className="doc-check-status-icon" />
      <span className="doc-check-title">
        <strong>{name ?? check.item_code}</strong>
        {name && <small>{check.item_code}</small>}
      </span>
      <Tag className="doc-check-status">{label}</Tag>
      <DownOutlined className="doc-check-chevron" />
      {check.reason && <span className="doc-check-reason-preview">{check.reason}</span>}
    </summary>
    <div className="doc-check-body">
      <div><strong>判定理由</strong><p>{check.reason || '暂无判定理由'}</p></div>
      <div><strong>原文证据</strong>
        {check.quotes.length ? check.quotes.map((quote, index) => <EvidenceQuote key={index} quote={quote} />) : <p>暂无原文证据</p>}
      </div>
    </div>
  </details>
}

export function DocumentMetricDetail({ metric, name, score, itemNames }: {
  metric: Metric
  name: string
  score: string
  itemNames?: Record<string, string>
}) {
  const satisfied = metric.satisfied_aspect_count
  const required = metric.required_aspect_count
  const rate = satisfied != null && required != null && required > 0 ? 100 * satisfied / required : null

  return <div className="doc-metric-detail">
    <section className="doc-metric-overview" aria-label={`${name}概览`}>
      <div className="doc-metric-heading"><strong>{name}</strong><small>{metric.key}</small></div>
      <div className="doc-metric-stats">
        <span>得分 <strong>{score}</strong></span>
        <span>满足项 <strong>{satisfied ?? '—'}/{required ?? '—'}</strong></span>
        <span>完整率 <strong>{rate === null ? '—' : `${rate.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}%`}</strong></span>
      </div>
      {rate !== null && <Progress aria-label="完整率" percent={rate} showInfo={false} size={['100%', 4]} strokeColor="#638a76" />}
    </section>
    {metric.checks?.length
      ? metric.checks.map(check => <CheckCard key={check.item_code} check={check} name={itemNames?.[check.item_code]} />)
      : <p className="doc-check-empty">{metric.status === 'NOT_APPLICABLE' ? '该指标不适用' : '暂无检查项明细'}</p>}
  </div>
}
