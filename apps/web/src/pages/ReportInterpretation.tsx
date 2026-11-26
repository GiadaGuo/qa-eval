import { useEffect, useState } from 'react'
import { Alert, Button, Card, List, Typography } from 'antd'
import { formatStatistic, policyFormatPresentation, reasonLabel } from './calculatorPresentation'

type Item = { text: string; evidence_keys: string[] }
type Interpretation = {
  sections: { conclusion: Item; causes: Item[]; risks: Item[]; recommendations: Item[] }
  facts: Record<string, unknown>
}
type State = { status: 'NOT_REQUESTED' | 'RUNNING' | 'FAILED' | 'READY'; interpretation?: Interpretation }

function statisticEvidence(key: string, value: unknown): string {
  if (value && typeof value === 'object') {
    return Object.entries(value).map(([field, count]) => `${reasonLabel(field)} ${formatStatistic(count)}`).join('，')
  }
  const statistic = key.split('.').at(-1) ?? key
  return formatStatistic(value, policyFormatPresentation.formulas[statistic] ? 'ratio' : undefined)
}

export default function ReportInterpretation({ runId }: { runId: string }) {
  const endpoint = `/api/v1/evaluation-runs/${runId}/report-interpretation`
  const [state, setState] = useState<State>({ status: 'NOT_REQUESTED' })
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    const refresh = async () => {
      try {
        const response = await fetch(endpoint)
        if (!response.ok) throw new Error()
        const next: State = await response.json()
        if (!active) return
        setState(next)
        if (next.status === 'RUNNING') timer = setTimeout(refresh, 1500)
      } catch {
        if (active) setState({ status: 'FAILED' })
      }
    }
    void refresh()
    return () => { active = false; clearTimeout(timer) }
  }, [endpoint, state.status === 'RUNNING'])

  const generate = async () => {
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
  const result = state.interpretation
  return <Card title="智能解读" extra={<Button onClick={() => void generate()} loading={submitting || state.status === 'RUNNING'}>
    {state.status === 'FAILED' || state.status === 'READY' ? '重新生成' : '生成解读'}
  </Button>}>
    {state.status === 'FAILED' && <Alert type="info" title="智能解读暂不可用，可继续查阅报告中的统计和建议。" />}
    {state.status === 'RUNNING' && <Typography.Text>正在生成解读</Typography.Text>}
    {result && ([
      ['核心结论', [result.sections.conclusion]], ['主要原因', result.sections.causes],
      ['风险提示', result.sections.risks], ['整改建议', result.sections.recommendations],
    ] as [string, Item[]][]).map(([title, items]) => <List key={String(title)} header={title} dataSource={items}
      renderItem={item => <List.Item><div>{item.text}<br /><Typography.Text type="secondary">
        依据 {item.evidence_keys.map(key => `${policyFormatPresentation.statistics[key.split(".").at(-1) ?? key] ?? key} ${statisticEvidence(key, result.facts[key])}`).join('、')}
      </Typography.Text></div></List.Item>} />)}
  </Card>
}
