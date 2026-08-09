<script setup lang="ts">
import type { ChatSession } from '@/types'
import { useChatStore } from '@/stores/chat'
import { useContextMenu } from '@/composables/useContextMenu'
import type { MenuItem } from '@/composables/useContextMenu'

const props = defineProps<{
  session: ChatSession
  isActive: boolean
}>()

const emit = defineEmits<{
  rename: [sessionId: number, title: string]
}>()

const chatStore = useChatStore()
const { visible: menuVisible, position: menuPosition, items: menuItems, open: menuOpen, close: menuClose } = useContextMenu()

function selectSession() {
  chatStore.loadSessionMessages(props.session.session_id)
}

function openMenu(event: MouseEvent) {
  event.stopPropagation()
  const trigger = event.currentTarget as HTMLElement
  const items: MenuItem[] = [
    {
      label: props.session.is_pinned ? '取消置顶' : '置顶聊天',
      action: () => {
        chatStore.togglePin(props.session.session_id, !props.session.is_pinned)
        menuClose()
      },
    },
    {
      label: '重命名会话',
      action: () => {
        emit('rename', props.session.session_id, props.session.title)
        menuClose()
      },
    },
    {
      label: '删除会话',
      danger: true,
      action: () => {
        if (window.confirm('确定删除该会话吗？所有消息记录也会一并删除。')) {
          chatStore.deleteSessionById(props.session.session_id)
        }
        menuClose()
      },
    },
  ]
  menuOpen(trigger, items)
}
</script>

<template>
  <div
    :class="['session-item', { active: isActive, pinned: session.is_pinned }]"
    @click="selectSession"
  >
    <div class="session-row">
      <div class="session-content">
        <div class="session-title">{{ session.title || '新会话' }}</div>
        <div class="session-last-message">{{ session.last_message || '暂无消息' }}</div>
      </div>
      <button
        type="button"
        class="session-menu-trigger"
        aria-label="会话操作"
        title="更多操作"
        @click="openMenu"
      >&#8942;</button>
    </div>
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
</template>
