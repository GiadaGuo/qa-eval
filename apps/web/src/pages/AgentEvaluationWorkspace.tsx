import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  HistoryOutlined,
  PlusOutlined,
  RobotOutlined,
  SendOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Collapse,
  Descriptions,
  Empty,
  Input,
  InputNumber,
  Row,
  Segmented,
  Select,
  Space,
  Spin,
  Switch,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
  notification,
} from 'antd'

import {
  createAgentSession,
  getAgentSession,
  listAgentSessions,
  recommendationCoverage,
  sendAgentMessage,
  confirmQualityContract,
  confirmEvaluationRun,
  observeEvaluationRun,
  saveProposalDraft,
  type AgentSessionSnapshot,
  type AgentSession,
  type GuideCoverageItem,
  type ProposalItem,
  type ProposalRevision,
} from '../api/agentSessions'
import type { Dataset, DatasetVersion } from '../api/datasetVersions'
import type { EvaluationRunObservationResult } from '../api/agentSessions'

const { Text, Title, Paragraph } = Typography
const SESSION_KEY_PREFIX = 'qa-eval:agent-session:'
const QUICK_PROMPT = '请为当前数据集推荐综合质量评测方案'

interface Props {
  dataset: Dataset
  version: DatasetVersion
  onBack: () => void
}

const statusColor: Record<string, string> = {
  APPLICABLE: 'success', SUCCEEDED: 'success', ACTIVE: 'processing', RUNNING: 'processing',
  PENDING: 'default', NEEDS_INPUT: 'warning', UNAVAILABLE: 'default', NOT_APPLICABLE: 'default',
  FAILED: 'error', COMPLETED: 'success', CANCELED: 'default',
}

function AgentResponseBadge({ attachment }: { attachment?: Record<string, unknown> }) {
  if (!attachment) return null
  if (attachment.mode === 'MODEL') {
    return <Tag className="agent-mode-badge" color="purple" title="本条回复由后端配置的真实模型生成">AI 模型 · {String(attachment.model ?? 'unknown')}</Tag>
  }
  return <Tag className="agent-mode-badge" color="default" title={`模型未生成本条回复：${String(attachment.reason ?? 'unknown')}`}>确定性降级 · {String(attachment.reason ?? 'unknown')}</Tag>
}

function ParameterInput({ schema, value, onChange }: { schema: Record<string, unknown>; value: unknown; onChange: (value: unknown) => void }) {
  const type = Array.isArray(schema.type) ? schema.type.find((item) => item !== 'null') : schema.type
  if (Array.isArray(schema.enum)) return <Select style={{ width: '100%' }} value={value} options={schema.enum.map((item) => ({ label: String(item), value: item }))} onChange={onChange} />
  if (type === 'boolean') return <Switch checked={Boolean(value)} onChange={onChange} />
  if (type === 'number' || type === 'integer') return <InputNumber style={{ width: '100%' }} value={typeof value === 'number' ? value : null} min={schema.minimum as number | undefined} max={schema.maximum as number | undefined} precision={type === 'integer' ? 0 : undefined} onChange={onChange} />
  if (type === 'object' || type === 'array') return <Input.TextArea value={JSON.stringify(value ?? (type === 'array' ? [] : {}), null, 2)} autoSize={{ minRows: 2, maxRows: 8 }} onChange={(event) => { try { onChange(JSON.parse(event.target.value)) } catch { /* keep editing until valid JSON */ } }} />
  return <Input value={value == null ? '' : String(value)} onChange={(event) => onChange(event.target.value)} />
}

