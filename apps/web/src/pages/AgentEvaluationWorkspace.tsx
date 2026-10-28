import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  RobotOutlined,
  SendOutlined,
  ToolOutlined,
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Empty,
  Input,
  Row,
  Segmented,
  Space,
  Spin,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'

import {
  createAgentSession,
  getAgentSession,
  recommendationCoverage,
  sendAgentMessage,
  type AgentSessionSnapshot,
  type GuideCoverageItem,
  type ProposalItem,
} from '../api/agentSessions'
import type { Dataset, DatasetVersion } from '../api/datasetVersions'

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
  FAILED: 'error',
}

function AgentResponseBadge({ attachment }: { attachment?: Record<string, unknown> }) {
  if (!attachment) return null
  if (attachment.mode === 'MODEL') {
    return <Tag className="agent-mode-badge" color="purple" title="本条回复由后端配置的真实模型生成">AI 模型 · {String(attachment.model ?? 'unknown')}</Tag>
  }
  return <Tag className="agent-mode-badge" color="default" title={`模型未生成本条回复：${String(attachment.reason ?? 'unknown')}`}>确定性降级 · {String(attachment.reason ?? 'unknown')}</Tag>
}

function ProposalPanel({ snapshot }: { snapshot: AgentSessionSnapshot }) {
  const revision = snapshot.active_proposal_revision
  const coverage = recommendationCoverage(snapshot)
  const [view, setView] = useState<'proposal' | 'coverage'>('proposal')

  const proposalColumns = [
    { title: '指标', key: 'metric', render: (_: unknown, item: ProposalItem) => <Space direction="vertical" size={0}><Text strong>{item.metric.key}</Text><Text type="secondary">{item.metric.measurement}</Text></Space> },
    { title: 'Calculator', key: 'calculator', render: (_: unknown, item: ProposalItem) => <><Text>{item.selected_calculator.key}</Text><br/><Text type="secondary">v{item.selected_calculator.version}</Text></> },
    { title: '规则', key: 'rule', render: (_: unknown, item: ProposalItem) => <Text code>{item.rule.operator} {String(item.rule.threshold)}</Text> },
    { title: '参数', key: 'parameters', render: (_: unknown, item: ProposalItem) => <Text code>{JSON.stringify(item.parameters)}</Text> },
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
      <Table rowKey="key" size="small" pagination={false} dataSource={revision.proposal.items} columns={proposalColumns} scroll={{ x: 720 }} expandable={{ expandedRowRender: (item) => <Space direction="vertical"><Paragraph>{item.rationale}</Paragraph>{item.limitations.map((value) => <Alert key={value} type="warning" showIcon message={value} />)}</Space> }} />
      <Alert type="info" showIcon message="当前仅展示 Agent 确定性推荐结果" description="发布 Contract 与提交 Run 属于后续人工确认任务，本工作台不会自动执行写操作。" />
    </> : <Table rowKey={(item) => `${item.metric_key}:${item.metric_version}`} size="small" pagination={false} dataSource={coverage} columns={coverageColumns} scroll={{ x: 650 }} />}
  </Space>
}

export default function AgentEvaluationWorkspace({ dataset, version, onBack }: Props) {
  const [snapshot, setSnapshot] = useState<AgentSessionSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [input, setInput] = useState('')
  const messageEndRef = useRef<HTMLDivElement>(null)
  const storageKey = `${SESSION_KEY_PREFIX}${version.id}`

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        let sessionId = localStorage.getItem(storageKey)
        let next: AgentSessionSnapshot
        if (sessionId) {
          try { next = await getAgentSession(sessionId) }
          catch { localStorage.removeItem(storageKey); sessionId = null; const created = await createAgentSession(version.id); localStorage.setItem(storageKey, created.session_id); next = await getAgentSession(created.session_id) }
        } else {
          const created = await createAgentSession(version.id)
          localStorage.setItem(storageKey, created.session_id)
          next = await getAgentSession(created.session_id)
        }
        if (!cancelled) setSnapshot(next)
      } catch (error) {
        if (!cancelled) message.error(error instanceof Error ? error.message : '工作台加载失败')
      } finally { if (!cancelled) setLoading(false) }
    }
    void load()
    return () => { cancelled = true }
  }, [storageKey, version.id])
  useEffect(() => { messageEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [snapshot?.messages.length])

  const invoke = async (content: string) => {
    if (!snapshot || !content.trim() || sending) return
    setSending(true)
    try {
      await sendAgentMessage(snapshot.session.session_id, content.trim())
      setInput('')
      setSnapshot(await getAgentSession(snapshot.session.session_id))
    } catch (error) { message.error(error instanceof Error ? error.message : 'Agent 执行失败') }
    finally { setSending(false) }
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
        {snapshot && <Space><Tag color="processing">Dataset Version 已绑定</Tag><Tag color={statusColor[snapshot.session.status]}>{snapshot.session.status}</Tag></Space>}
      </div>
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
              <Input.TextArea value={input} onChange={(event) => setInput(event.target.value)} onPressEnter={(event) => { if (!event.shiftKey) { event.preventDefault(); void invoke(input) } }} placeholder="输入评测需求，Enter 发送，Shift+Enter 换行" autoSize={{ minRows: 2, maxRows: 4 }} disabled={sending || !snapshot} />
              <Button type="primary" icon={<SendOutlined />} onClick={() => void invoke(input)} loading={sending}>发送</Button>
            </Space.Compact>
            <Button type="link" disabled={sending || !snapshot} onClick={() => void invoke(QUICK_PROMPT)}>快捷指令：推荐综合质量评测方案</Button>
            <Collapse ghost items={[{ key: 'trace', label: `Skill / Tool 调用轨迹 (${timeline.length})`, children: timeline.length ? <Timeline items={timeline} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 Tool 调用" /> }]} />
          </Card>
        </Col>
        <Col xs={24} xl={14} className="agent-workspace-column"><Card className="workspace-panel proposal-panel"><ProposalPanel snapshot={snapshot ?? { session: {} as AgentSessionSnapshot['session'], messages: [], skill_invocations: [], tool_invocations: [], proposal_revisions: [], active_proposal_revision: null }} /></Card></Col>
      </Row>
    </Space>
  </Spin>
}
