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
