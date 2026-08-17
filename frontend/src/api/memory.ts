/**
 * 记忆模块 API（后端 /memories 接口）
 * 基于 axios 封装的 request，返回统一的 ApiResult 结构。
 */
import { request, type ApiResult } from './http'
import type {
  ActionResponse,
  MemoryItem,
  MemoryListResponse,
  MemoryMutationResponse,
} from '@/types'

/** 后端 MemoryResponse 使用 memory_id 字段，这里归一化为前端契约的 id */
interface RawMemoryItem {
  memory_id?: number
  id?: number
  content: string
  created_at: string | null
  updated_at: string | null
}

function normalizeMemory(raw: RawMemoryItem): MemoryItem {
  return {
    id: raw.memory_id ?? raw.id ?? 0,
    content: raw.content,
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
export async function createMemory(content: string): Promise<ApiResult<MemoryMutationResponse>> {
  const result = await request<{ success: boolean; memory: RawMemoryItem }>({
    url: '/memories',
    method: 'POST',
    data: { content },
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