function ProposalItemEditor({ item, onChange }: { item: ProposalItem; onChange: (item: ProposalItem) => void }) {
  const properties = item.parameter_schema.properties ?? {}
  const required = new Set(item.parameter_schema.required ?? [])
  const parameterEntries = Object.entries(properties).filter(([key]) => key !== 'content_role')
  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <Checkbox checked={item.enabled} onChange={(event) => onChange({ ...item, enabled: event.target.checked })}>纳入本次评测</Checkbox>
    <Descriptions size="small" bordered column={1}>
      <Descriptions.Item label="执行模式"><Tag>{item.execution_mode}</Tag></Descriptions.Item>
      <Descriptions.Item label="覆盖范围"><Tag color={item.semantic_coverage.level === 'PARTIAL' ? 'warning' : 'success'}>{item.semantic_coverage.level ?? 'UNKNOWN'}</Tag>{item.semantic_coverage.covered?.join('；')}</Descriptions.Item>
    </Descriptions>
    <Card size="small" title="字段绑定">
      <Select allowClear style={{ width: '100%' }} placeholder="选择 content role" value={item.dataset_binding.content_role as string | undefined} options={item.available_content_roles.map((role) => ({ label: role, value: role }))} onChange={(value) => onChange({ ...item, dataset_binding: value ? { ...item.dataset_binding, content_role: value } : {} })} />
    </Card>
    <Card size="small" title="计算参数">
      <Space direction="vertical" style={{ width: '100%' }}>
        {parameterEntries.length === 0 && <Text type="secondary">此 Calculator 无可编辑计算参数</Text>}
        {parameterEntries.map(([key, schema]) => <div key={key}><Text strong>{key}{required.has(key) ? ' *' : ''}</Text><ParameterInput schema={schema} value={item.parameters[key] ?? schema.default} onChange={(value) => onChange({ ...item, parameters: { ...item.parameters, [key]: value } })} /></div>)}
      </Space>
    </Card>
    <Card size="small" title="质量判定规则">
      <Select style={{ width: '100%', marginBottom: 8 }} value={item.rule.strategy} options={[{ value: 'THRESHOLD', label: '阈值判定' }, { value: 'OBSERVE_ONLY', label: '仅观察（不产生 PASS / FAIL）' }]} onChange={(strategy) => onChange({ ...item, rule: strategy === 'OBSERVE_ONLY' ? { ...item.rule, strategy, operator: null, threshold: null } : { ...item.rule, strategy, operator: 'GTE', threshold: null } })} />
      {item.rule.strategy === 'THRESHOLD' && <Row gutter={8}>
        <Col span={8}><Select style={{ width: '100%' }} value={item.rule.operator} options={['LT', 'LTE', 'GT', 'GTE', 'EQ', 'NE'].map((value) => ({ value }))} onChange={(operator) => onChange({ ...item, rule: { ...item.rule, operator } })} /></Col>
        <Col span={16}><InputNumber style={{ width: '100%' }} placeholder={`阈值（${item.measurement_definition.unit ?? '数值'}）`} value={typeof item.rule.threshold === 'number' ? item.rule.threshold : null} min={item.measurement_definition.minimum} max={item.measurement_definition.maximum} onChange={(threshold) => onChange({ ...item, rule: { ...item.rule, threshold } })} /></Col>
      </Row>}
      {item.metric.key === '02080101' && <Alert style={{ marginTop: 8 }} type="info" showIcon message="PPL 是原始困惑度；这里不会自动映射为 0～1 分数。" />}
    </Card>
  </Space>
}

