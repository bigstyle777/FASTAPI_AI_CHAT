<script setup lang="ts">
import { ref, watch, nextTick, onMounted } from 'vue'
import type { ChatMessage } from '@/types'
import type { TokenUsage as TokenUsageType } from '@/types'
import { useChatStore } from '@/stores/chat'
import { useMarkdown } from '@/composables/useMarkdown'
import TokenUsage from './TokenUsage.vue'
import { useContextMenu } from '@/composables/useContextMenu'
import type { MenuItem } from '@/composables/useContextMenu'

const props = defineProps<{
  message: ChatMessage
  isStreaming?: boolean
}>()

const chatStore = useChatStore()
const { renderFull, renderStream, resetStream } = useMarkdown()
const contentRef = ref<HTMLElement | null>(null)
const likeActive = ref(false)
const dislikeActive = ref(false)

const { visible: menuVisible, position: menuPosition, items: menuItems, open: menuOpen, close: menuClose } = useContextMenu()

function getMessageUsage(): TokenUsageType | null {
  if (!props.message.total_tokens) return null
  return {
    model: props.message.model || null,
    prompt_tokens: props.message.prompt_tokens || 0,
    completion_tokens: props.message.completion_tokens || 0,
    total_tokens: props.message.total_tokens || 0,
    is_inherited: props.message.is_inherited,
  }
}

// 是否处于「等待 AI 回复」的思考状态
function isThinking(): boolean {
  if (props.message.role !== 'assistant') return false
  if (!props.isStreaming) return false
  // 消息内容为空 或 内容仅是我们手动设置的等待提示文本
  return !props.message.content || props.message.content === '正在思考...'
}

async function renderContent() {
  if (!contentRef.value) return

  if (props.message.role === 'user') {
    // 用户消息直接显示文本，无需 markdown 渲染
    contentRef.value.textContent = props.message.content
  } else if (props.message.role === 'assistant') {
    // 思考中状态不渲染，由模板中的 thinking-indicator 接管显示
    if (isThinking()) return

    if (props.isStreaming && chatStore.streamingContent) {
      renderStream(contentRef.value, chatStore.streamingContent)
    } else {
      resetStream()
      await renderFull(contentRef.value, props.message.content)
    }
  }
}

watch(
  () => chatStore.streamingContent,
  () => {
    if (props.isStreaming) {
      renderContent()
      scrollParentToBottom()
    }
  },
)

watch(
  () => props.message.content,
  () => {
    if (!props.isStreaming) {
      renderContent()
    }
  },
)

// 组件挂载后渲染初始内容
onMounted(() => {
  if (!props.isStreaming) {
    renderContent()
  }
})

watch(
  () => props.isStreaming,
  (streaming, wasStreaming) => {
    if (wasStreaming && !streaming) {
      nextTick(() => renderContent())
    }
  },
)

function scrollParentToBottom() {
  const container = contentRef.value?.closest('.messages-container')
  if (container) {
    container.scrollTop = container.scrollHeight
  }
}

function openMenu(event: MouseEvent) {
  event.stopPropagation()
  const trigger = event.currentTarget as HTMLElement
  const items: MenuItem[] = []

  if (props.message.role === 'user' && !props.message.is_inherited) {
    items.push({
      label: '修改消息',
      action: () => {
        chatStore.startEditingMessage(props.message.message_id, props.message.content)
        menuClose()
      },
    })
  }

  items.push({
    label: props.message.role === 'user' ? '在新对话中建立分支' : '在新分支中新建对话',
    action: () => {
      chatStore.branchFromMessage(props.message.message_id)
      menuClose()
    },
  })

  if (!props.message.is_inherited) {
    items.push({
      label: '删除消息',
      danger: true,
      action: () => {
        if (window.confirm('确定删除这条消息吗？')) {
          chatStore.deleteMessageById(props.message.message_id)
        }
        menuClose()
      },
    })
  }

  menuOpen(trigger, items, props.message.role === 'user')
}

async function copyContent() {
  try {
    await navigator.clipboard.writeText(props.message.content)
    chatStore.showNotice('已复制到剪贴板', 'success')
  } catch {
    chatStore.showNotice('复制失败', 'error')
  }
}

function toggleFeedback(type: 'like' | 'dislike') {
  if (type === 'like') {
    likeActive.value = !likeActive.value
    dislikeActive.value = false
    if (likeActive.value) chatStore.showNotice('已点赞', 'success')
  } else {
    dislikeActive.value = !dislikeActive.value
    likeActive.value = false
    if (dislikeActive.value) chatStore.showNotice('已点踩', 'success')
  }
}
</script>

<template>
  <div :class="['message-group', message.role]" :data-message-id="message.message_id">
    <!-- 思考中动画 -->
    <div v-if="isThinking()" class="thinking-indicator">
      <span class="thinking-text">正在思考中</span>
      <span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span>
    </div>
    <div ref="contentRef" :class="['message', message.role]" v-show="!isThinking()"></div>

    <TokenUsage v-if="message.role === 'assistant'" :usage="getMessageUsage()" />

    <div v-if="!message.is_inherited || message.role === 'user'" class="message-actions">
      <template v-if="message.role === 'assistant' && !message.is_inherited">
        <button
          type="button"
          class="message-action-btn like-btn"
          :class="{ active: likeActive }"
          aria-label="点赞"
          title="点赞"
          @click.stop="toggleFeedback('like')"
        >&#128077;</button>
        <button
          type="button"
          class="message-action-btn dislike-btn"
          :class="{ active: dislikeActive }"
          aria-label="点踩"
          title="点踩"
          @click.stop="toggleFeedback('dislike')"
        >&#128078;</button>
        <button
          type="button"
          class="message-action-btn copy-btn"
          aria-label="复制"
          title="复制"
          @click.stop="copyContent"
        >&#128203;</button>
      </template>
      <button
        type="button"
        class="message-menu-trigger"
        aria-label="消息操作"
        title="更多操作"
        @click="openMenu"
      >&#8942;</button>
    </div>

    <Teleport to="body">
      <div
        v-if="menuVisible"
        class="context-menu"
        :style="{ top: menuPosition.top + 'px', left: menuPosition.left + 'px' }"
      >
        <button
          v-for="(item, idx) in menuItems"
          :key="idx"
          type="button"
          :class="['context-menu-item', { danger: item.danger }]"
          @click.stop="item.action"
        >
          {{ item.label }}
        </button>
      </div>
    </Teleport>
  </div>
</template>
