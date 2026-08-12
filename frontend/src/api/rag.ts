import { apiJson, getToken } from './client'
import type {
  ActionResponse,
  RagDocumentListResponse,
  RagSearchResponse,
  UploadSSEEvent,
} from '@/types'

export function fetchDocuments(): Promise<RagDocumentListResponse | null> {
  return apiJson<RagDocumentListResponse>('/rag/documents')
}

export interface UploadStreamHandlers {
  onProgress: (event: Extract<UploadSSEEvent, { type: 'progress' }>) => void
  onDone: (event: Extract<UploadSSEEvent, { type: 'done' }>) => void
  onError: (event: Extract<UploadSSEEvent, { type: 'error' }>) => void
}

export function uploadDocumentStream(
  file: File,
  handlers: UploadStreamHandlers,
): () => void {
  const controller = new AbortController()
  const formData = new FormData()
  formData.append('file', file, file.name)

  const token = getToken()
  const headers: Record<string, string> = {}
  if (token) headers.Authorization = `Bearer ${token}`

  void fetch('/rag/upload', {
    method: 'POST',
    body: formData,
    headers,
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok || !response.body) {
        const payload = await readErrorPayload(response)
        handlers.onError({
          type: 'error',
          message: payload || `Upload failed: HTTP ${response.status}`,
        })
        return
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        buffer = consumeSseBuffer(buffer, handlers)
      }

      buffer += decoder.decode()
      consumeSseBuffer(`${buffer}\n\n`, handlers)
    })
    .catch((error) => {
      if (error instanceof Error && error.name === 'AbortError') return
      handlers.onError({
        type: 'error',
        message: error instanceof Error ? error.message : 'Network request failed',
      })
    })

  return () => controller.abort()
}

export function deleteDocument(
  documentId: number,
): Promise<ActionResponse | null> {
  return apiJson<ActionResponse>(
    `/rag/documents/${documentId}`,
    { method: 'DELETE' },
  )
}

export function searchDocuments(
  query: string,
): Promise<RagSearchResponse | null> {
  return apiJson<RagSearchResponse>(
    `/rag/search?q=${encodeURIComponent(query)}`,
  )
}

async function readErrorPayload(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as {
      message?: string
      detail?: string | Array<{ msg?: string }>
    }
    if (payload.message) return payload.message
    if (typeof payload.detail === 'string') return payload.detail
    if (Array.isArray(payload.detail)) return payload.detail[0]?.msg || ''
  } catch {
    return (await response.text().catch(() => '')).trim()
  }
  return ''
}

function consumeSseBuffer(
  buffer: string,
  handlers: UploadStreamHandlers,
): string {
  const normalized = buffer.replace(/\r\n/g, '\n')
  const blocks = normalized.split('\n\n')
  const rest = blocks.pop() || ''

  for (const block of blocks) {
    const event = parseSseEvent(block)
    if (!event) continue
    if (event.type === 'progress') handlers.onProgress(event)
    if (event.type === 'done') handlers.onDone(event)
    if (event.type === 'error') handlers.onError(event)
  }

  return rest
}

function parseSseEvent(block: string): UploadSSEEvent | null {
  let eventName = ''
  const dataLines: string[] = []

  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      eventName = line.slice(6).trim()
    }
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart())
    }
  }

  if (!eventName || dataLines.length === 0) return null

  try {
    return {
      type: eventName,
      ...JSON.parse(dataLines.join('\n')),
    } as UploadSSEEvent
  } catch {
    return null
  }
}
