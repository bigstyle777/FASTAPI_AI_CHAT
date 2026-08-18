<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch, nextTick, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useChatStore } from '@/stores/chat'
import { useThemeStore } from '@/stores/theme'
import { useUiStore } from '@/stores/ui'
import SessionItem from '@/components/chat/SessionItem.vue'
import MessageItem from '@/components/chat/MessageItem.vue'
import RenameModal from '@/components/chat/RenameModal.vue'
import ChatMinimap from '@/components/chat/ChatMinimap.vue'
import AgentPanel from '@/components/chat/AgentPanel.vue'

const router = useRouter()
const authStore = useAuthStore()
const chatStore = useChatStore()
const themeStore = useThemeStore()
const uiStore = useUiStore()

const messageInput = ref('')
const messagesContainer = ref<HTMLElement | null>(null)
const isInitialLoading = ref(true)

// 重命名模态框状态
const renameModalVisible = ref(false)
const renameSessionId = ref(0)
const renameCurrentTitle = ref('')
const renameModalRef = ref<InstanceType<typeof RenameModal> | null>(null)

const isAdmin = computed(() => authStore.isAdmin)

const isStreamingLast = computed(() => {
  if (!chatStore.isSending) return false
  const lastMsg = chatStore.messages[chatStore.messages.length - 1]
  return lastMsg?.role === 'assistant'
})

function scrollToBottom() {
  nextTick(() => {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  })
}

watch(() => chatStore.messages.length, scrollToBottom)
watch(() => chatStore.streamingContent, scrollToBottom)

function handleSend() {
  if (chatStore.isSending) {
    chatStore.stopStreaming()
    return
  }
  const text = messageInput.value.trim()
  if (!text) return
  messageInput.value = ''
  // Work 模式走 agent 接口（/agent/stream），AIChatPro 走普通对话接口
  if (uiStore.chatMode === 'work') {
    chatStore.sendAgentMessage(text)
  } else {
    chatStore.sendMessage(text)
  }
}

function handleInputKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    handleSend()
  }
}

function openRenameModal(sessionId: number, title: string) {
  renameSessionId.value = sessionId
  renameCurrentTitle.value = title
  renameModalVisible.value = true
}

async function handleRenameSave(newTitle: string) {
  const success = await chatStore.renameSession(renameSessionId.value, newTitle)
  renameModalRef.value?.setSaving(false)
  if (success) {
    renameModalVisible.value = false
  }
}

async function handleLogout() {
  await authStore.logout()
  router.push('/login')
}

async function initChat() {
  try {
    if (!authStore.isLoggedIn) {
      router.push('/login')
      return
    }
    // 先确保用户信息和 token 有效
    if (!authStore.user) {
      await authStore.loadProfile()
    }
    // 如果 profile 加载失败（token 过期等），跳转登录
    if (!authStore.user) {
      router.push('/login')
      return
    }
    await authStore.loadSettings()
    await chatStore.loadSessions()
  } catch (e) {
    console.error('ChatView 初始化失败:', e)
  } finally {
    isInitialLoading.value = false
  }
}

onMounted(() => {
  initChat()
})

// 防止组件卸载后还在更新状态
onUnmounted(() => {
  isInitialLoading.value = false
})

// 监听编辑消息事件
watch(
  () => chatStore.editingMessageId,
  (id) => {
    if (id !== null) {
      const msg = chatStore.messages.find((m) => m.message_id === id)
      if (msg) {
        messageInput.value = msg.content
        nextTick(() => {
          const input = document.querySelector('.chat-input') as HTMLInputElement
          input?.focus()
        })
      }
    }
  },
)
</script>

