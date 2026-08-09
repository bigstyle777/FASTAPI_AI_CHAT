import { apiCall, apiJson } from './client'
import type {
  ActionResponse,
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