function ProposalPanel({ snapshot, onRefresh, runObservation }: { snapshot: AgentSessionSnapshot; onRefresh: () => Promise<void>; runObservation: EvaluationRunObservationResult | null }) {
  const revision = snapshot.active_proposal_revision
  const coverage = recommendationCoverage(snapshot)
  const [view, setView] = useState<'proposal' | 'coverage'>('proposal')
  const [confirmed, setConfirmed] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [submittingRun, setSubmittingRun] = useState(false)
  const [contractName, setContractName] = useState('数据集综合质量评测契约')
  const [draft, setDraft] = useState<ProposalRevision['proposal'] | null>(revision?.proposal ?? null)
  const [saving, setSaving] = useState(false)
  const published = snapshot.messages
    .find((item) => item.attachments.some((value) => value.type === 'HUMAN_CONFIRMATION' && value.proposal_revision_id === revision?.proposal_revision_id))
    ?.attachments.find((attachment) => attachment.type === 'QUALITY_CONTRACT')
  const activeRun = snapshot.messages.flatMap((item) => item.attachments).find(
    (attachment) => attachment.type === 'EVALUATION_RUN' && attachment.contract_version_id === published?.contract_version_id,
  )

  const publish = async () => {
    if (!revision || !confirmed) return
    setPublishing(true)
    try {
      const result = await confirmQualityContract(
        snapshot.session.session_id,
        revision.proposal_revision_id,
        contractName.trim(),
        `proposal-r${revision.revision}`,
      )
      message.success(`已发布 Contract ${result.contract_version_id}，共 ${result.item_count} 个 items`)
      await onRefresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '发布 Quality Contract 失败')
    } finally { setPublishing(false) }
  }
  const submitRun = async () => {
    if (!published?.contract_version_id) return
    setSubmittingRun(true)
    try {
      const result = await confirmEvaluationRun(snapshot.session.session_id, String(published.contract_version_id))
      localStorage.setItem(`qa-eval:active-run:${snapshot.session.session_id}`, result.run_id)
      message.success(`已提交 Evaluation Run ${result.run_id}`)
      await onRefresh()
    } catch (error) {
      message.error(error instanceof Error ? error.message : '提交 Evaluation Run 失败')
    } finally { setSubmittingRun(false) }
  }
  const saveDraft = async () => {
    if (!revision || !draft) return
    setSaving(true)
    try {
      await saveProposalDraft(snapshot.session.session_id, revision.proposal_revision_id, draft)
      message.success('Proposal 草稿已保存，准备状态已由后端重新校验')
      await onRefresh()
    } catch (error) { message.error(error instanceof Error ? error.message : '保存失败') }
    finally { setSaving(false) }
  }

  const proposalColumns = [
    { title: '指标', key: 'metric', render: (_: unknown, item: ProposalItem) => <Space direction="vertical" size={0}><Text strong>{item.metric.key}</Text><Text type="secondary">{item.metric.measurement}</Text></Space> },
    { title: '状态', key: 'status', render: (_: unknown, item: ProposalItem) => { const needsThreshold = item.rule.strategy === 'THRESHOLD' && item.rule.threshold == null; return <Tag color={!item.enabled ? 'default' : needsThreshold ? 'warning' : 'success'}>{!item.enabled ? '已排除' : needsThreshold ? 'NEEDS_INPUT' : '已配置'}</Tag> } },
    { title: 'Calculator', key: 'calculator', render: (_: unknown, item: ProposalItem) => <><Text>{item.selected_calculator.key}</Text><br/><Text type="secondary">v{item.selected_calculator.version} · {item.execution_mode}</Text></> },
    { title: '规则', key: 'rule', render: (_: unknown, item: ProposalItem) => <Text code>{item.rule.strategy === 'OBSERVE_ONLY' ? 'OBSERVE_ONLY' : `${item.rule.operator} ${String(item.rule.threshold)}`}</Text> },
    { title: '参数', key: 'parameters', render: (_: unknown, item: ProposalItem) => <Text code>{Object.keys(item.parameters).length} 项</Text> },
  ]
  const coverageColumns = [
    { title: '指标', key: 'metric', render: (_: unknown, item: GuideCoverageItem) => <Space direction="vertical" size={0}><Text strong>{item.metric_key}</Text><Text type="secondary">{item.metric_name}</Text></Space> },
    { title: '状态', dataIndex: 'status', key: 'status', render: (value: string) => <Tag color={statusColor[value]}>{value}</Tag> },
    { title: '说明', dataIndex: 'reason', key: 'reason' },
  ]

  if (!revision) return <div className="proposal-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="发送推荐请求后，Agent Tool Result 会在这里生成 Proposal" /></div>

  return <Space direction="vertical" size="middle" style={{ width: '100%' }}>
    <div className="workspace-panel-heading">
      <div><Title level={4}>Evaluation Proposal</Title><Text type="secondary">Revision {revision.revision} · 后端会话事实源</Text></div>
      <Tag color={revision.validation?.valid ? 'success' : 'error'} icon={revision.validation?.valid ? <CheckCircleOutlined /> : undefined}>{revision.validation?.valid ? '校验通过' : '待修正'}</Tag>
    </div>
    <Descriptions size="small" column={1} bordered>
      <Descriptions.Item label="Profile fingerprint"><Text copyable ellipsis>{revision.profile_fingerprint}</Text></Descriptions.Item>
    </Descriptions>
    <Segmented value={view} onChange={(value) => setView(value as 'proposal' | 'coverage')} options={[{ label: `可执行方案 (${revision.proposal.items.length})`, value: 'proposal' }, { label: `指南覆盖 (${coverage.length})`, value: 'coverage' }]} />
    {view === 'proposal' ? <>
      <Table rowKey="key" size="small" pagination={false} dataSource={draft?.items ?? revision.proposal.items} columns={proposalColumns} scroll={{ x: 820 }} expandable={{ expandedRowRender: (item) => <Space direction="vertical" style={{ width: '100%' }}><Paragraph>{item.rationale}</Paragraph><ProposalItemEditor item={item} onChange={(next) => setDraft((current) => current ? { ...current, items: current.items.map((value) => value.key === next.key ? next : value) } : current)} />{item.limitations.map((value) => <Alert key={value} type="warning" showIcon message={value} />)}</Space> }} />
      {!published && <Button loading={saving} onClick={() => void saveDraft()}>保存配置草稿</Button>}
      {published ? <Space direction="vertical" style={{ width: '100%' }}>
        <Alert type="success" showIcon message="多指标 Quality Contract 已发布" description={`Contract Version ID: ${String(published.contract_version_id)} · ${String(published.item_count)} items`} />
        {activeRun ? <Alert type={runObservation?.terminal ? 'success' : 'info'} showIcon message={`Evaluation Run：${String(activeRun.run_id)}`} description={`状态：${runObservation?.status ?? String(activeRun.status)}${runObservation?.gate_status ? ` · Gate：${runObservation.gate_status}` : ''}`} /> : <Button type="primary" loading={submittingRun} onClick={() => void submitRun()}>提交真实 Evaluation Run</Button>}
      </Space> : <Card size="small" title="人工确认并发布">
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input value={contractName} maxLength={255} onChange={(event) => setContractName(event.target.value)} placeholder="Contract 名称" />
          <Checkbox checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}>我已核对当前 Proposal revision，确认发布其中 {revision.proposal.items.filter((item) => item.enabled).length} 个已启用 items</Checkbox>
          <Alert type="warning" showIcon message="发布是后端写操作" description="只有勾选确认并点击下方按钮，Agent 才会调用 create_quality_contract Tool；普通对话不会触发发布。" />
          <Button type="primary" disabled={!confirmed || !contractName.trim() || !revision.validation?.valid} loading={publishing} onClick={() => void publish()}>确认并发布多指标 Contract</Button>
        </Space>
      </Card>}
    </> : <Table rowKey={(item) => `${item.metric_key}:${item.metric_version}`} size="small" pagination={false} dataSource={coverage} columns={coverageColumns} scroll={{ x: 650 }} />}
  </Space>
}