<template>
  <div class="app-shell">
    <!-- 侧边栏 -->
    <aside :class="['sidebar', { collapsed: uiStore.sidebarCollapsed }]">
      <div class="sidebar-header">
        <div class="brand-row">
          <img src="/logo.png" class="brand-logo small" alt="AI Chat Pro">
          <div class="brand-text">
            <div class="brand-mode-toggle">
              <button
                :class="['mode-segment', { active: uiStore.chatMode === 'aichatpro' }]"
                type="button"
                @click="uiStore.setChatMode('aichatpro')"
              >AIChatPro</button>
              <button
                :class="['mode-segment', { active: uiStore.chatMode === 'work' }]"
                type="button"
                @click="uiStore.setChatMode('work')"
              >Work</button>
            </div>
            <p>{{ uiStore.chatMode === 'work' ? 'Agent 任务工作台' : '智能对话工作台' }}</p>
          </div>
          <button
            class="sidebar-toggle"
            type="button"
            :title="uiStore.sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
            @click="uiStore.toggleSidebar()"
          >{{ uiStore.sidebarCollapsed ? '»' : '«' }}</button>
        </div>
        <button
          class="new-chat-btn"
          type="button"
          :title="uiStore.sidebarCollapsed ? '新建聊天' : ''"
          @click="chatStore.createNewSession"
        >{{ uiStore.sidebarCollapsed ? '+' : '新建聊天' }}</button>
      </div>

      <div v-if="!uiStore.sidebarCollapsed" class="session-list">
        <template v-if="isInitialLoading">
          <div class="sidebar-loading">加载中...</div>
        </template>
        <template v-else>
          <SessionItem
            v-for="session in chatStore.sessions"
            :key="session.session_id"
            :session="session"
            :is-active="Number(chatStore.currentSessionId) === Number(session.session_id)"
            @rename="openRenameModal"
          />
          <div v-if="chatStore.sessions.length === 0" class="empty-sessions">暂无会话，点击"新建聊天"开始</div>
        </template>
      </div>

      <div class="sidebar-footer">
        <div class="sidebar-nav">
          <button class="nav-item active" type="button" title="对话">
            <span class="nav-icon">✈</span>
            <span class="nav-label">对话</span>
          </button>
          <button class="nav-item" type="button" title="知识库" @click="router.push('/knowledge')">
            <span class="nav-icon">◈</span>
            <span class="nav-label">知识库</span>
          </button>
          <button class="nav-item" type="button" title="记忆" @click="router.push('/memory')">
            <span class="nav-icon">✦</span>
            <span class="nav-label">记忆</span>
          </button>
          <button v-if="isAdmin" class="nav-item" type="button" title="管理员中心" @click="router.push('/admin')">
            <span class="nav-icon">⚙</span>
            <span class="nav-label">管理员中心</span>
          </button>
          <button class="nav-item" type="button" title="个人中心" @click="router.push('/profile')">
            <span class="nav-icon">☰</span>
            <span class="nav-label">个人中心</span>
          </button>
        </div>
        <div v-if="!uiStore.sidebarCollapsed" class="theme-switcher">
          <button
            v-for="t in ['light', 'dark', 'system']"
            :key="t"
            :class="['theme-btn', { active: themeStore.theme === t }]"
            type="button"
            @click="themeStore.setTheme(t)"
          >
            {{ t === 'light' ? '☀' : t === 'dark' ? '☾' : '◐' }}
          </button>
        </div>
        <button
          class="ghost-btn danger"
          type="button"
          :title="uiStore.sidebarCollapsed ? '退出登录' : ''"
          @click="handleLogout"
        >{{ uiStore.sidebarCollapsed ? '⎋' : '退出登录' }}</button>
        <div v-if="!uiStore.sidebarCollapsed" class="sidebar-meta">
          <span>{{ authStore.user?.username || '--' }}</span>
          <span>{{ authStore.user?.role || '--' }}</span>
        </div>
      </div>
    </aside>

    <!-- 聊天区域 -->
    <main class="chat">
      <header class="chat-header">
        <div>
          <h1>AI Assistant</h1>
          <p>清晰、连续、专注的对话体验</p>
        </div>
        <div class="header-badges">
          <span class="status-pill">在线</span>
        </div>
      </header>

      <div v-if="chatStore.notice.message" :class="['notice', chatStore.notice.type]">
        {{ chatStore.notice.message }}
      </div>

      <div class="chat-body">
        <div ref="messagesContainer" class="messages-container">
          <template v-if="isInitialLoading">
            <div class="empty-state">
              <img src="/logo.png" class="empty-logo" alt="AI Chat Pro">
              <h2>正在加载...</h2>
              <p>正在同步你的会话和消息记录。</p>
            </div>
          </template>
          <template v-else-if="chatStore.messages.length > 0">
            <!-- Work 模式：Agent 任务执行面板 -->
            <AgentPanel v-if="uiStore.chatMode === 'work'" />
            <MessageItem
              v-for="(msg, idx) in chatStore.messages"
              :key="msg.message_id"
              :message="msg"
              :is-streaming="isStreamingLast && idx === chatStore.messages.length - 1"
            />
          </template>
          <div v-else class="empty-state">
            <img src="/logo.png" class="empty-logo" alt="AI Chat Pro">
            <h2>开始一段新的对话</h2>
            <p>选择左侧会话，或者新建聊天后输入你的问题。</p>
          </div>
        </div>

        <ChatMinimap
          :messages="chatStore.messages"
          :container-ref="messagesContainer"
        />
      </div>

      <div class="input-area">
        <input
          v-model="messageInput"
          type="text"
          class="chat-input"
          :placeholder="chatStore.editingMessageId !== null ? '修改消息后发送将更新原消息...' : '输入消息，按 Enter 发送'"
          @keydown="handleInputKeydown"
        >
        <button
          :class="['send-btn', { 'stop-mode': chatStore.isSending }]"
          type="button"
          :aria-label="chatStore.isSending ? '停止生成' : '发送消息'"
          @click="handleSend"
        >{{ chatStore.isSending ? '' : '发送' }}</button>
      </div>
    </main>

    <!-- 重命名模态框 -->
    <RenameModal
      ref="renameModalRef"
      :visible="renameModalVisible"
      :current-title="renameCurrentTitle"
      @close="renameModalVisible = false"
      @save="handleRenameSave"
    />
  </div>
</template>
