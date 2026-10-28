export type InvocationStatus = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'

export interface AgentSession {
  session_id: string
  dataset_version_id: string
  status: 'ACTIVE' | 'COMPLETED' | 'FAILED'
  active_skill: string | null
  created_at: string
  updated_at: string
}

export interface ConversationMessage {
  message_id: string
  session_id: string
  role: 'USER' | 'ASSISTANT' | 'SYSTEM' | 'TOOL'
  content: string
  attachments: Array<Record<string, unknown>>
  created_at: string
}

export interface SkillInvocation {
  invocation_id: string
  skill_key: string
  skill_version: string
  status: 'RUNNING' | 'SUCCEEDED' | 'FAILED'
  reason: string | null
  started_at: string
  finished_at: string | null
}

export interface ToolInvocation {
  invocation_id: string
  tool_name: string
  arguments_digest: string
  status: InvocationStatus
  result_refs: Array<Record<string, unknown>>
  error: { message?: string; code?: string } | null
  started_at: string
  finished_at: string | null
}

export interface ProposalItem {
  key: string
  metric: { key: string; version: string; measurement: string }
  candidates: Array<{ key: string; version: string }>
  selected_calculator: { key: string; version: string }
  parameters: Record<string, unknown>
  rule: { operator: string; threshold: unknown; severity: string; on_missing: string }
  rationale: string | null
  limitations: string[]
}

export interface ProposalRevision {
  proposal_revision_id: string
  revision: number
  proposal: {
    proposal_id: string
    dataset_version_id: string
    profile_fingerprint: string | null
    items: ProposalItem[]
    created_at: string
  }
  validation: { valid: boolean; findings?: Array<{ code: string; message: string }> } | null
  profile_fingerprint: string
  created_at: string
}

export interface GuideCoverageItem {
  metric_key: string
  metric_version: string
  metric_name: string
  status: 'APPLICABLE' | 'NOT_APPLICABLE' | 'NEEDS_INPUT' | 'UNAVAILABLE'
  reason: string
  selected_calculator: { key: string; version: string } | null
}

export interface AgentSessionSnapshot {
  session: AgentSession
  messages: ConversationMessage[]
  skill_invocations: SkillInvocation[]
  tool_invocations: ToolInvocation[]
  proposal_revisions: ProposalRevision[]
  active_proposal_revision: ProposalRevision | null
}

async function apiError(response: Response, fallback: string) {
  try {
    const body = await response.json() as { detail?: string }
    return body.detail ? `${fallback}：${body.detail}` : fallback
  } catch {
    return `${fallback}：HTTP ${response.status}`
  }
}

export async function createAgentSession(datasetVersionId: string): Promise<AgentSession> {
  const response = await fetch('/api/v1/agent-sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dataset_version_id: datasetVersionId }),
  })
  if (!response.ok) throw new Error(await apiError(response, '创建 Agent 会话失败'))
  return response.json() as Promise<AgentSession>
}

export async function getAgentSession(sessionId: string): Promise<AgentSessionSnapshot> {
  const response = await fetch(`/api/v1/agent-sessions/${sessionId}`)
  if (!response.ok) throw new Error(await apiError(response, '恢复 Agent 会话失败'))
  return response.json() as Promise<AgentSessionSnapshot>
}

export async function sendAgentMessage(sessionId: string, content: string): Promise<void> {
  const response = await fetch(`/api/v1/agent-sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, client_request_id: crypto.randomUUID() }),
  })
  if (!response.ok) throw new Error(await apiError(response, '发送消息失败'))
}

export function recommendationCoverage(snapshot: AgentSessionSnapshot): GuideCoverageItem[] {
  const recommendation = [...snapshot.tool_invocations]
    .reverse()
    .find((tool) => tool.tool_name === 'recommend_evaluation_proposal' && tool.status === 'SUCCEEDED')
  const inline = recommendation?.result_refs.find((ref) => ref.type === 'INLINE_TOOL_RESULT')
  const result = inline?.result as { guide_coverage?: GuideCoverageItem[] } | undefined
  return result?.guide_coverage ?? []
}
