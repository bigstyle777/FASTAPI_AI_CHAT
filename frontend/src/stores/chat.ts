import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { ChatMessage, ChatSession, SSEEvent, TokenUsage } from '@/types'
import {
  createSession as apiCreateSession,
  createMessageBranch,
  createSessionBranch,
  deleteMessage as apiDeleteMessage,
  deleteSession as apiDeleteSession,
  fetchMessages,
  fetchSessions,
  modifyStreamMessage,
  sendStreamMessage,
  stopGeneration,
  updateSession as apiUpdateSession,
} from '@/api/chat'

export const useChatStore = defineStore('chat', () => {
  const sessions = ref<ChatSession[]>([])
  const currentSessionId = ref<number | null>(null)
  const messages = ref<ChatMessage[]>([])
  const currentSessionHasMessages = ref(false)
  const isSending = ref(false)
  const isStopping = ref(false)
  const streamingContent = ref('')
  const streamingUsage = ref<TokenUsage | null>(null)
  const notice = ref<{ message: string; type: 'info' | 'success' | 'error' }>({ message: '', type: 'info' })
  const editingMessageId = ref<number | null>(null)
  let abortController: AbortController | null = null

  const currentSession = computed(() =>
    sessions.value.find((s) => Number(s.session_id) === Number(currentSessionId.value)) || null,
  )

  function showNotice(message: string, type: 'info' | 'success' | 'error' = 'info') {
    notice.value = { message, type }
  }

  function clearNotice() {
    notice.value = { message: '', type: 'info' }
  }

  /** 只刷新会话列表（更新侧边栏），不重载消息，避免消息列表销毁重建导致卡顿 */
  async function refreshSessionsOnly() {
    const data = await fetchSessions()
    if (!data || !data.sessions) return
    sessions.value = data.sessions
  }

  async function loadSessions() {
    console.log('[chatStore] loadSessions 开始...')
    const data = await fetchSessions()
    console.log('[chatStore] fetchSessions 返回:', data ? { success: (data as unknown as Record<string, unknown>).success, count: data.sessions?.length } : null)

    if (!data) {
      console.warn('[chatStore] fetchSessions 返回 null，可能是 token 已过期或网络错误')
      return
    }

    if (data.sessions && data.sessions.length > 0) {
      sessions.value = data.sessions
      const hasCurrent = data.sessions.some(
        (s) => Number(s.session_id) === Number(currentSessionId.value),
      )
      if (!currentSessionId.value || !hasCurrent) {
        const firstId = data.sessions[0]?.session_id
        currentSessionId.value = firstId ?? null
        console.log('[chatStore] 设置 currentSessionId =', currentSessionId.value)
      }
      if (currentSessionId.value) {
        await loadSessionMessages(currentSessionId.value)
      }
    } else {
      console.log('[chatStore] 没有会话')
      sessions.value = []
      currentSessionId.value = null
      currentSessionHasMessages.value = false
      messages.value = []
    }
  }

  async function deleteEmptyCurrentSession(nextSessionId: number | null = null) {
    if (!currentSessionId.value || currentSessionHasMessages.value) return false
    if (nextSessionId && Number(currentSessionId.value) === Number(nextSessionId)) return false

    const sessionId = currentSessionId.value
    await apiDeleteSession(sessionId)
    sessions.value = sessions.value.filter((s) => Number(s.session_id) !== Number(sessionId))

    if (Number(currentSessionId.value) === Number(sessionId)) {
      currentSessionId.value = null
      currentSessionHasMessages.value = false
    }
    return true
  }

  async function createNewSession() {
    if (isSending.value) {
      showNotice('消息发送中，请稍后再新建会话', 'error')
      return
    }

    await deleteEmptyCurrentSession()

    const data = await apiCreateSession('新会话')
    if (data?.session_id) {
      currentSessionId.value = data.session_id
      currentSessionHasMessages.value = false
      messages.value = []
      clearNotice()
      // 在列表头部插入临时条目
      sessions.value.unshift({
        session_id: data.session_id,
        title: '新会话',
        last_message: '暂无消息',
        is_pinned: false,
      })
    }
  }

  async function loadSessionMessages(sessionId: number) {
    if (isSending.value && Number(currentSessionId.value) !== Number(sessionId)) {
      showNotice('消息发送中，请稍后再切换会话', 'error')
      return
    }

    await deleteEmptyCurrentSession(sessionId)
    currentSessionId.value = Number(sessionId)
    clearNotice()

    const data = await fetchMessages(sessionId)
    if (!data) return

    if (!data.success) {
      showNotice(data.message || '加载消息失败', 'error')
      currentSessionId.value = null
      currentSessionHasMessages.value = false
      await loadSessions()
      return
    }

    if (data.messages && data.messages.length > 0) {
      currentSessionHasMessages.value = true
      messages.value = data.messages
    } else {
      currentSessionHasMessages.value = false
      messages.value = []
    }
  }

  async function deleteSessionById(sessionId: number) {
    const data = await apiDeleteSession(sessionId)
    if (!data?.success) {
      showNotice(data?.message || '删除会话失败', 'error')
      return
    }

    if (Number(currentSessionId.value) === Number(sessionId)) {
      currentSessionId.value = null
      currentSessionHasMessages.value = false
      messages.value = []
    }
    await loadSessions()
    showNotice(data.message || '会话已删除', 'success')
  }

  async function renameSession(sessionId: number, title: string) {
    const data = await apiUpdateSession(sessionId, { title })
    if (!data?.success) {
      showNotice(data?.message || '修改会话名称失败', 'error')
      return false
    }
    const session = sessions.value.find((s) => Number(s.session_id) === Number(sessionId))
    if (session) {
      session.title = title
    }
    showNotice('会话名称已更新', 'success')
    return true
  }

  async function togglePin(sessionId: number, isPinned: boolean) {
    const data = await apiUpdateSession(sessionId, { is_pinned: isPinned })
    if (!data?.success) {
      showNotice(data?.message || '置顶失败', 'error')
      return
    }
    await refreshSessionsOnly()
    showNotice(isPinned ? '已置顶' : '已取消置顶', 'success')
  }

  async function deleteMessageById(messageId: number) {
    const data = await apiDeleteMessage(messageId)
    if (!data?.success) {
      showNotice(data?.message || '删除消息失败', 'error')
      return
    }
    showNotice(data.message || '消息已删除', 'success')
    if (currentSessionId.value) {
      await loadSessionMessages(currentSessionId.value)
    }
  }

  async function branchFromMessage(messageId: number) {
    if (isSending.value) {
      showNotice('消息发送中，请稍后再创建分支', 'error')
      return
    }
    const data = await createMessageBranch(messageId)
    if (!data?.success) {
      showNotice(data?.message || '建立分支失败', 'error')
      return
    }
    currentSessionId.value = data.session_id
    currentSessionHasMessages.value = true
    await loadSessions()
    showNotice('已在新对话中建立分支', 'success')
  }

  async function branchFromSession() {
    if (isSending.value) {
      showNotice('消息发送中，请稍后再创建分支', 'error')
      return
    }
    if (!currentSessionId.value) {
      showNotice('当前没有可分支的会话', 'error')
      return
    }
    const data = await createSessionBranch(currentSessionId.value)
    if (!data?.success) {
      showNotice(data?.message || '建立分支对话失败', 'error')
      return
    }
    currentSessionId.value = data.session_id
    currentSessionHasMessages.value = true
    await loadSessions()
    showNotice('已在新分支中新建对话', 'success')
  }

  function parseSseBuffer(buffer: string, onEvent: (eventName: string, payload: SSEEvent) => void): string {
    const normalized = buffer.replace(/\r\n/g, '\n')
    const blocks = normalized.split('\n\n')
    const rest = blocks.pop() || ''

    blocks.forEach((block) => {
      let eventName = 'message'
      const dataLines: string[] = []

      block.split('\n').forEach((line) => {
        if (line.startsWith('event:')) {
          eventName = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart())
        }
      })

      if (!dataLines.length) return

      const rawData = dataLines.join('\n')
      try {
        onEvent(eventName, JSON.parse(rawData) as SSEEvent)
      } catch {
        onEvent(eventName, { type: 'delta', content: rawData } as SSEEvent)
      }
    })

    return rest
  }

  async function consumeStream(
    response: Response,
    onDelta: (fullReply: string) => void,
    onUsage: (usage: TokenUsage) => void,
  ): Promise<string> {
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let fullReply = ''
    let buffer = ''
    let hasError = false

    const handleEvent = (_eventName: string, payload: SSEEvent) => {
      const type = payload.type
      if (type === 'delta') {
        fullReply += payload.content || ''
        onDelta(fullReply)
      } else if (type === 'usage') {
        onUsage(payload.usage)
      } else if (type === 'error') {
        hasError = true
        fullReply = payload.message || payload.content || '请求失败，请稍后再试'
        onDelta(fullReply)
      }
    }

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        buffer = parseSseBuffer(buffer, handleEvent)
      }
      const trailing = decoder.decode()
      if (trailing) buffer += trailing
      if (buffer.trim()) {
        parseSseBuffer(`${buffer}\n\n`, handleEvent)
      }
    } catch (error) {
      if (error instanceof Error && error.name !== 'AbortError') {
        throw error
      }
    }

    return hasError ? '' : fullReply
  }

  async function sendMessage(text: string) {
    if (isSending.value) return
    if (!text.trim()) {
      showNotice('请输入消息内容', 'error')
      return
    }
    if (!currentSessionId.value) {
      showNotice('请先创建或选择一个会话', 'error')
      return
    }

    // 如果是修改消息模式
    if (editingMessageId.value) {
      const messageId = editingMessageId.value
      editingMessageId.value = null
      await modifyMessage(messageId, text)
      return
    }

    isSending.value = true
    clearNotice()

    // 添加用户消息
    messages.value.push({
      message_id: Date.now(),
      role: 'user',
      content: text,
    })

    // 添加 AI 占位消息
    const aiMessage: ChatMessage = {
      message_id: Date.now() + 1,
      role: 'assistant',
      content: '',
    }
    messages.value.push(aiMessage)

    streamingContent.value = '正在思考...'
    streamingUsage.value = null
    abortController = new AbortController()

    try {
      const response = await sendStreamMessage(
        currentSessionId.value,
        text,
        abortController.signal,
      )

      if (!response || !response.body) {
        aiMessage.content = '请求失败，请稍后再试'
        streamingContent.value = ''
        return
      }

      streamingContent.value = ''
      const fullReply = await consumeStream(
        response,
        (reply) => {
          streamingContent.value = reply
          aiMessage.content = reply
        },
        (usage) => {
          streamingUsage.value = usage
          aiMessage.model = usage.model
          aiMessage.prompt_tokens = usage.prompt_tokens
          aiMessage.completion_tokens = usage.completion_tokens
          aiMessage.total_tokens = usage.total_tokens
          aiMessage.is_inherited = usage.is_inherited
        },
      )

      if (fullReply) {
        aiMessage.content = fullReply
      }
      currentSessionHasMessages.value = true
      // 只刷新会话列表（更新侧边栏），不重新加载消息避免重建 DOM
      await refreshSessionsOnly()
    } catch (error) {
      if (error instanceof Error && error.name !== 'AbortError') {
        console.error('发送消息失败:', error)
        aiMessage.content = '发送消息失败，请稍后再试'
        showNotice('发送消息失败，请稍后再试', 'error')
      }
    } finally {
      abortController = null
      isStopping.value = false
      isSending.value = false
      streamingContent.value = ''
    }
  }

  async function modifyMessage(messageId: number, newContent: string) {
    isSending.value = true
    clearNotice()

    // 找到被修改的用户消息，更新内容，删除其后的所有消息
    const msgIndex = messages.value.findIndex((m) => m.message_id === messageId)
    if (msgIndex >= 0) {
      const targetMsg = messages.value[msgIndex]
      if (targetMsg) {
        targetMsg.content = newContent
      }
      messages.value = messages.value.slice(0, msgIndex + 1)
    }

    // 添加 AI 占位消息
    const aiMessage: ChatMessage = {
      message_id: Date.now(),
      role: 'assistant',
      content: '',
    }
    messages.value.push(aiMessage)

    streamingContent.value = '正在修改并重新生成回复...'
    streamingUsage.value = null
    abortController = new AbortController()

    try {
      const response = await modifyStreamMessage(
        messageId,
        newContent,
        abortController.signal,
      )

      if (!response || !response.ok || !response.body) {
        aiMessage.content = '修改消息失败'
        showNotice('修改消息失败', 'error')
        return
      }

      streamingContent.value = ''
      const fullReply = await consumeStream(
        response,
        (reply) => {
          streamingContent.value = reply
          aiMessage.content = reply
        },
        (usage) => {
          streamingUsage.value = usage
          aiMessage.model = usage.model
          aiMessage.prompt_tokens = usage.prompt_tokens
          aiMessage.completion_tokens = usage.completion_tokens
          aiMessage.total_tokens = usage.total_tokens
        },
      )

      if (fullReply) {
        aiMessage.content = fullReply
      }
      currentSessionHasMessages.value = true
      showNotice('修改成功', 'success')
      await refreshSessionsOnly()
    } catch (error) {
      if (error instanceof Error && error.name !== 'AbortError') {
        console.error('修改消息失败:', error)
        aiMessage.content = '修改消息失败'
        showNotice('修改消息失败', 'error')
      }
    } finally {
      abortController = null
      isStopping.value = false
      isSending.value = false
      streamingContent.value = ''
    }
  }

  async function stopStreaming() {
    if (!isSending.value || !abortController || isStopping.value) return
    const sessionId = currentSessionId.value
    if (!sessionId) return

    isStopping.value = true
    try {
      await stopGeneration(sessionId)
      showNotice('已停止生成，正在收尾...', 'info')
    } catch (error) {
      if (error instanceof Error && error.name !== 'AbortError') {
        console.error('停止生成失败:', error)
      }
      if (abortController) {
        abortController.abort()
      }
    }
  }

  function startEditingMessage(messageId: number, content: string) {
    editingMessageId.value = messageId
    showNotice('已进入修改该消息状态，发送后将更新原消息，并重新生成回复', 'info')
    return content
  }

  function cancelEditing() {
    editingMessageId.value = null
    clearNotice()
  }

  return {
    sessions,
    currentSessionId,
    currentSession,
    messages,
    currentSessionHasMessages,
    isSending,
    isStopping,
    streamingContent,
    streamingUsage,
    notice,
    editingMessageId,
    showNotice,
    clearNotice,
    loadSessions,
    refreshSessionsOnly,
    createNewSession,
    loadSessionMessages,
    deleteSessionById,
    renameSession,
    togglePin,
    deleteMessageById,
    branchFromMessage,
    branchFromSession,
    sendMessage,
    modifyMessage,
    stopStreaming,
    startEditingMessage,
    cancelEditing,
    deleteEmptyCurrentSession,
  }
})
