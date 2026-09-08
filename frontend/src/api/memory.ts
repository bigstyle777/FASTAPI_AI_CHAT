/**
 * 记忆模块 API（后端 /memories 接口）
 * 基于 axios 封装的 request，返回统一的 ApiResult 结构。
 */
import { request, type ApiResult } from './http'
import type {
  ActionResponse,
  MemoryCreateRequest,
  MemoryItem,
  MemoryListResponse,
  MemoryMutationResponse,
} from '@/types'

/** 后端 MemoryRead 的原始字段。id 来自 Pydantic 字段名（已经是 id，不再是 memory_id）。 */
interface RawMemoryItem {
  id: number
  type: 'semantic' | 'episodic'
  key: string | null
  value: string | null
  summary: string | null
  event_type: string | null
  importance: number
  created_at: string | null
  updated_at: string | null
}

function normalizeMemory(raw: RawMemoryItem): MemoryItem {
  const content = raw.type === 'semantic' ? (raw.value ?? '') : (raw.summary ?? '')
  return {
    id: raw.id,
    type: raw.type,
    key: raw.key,
    value: raw.value,
    summary: raw.summary,
    event_type: raw.event_type,
    importance: raw.importance ?? 0.5,
    content,
    created_at: raw.created_at,
    updated_at: raw.updated_at,
  }
}

/** 获取当前用户的记忆列表 */
export async function fetchMemories(): Promise<ApiResult<MemoryListResponse>> {
  const result = await request<{ success: boolean; memories: RawMemoryItem[] }>({
    url: '/memories',
    method: 'GET',
  })
  if (!result.ok) return result
  return {
    ok: true,
    data: {
      success: result.data.success,
      memories: (result.data.memories || []).map(normalizeMemory),
    },
  }
}

/** 创建一条记忆 */
export async function createMemory(
  payload: MemoryCreateRequest,
): Promise<ApiResult<MemoryMutationResponse>> {
  const result = await request<{ success: boolean; memory: RawMemoryItem }>({
    url: '/memories',
    method: 'POST',
    data: payload,
  })
  if (!result.ok) return result
  return { ok: true, data: { success: result.data.success, memory: normalizeMemory(result.data.memory) } }
}

/** 更新记忆内容 */
export async function updateMemory(
  memoryId: number,
  content: string,
): Promise<ApiResult<MemoryMutationResponse>> {
  const result = await request<{ success: boolean; memory: RawMemoryItem }>({
    url: `/memories/${memoryId}`,
    method: 'PUT',
    data: { content },
  })
  if (!result.ok) return result
  return { ok: true, data: { success: result.data.success, memory: normalizeMemory(result.data.memory) } }
}

/** 删除一条记忆 */
export async function deleteMemory(memoryId: number): Promise<ApiResult<ActionResponse>> {
  return request<ActionResponse>({ url: `/memories/${memoryId}`, method: 'DELETE' })
}

/**
 * 触发一次重复事件合并（把近期内反复发生的同类事件归纳成一条高层总结）。
 * 后端只是入队，实际归纳在 Celery worker 上跑，需要稍后刷新才能看到结果。
 */
export async function consolidateMemories(): Promise<ApiResult<ActionResponse>> {
  return request<ActionResponse>({ url: '/memories/consolidate', method: 'POST' })
}

/**
 * 触发一次生命周期清扫（过期归档 / 长期未用降级 / 观察窗到期归档）。
 * 同样是异步入队；归档只是不再参与检索，不会物理删除记忆。
 */
export async function decayMemories(): Promise<ApiResult<ActionResponse>> {
  return request<ActionResponse>({ url: '/memories/decay', method: 'POST' })
}