export default function AgentEvaluationWorkspace({ dataset, version, onBack }: Props) {
  const [snapshot, setSnapshot] = useState<AgentSessionSnapshot | null>(null)
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [sending, setSending] = useState(false)
  const [input, setInput] = useState('')
  const [runObservation, setRunObservation] = useState<EvaluationRunObservationResult | null>(null)
  const messageEndRef = useRef<HTMLDivElement>(null)
  const sessionGenerationRef = useRef(0)
  const storageKey = `${SESSION_KEY_PREFIX}${version.id}`
  const activeRunKey = snapshot
    ? `qa-eval:active-run:${snapshot.session.session_id}`
    : null
  const completedRunObservation = useMemo<EvaluationRunObservationResult | null>(() => {
    const completed = snapshot?.messages
      .flatMap((item) => item.attachments)
      .find((item) => item.type === 'EVALUATION_RUN_COMPLETED')
    if (!completed?.run_id) return null
    return {
      run_id: String(completed.run_id),
      status: String(completed.status),
      terminal: true,
      completion_message_created: false,
      report_id: completed.report_id ? String(completed.report_id) : null,
      gate_status: completed.gate_status ? String(completed.gate_status) : null,
    }
  }, [snapshot?.messages])

  useEffect(() => {
    let cancelled = false
    const generation = ++sessionGenerationRef.current
    const load = async () => {
      try {
        let available = await listAgentSessions(version.id)
        const storedSessionId = localStorage.getItem(storageKey)
        let selected = available.find((item) => item.session_id === storedSessionId) ?? available[0]
        if (!selected) {
          const created = await createAgentSession(version.id)
          available = [created]
          selected = created
        }
        const next = await getAgentSession(selected.session_id)
        if (!cancelled && generation === sessionGenerationRef.current) {
          localStorage.setItem(storageKey, selected.session_id)
          setSessions(available)
          setRunObservation(null)
          setSnapshot(next)
        }
      } catch (error) {
        if (!cancelled) message.error(error instanceof Error ? error.message : '工作台加载失败')
      } finally { if (!cancelled) setLoading(false) }
    }
    void load()
    return () => { cancelled = true }
  }, [storageKey, version.id])
  useEffect(() => { messageEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [snapshot?.messages.length])
  useEffect(() => {
    if (!snapshot || !activeRunKey) return
    if (completedRunObservation) return
    const attachment = snapshot.messages.flatMap((item) => item.attachments).find((item) => item.type === 'EVALUATION_RUN')
    const runId = String(attachment?.run_id ?? localStorage.getItem(activeRunKey) ?? '')
    if (!runId) return
    localStorage.setItem(activeRunKey, runId)
    let cancelled = false
    const generation = sessionGenerationRef.current
    let timer: number | undefined
    const poll = async () => {
      try {
        const observed = await observeEvaluationRun(snapshot.session.session_id, runId)
        if (cancelled) return
        setRunObservation(observed)
        if (observed.terminal) {
          localStorage.removeItem(activeRunKey)
          const next = await getAgentSession(snapshot.session.session_id)
          if (cancelled || generation !== sessionGenerationRef.current) return
          setSnapshot(next)
          setSessions((current) => current.map((item) => item.session_id === next.session.session_id ? next.session : item))
          notification.success({
            message: '综合质量评测已完成',
            description: `Run ${runId}：${observed.status}${observed.gate_status ? `，Gate ${observed.gate_status}` : ''}`,
            duration: 0,
          })
          return
        }
        timer = window.setTimeout(() => void poll(), 2500)
      } catch (error) {
        if (!cancelled) {
          message.warning(error instanceof Error ? error.message : 'Run 状态轮询失败，将自动重试')
          timer = window.setTimeout(() => void poll(), 4000)
        }
      }
    }
    void poll()
    return () => { cancelled = true; if (timer !== undefined) window.clearTimeout(timer) }
  }, [activeRunKey, completedRunObservation, snapshot])

  const switchSession = async (sessionId: string) => {
    if (sessionId === snapshot?.session.session_id || sending || creating) return
    const generation = ++sessionGenerationRef.current
    setLoading(true)
    setRunObservation(null)
    setSnapshot(null)
    try {
      const next = await getAgentSession(sessionId)
      if (next.session.dataset_version_id !== version.id) throw new Error('会话不属于当前数据集版本')
      if (generation === sessionGenerationRef.current) {
        localStorage.setItem(storageKey, sessionId)
        setSnapshot(next)
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : '切换会话失败')
    } finally {
      if (generation === sessionGenerationRef.current) setLoading(false)
    }
  }

  const createConversation = async () => {
    if (creating || sending) return
    const generation = ++sessionGenerationRef.current
    setCreating(true)
    setRunObservation(null)
    try {
      const created = await createAgentSession(version.id)
      const next = await getAgentSession(created.session_id)
      if (generation === sessionGenerationRef.current) {
        localStorage.setItem(storageKey, created.session_id)
        setSessions((current) => [created, ...current])
        setInput('')
        setSnapshot(next)
        message.success('已创建独立的新对话')
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : '新建对话失败')
    } finally {
      if (generation === sessionGenerationRef.current) setCreating(false)
    }
  }

  const invoke = async (content: string) => {
    if (!snapshot || snapshot.session.status !== 'ACTIVE' || !content.trim() || sending) return
    const sessionId = snapshot.session.session_id
    const generation = sessionGenerationRef.current
    setSending(true)
    try {
      await sendAgentMessage(sessionId, content.trim())
      const next = await getAgentSession(sessionId)
      if (generation === sessionGenerationRef.current) {
        setInput('')
        setSnapshot(next)
        setSessions((current) => current.map((item) => item.session_id === sessionId ? next.session : item))
      }
    } catch (error) { message.error(error instanceof Error ? error.message : 'Agent 执行失败') }
    finally { setSending(false) }
  }
  const refresh = async () => {
    if (!snapshot) return
    const sessionId = snapshot.session.session_id
    const generation = sessionGenerationRef.current
    const next = await getAgentSession(sessionId)
    if (generation === sessionGenerationRef.current) setSnapshot(next)
  }

  const timeline = useMemo(() => snapshot?.tool_invocations.map((tool) => ({
    color: tool.status === 'SUCCEEDED' ? 'green' : tool.status === 'FAILED' ? 'red' : 'blue',
    dot: tool.status === 'RUNNING' ? <ClockCircleOutlined /> : <ToolOutlined />,
    children: <div><Space><Text strong>{tool.tool_name}</Text><Tag color={statusColor[tool.status]}>{tool.status}</Tag></Space>{tool.error?.message && <Paragraph type="danger">{tool.error.message}</Paragraph>}<br/><Text type="secondary">{new Date(tool.started_at).toLocaleString()}</Text></div>,
  })) ?? [], [snapshot])

  return <Spin spinning={loading}>
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <div className="workspace-titlebar">
        <Space><Button icon={<ArrowLeftOutlined />} onClick={onBack}>返回版本</Button><div><Title level={3}>数据集综合质量评测</Title><Text type="secondary">{dataset.name} / {version.version}</Text></div></Space>
        <Space wrap>
          {snapshot && <><Tag color="processing">Dataset Version 已绑定</Tag><Tag color={statusColor[snapshot.session.status]}>{snapshot.session.status}</Tag></>}
          <Button icon={<PlusOutlined />} loading={creating} disabled={sending} onClick={() => void createConversation()}>新建对话</Button>
        </Space>
      </div>
      <Collapse className="session-history" items={[{
        key: 'sessions',
        label: <Space><HistoryOutlined />历史对话（{sessions.length}）</Space>,
        children: <div className="session-history-list">{sessions.map((item) => {
          const active = item.session_id === snapshot?.session.session_id
          return <button className={`session-history-item${active ? ' session-history-item-active' : ''}`} type="button" key={item.session_id} disabled={sending || creating} onClick={() => void switchSession(item.session_id)}>
            <span><Text strong={active}>新对话 · {new Date(item.created_at).toLocaleString()}</Text><Text type="secondary">最近更新：{new Date(item.updated_at).toLocaleString()}</Text></span>
            <Tag color={statusColor[item.status]}>{item.status}</Tag>
          </button>
        })}</div>,
      }]} />
      <Row gutter={[16, 16]} className="agent-workspace">
        <Col xs={24} xl={10} className="agent-workspace-column">
          <Card className="workspace-panel" title={<Space><RobotOutlined />Agent 对话</Space>} extra={snapshot?.session.active_skill && <Tag color="geekblue">Skill: {snapshot.session.active_skill}</Tag>}>
            <div className="conversation-list">
              {!snapshot?.messages.length && <Alert showIcon type="info" message="Agent 已就绪" description="发送推荐请求后，Agent 会真实加载 Skill 并依次调用 Profile、Registry 与推荐 Tool。" />}
              {snapshot?.messages.map((item) => {
                const responseMode = item.attachments.find((attachment) => attachment.type === 'AGENT_RESPONSE_MODE')
                return <div key={item.message_id} className={`conversation-message conversation-message-${item.role.toLowerCase()}`}><div className="message-meta"><span>{item.role === 'USER' ? '你' : 'Agent'} · {new Date(item.created_at).toLocaleTimeString()}</span><AgentResponseBadge attachment={responseMode} /></div><div>{item.content}</div></div>
              })}
              {sending && <div className="conversation-message conversation-message-assistant"><Spin size="small" /> Agent 正在执行 Skill 与 Tools…</div>}
              <div ref={messageEndRef} />
            </div>
            <Space.Compact style={{ width: '100%' }}>
              <Input.TextArea value={input} onChange={(event) => setInput(event.target.value)} onPressEnter={(event) => { if (!event.shiftKey) { event.preventDefault(); void invoke(input) } }} placeholder="输入评测需求，Enter 发送，Shift+Enter 换行" autoSize={{ minRows: 2, maxRows: 4 }} disabled={sending || !snapshot || snapshot.session.status !== 'ACTIVE'} />
              <Button type="primary" icon={<SendOutlined />} onClick={() => void invoke(input)} loading={sending} disabled={!snapshot || snapshot.session.status !== 'ACTIVE'}>发送</Button>
            </Space.Compact>
            {snapshot?.session.status !== 'ACTIVE' && <Alert type="success" showIcon message="此会话的评测流程已结束" description="历史记录保持只读；如需重新评测，请点击“新建对话”。" />}
            <Button type="link" disabled={sending || !snapshot || snapshot.session.status !== 'ACTIVE'} onClick={() => void invoke(QUICK_PROMPT)}>快捷指令：推荐综合质量评测方案</Button>
            <Collapse ghost items={[{ key: 'trace', label: `Skill / Tool 调用轨迹 (${timeline.length})`, children: timeline.length ? <Timeline items={timeline} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 Tool 调用" /> }]} />
          </Card>
        </Col>
        <Col xs={24} xl={14} className="agent-workspace-column"><Card className="workspace-panel proposal-panel"><ProposalPanel key={`${snapshot?.session.session_id ?? 'empty'}:${snapshot?.active_proposal_revision?.proposal_revision_id ?? 'none'}`} runObservation={completedRunObservation ?? runObservation} onRefresh={refresh} snapshot={snapshot ?? { session: {} as AgentSessionSnapshot['session'], messages: [], skill_invocations: [], tool_invocations: [], proposal_revisions: [], active_proposal_revision: null }} /></Card></Col>
      </Row>
    </Space>
  </Spin>
}
