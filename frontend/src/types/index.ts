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

// ===== Agent 相关类型 =====
export interface AgentPlanStep {
  description: string
  tool?: string | null
  args?: Record<string, unknown>
  expected_output?: string | null
}

export interface AgentPlanEvent {
  type: 'agent_plan'
  run_id: number
  steps: AgentPlanStep[]
}

export interface AgentStepEvent {
  type: 'agent_step'
  run_id: number
  index: number
  step: AgentPlanStep
  status: 'started' | 'completed' | 'failed' | 'skipped'
  output?: string | null
  error?: string | null
}

export interface AgentToolEvent {
  type: 'agent_tool'
  run_id: number
  step_index: number
  tool_call_id?: string | null
  tool: string
  arguments?: Record<string, unknown> | null
  status: 'started' | 'completed' | 'failed'
  result?: unknown
  error?: string | null
  duration_ms?: number | null
}

export interface AgentDoneEvent {
  type: 'done'
  run_id: number
  status: 'completed' | 'stopped' | 'failed'
}

export type AgentSSEEvent =
  | AgentPlanEvent
  | AgentStepEvent
  | AgentToolEvent
  | AgentDoneEvent

export interface AgentRun {
  run_id: number
  session_id: number
  user_id: number
  status: string
  user_input: string
  plan: AgentPlanStep[] | null
  final_answer: string | null
  model: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  error_message: string | null
  created_at: string | null
  updated_at: string | null
  trace_count: number
  traces?: AgentTracePoint[]
}

export interface AgentTracePoint {
  id: number
  sequence: number
  stage: string
  name: string
  status: string
  step_index: number | null
  tool_call_id: string | null
  tool_name: string | null
  input_data: Record<string, unknown> | null
  output_data: Record<string, unknown> | null
  error_message: string | null
  duration_ms: number | null
  created_at: string | null
}

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

// ===== 用户记忆相关类型 =====
/** 单条记忆（前端统一使用 id，api 层负责将后端 memory_id 归一化） */
export interface MemoryItem {
  id: number
  content: string
  created_at: string | null
  updated_at: string | null
}

export interface MemoryListResponse {
  success: boolean
  memories: MemoryItem[]
}

export interface MemoryMutationResponse {
  success: boolean
  memory: MemoryItem
}

export interface MemoryCreateRequest {
  content: string
}

export interface MemoryUpdateRequest {
  content: string
}
