// ===== 用户相关类型 =====
export interface UserProfile {
  user_id: number
  username: string
  role: string
  permissions: string[]
  success?: boolean
}

export interface UserSettings {
  api_key: string
  provider: string
  embedding_api_key: string | null
  embedding_base_url: string | null
  embedding_model: string | null
}

export interface CaptchaResponse {
  success: boolean
  captcha_id: string
  image: string
}

export interface LoginResponse {
  success: boolean
  access_token?: string
  message?: string
}

export interface RegisterResponse {
  success: boolean
  message?: string
}

// ===== 聊天相关类型 =====
export interface ChatSession {
  session_id: number
  title: string
  last_message: string
  is_pinned: boolean
}

export interface SessionListResponse {
  sessions: ChatSession[]
}

export interface CreateSessionResponse {
  session_id: number
}

export interface TokenUsage {
  model?: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  is_inherited?: boolean
}

export interface ChatMessage {
  message_id: number
  role: 'user' | 'assistant'
  content: string
  model?: string | null
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  is_inherited?: boolean
}

export interface MessageListResponse {
  success: boolean
  messages: ChatMessage[]
  message?: string
}

export interface ActionResponse {
  success: boolean
  message?: string
}

// SSE 事件类型
export interface SSEDeltaEvent {
  type: 'delta'
  content: string
}

export interface SSEUsageEvent {
  type: 'usage'
  usage: TokenUsage
}

export interface SSEErrorEvent {
  type: 'error'
  message?: string
  content?: string
}

export type SSEEvent = SSEDeltaEvent | SSEUsageEvent | SSEErrorEvent

// ===== 管理员相关类型 =====
export interface AdminDashboard {
  success: boolean
  summary: {
    users: number
    roles: number
    permissions: number
    admin_users: number
  }
}

export interface AdminUser {
  user_id: number
  username: string
  role: string
  permissions: string[]
}

export interface RolePermission {
  code: string
  name: string
}

export interface AdminRole {
  role_id: number
  name: string
  description: string | null
  permissions: RolePermission[]
}

export interface AdminPermission {
  permission_id: number
  code: string
  name: string
  description: string | null
}

export interface RoleResponse {
  role_id: number
  name: string
  description: string | null
  permissions: RolePermission[]
}

export interface BranchResponse {
  success: boolean
  session_id: number
  message?: string
}

// ===== RAG 知识库相关类型 =====
export interface RagDocument {
  document_id: number
  filename: string
  mime_type: string | null
  file_size: number
  status: 'pending' | 'ready' | 'failed' | 'processing'
  chunk_count: number
  embedding_model: string | null
  error_message: string | null
  created_at: string | null
  updated_at: string | null
}

export interface RagDocumentListResponse {
  success: boolean
  documents: RagDocument[]
}

export interface RagUploadResponse {
  success: boolean
  document: RagDocument
  message?: string
}

export interface RagSearchHit {
  document_id: number
  chunk_id: number
  filename: string
  content: string
  score: number
}

export interface RagSearchResponse {
  success: boolean
  hits: RagSearchHit[]
}

// ===== RAG 上传 SSE 事件 =====
export interface UploadProgressEvent {
  type: 'progress'
  stage: string
  message: string
  document?: RagDocument
}

export interface UploadDoneEvent {
  type: 'done'
  document: RagDocument
}

export interface UploadErrorEvent {
  type: 'error'
  message: string
}

export type UploadSSEEvent = UploadProgressEvent | UploadDoneEvent | UploadErrorEvent
