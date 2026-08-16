import { apiCall, apiJson } from './client'
import type {
  ActionResponse,
  AgentRun,
  BranchResponse,
  CreateSessionResponse,
  MessageListResponse,
  SessionListResponse,
} from '@/types'

export function fetchSessions(): Promise<SessionListResponse | null> {
  return apiJson<SessionListResponse>('/chat/sessions')
}

export function createSession(title = '新会话'): Promise<CreateSessionResponse | null> {
  return apiJson<CreateSessionResponse>('/chat/session', {
    method: 'POST',
    body: JSON.stringify({ title }),
  })
}

export function deleteSession(sessionId: number): Promise<ActionResponse | null> {
  return apiJson<ActionResponse>(`/chat/session/${sessionId}`, { method: 'DELETE' })
}

export function updateSession(
  sessionId: number,
  data: { title?: string; is_pinned?: boolean },
): Promise<ActionResponse | null> {
  return apiJson<ActionResponse>(`/chat/sessions/${sessionId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  })
}

export function fetchMessages(sessionId: number): Promise<MessageListResponse | null> {
  return apiJson<MessageListResponse>(
    `/chat/messages?session_id=${encodeURIComponent(sessionId)}`,
  )
}

export function deleteMessage(messageId: number): Promise<ActionResponse | null> {
  return apiJson<ActionResponse>(`/chat/messages/${messageId}`, { method: 'DELETE' })
}

export function createMessageBranch(messageId: number): Promise<BranchResponse | null> {
  return apiJson<BranchResponse>(`/chat/messages/${messageId}/branch`, {
    method: 'POST',
  })
}

export function createSessionBranch(sessionId: number): Promise<BranchResponse | null> {
  return apiJson<BranchResponse>(`/chat/sessions/${sessionId}/branch`, {
    method: 'POST',
  })
}

export function stopGeneration(sessionId: number): Promise<ActionResponse | null> {
  return apiJson<ActionResponse>(`/chat/stream/${sessionId}/stop`, { method: 'POST' })
}

/**
 * 发送流式聊天消息，返回原始 Response 供调用方读取流。
 */
export function sendStreamMessage(
  sessionId: number,
  message: string,
  signal?: AbortSignal,
): Promise<Response | null> {
  return apiCall(
    '/chat/stream',
    {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, message }),
      signal,
    },
  )
}

/**
 * 发送流式 agent 消息，返回原始 Response 供调用方读取流。
 * 事件协议与普通聊天一致，额外包含 agent_plan / agent_step / agent_tool 事件。
 */
export function sendAgentMessage(
  sessionId: number,
  message: string,
  signal?: AbortSignal,
): Promise<Response | null> {
  return apiCall(
    '/agent/stream',
    {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, message }),
      signal,
    },
  )
}

/**
 * 查看一次 agent 运行的完整 trace（排错用）。
 */
export function fetchAgentRun(runId: number): Promise<AgentRun | null> {
  return apiJson<AgentRun>(`/agent/runs/${runId}`)
}

/**
 * 修改用户消息并重新生成流式回复，返回原始 Response。
 */
export function modifyStreamMessage(
  messageId: number,
  content: string,
  signal?: AbortSignal,
): Promise<Response | null> {
  return apiCall(
    `/chat/messages/${messageId}/stream`,
    {
      method: 'PUT',
      body: JSON.stringify({ content }),
      signal,
    },
  )
}
