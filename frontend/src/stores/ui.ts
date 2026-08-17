import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 聊天模式：aichatpro 走普通对话接口，work 走 agent 接口 */
export type ChatMode = 'aichatpro' | 'work'

export const useUiStore = defineStore('ui', () => {
  const sidebarCollapsed = ref(localStorage.getItem('sidebar-collapsed') === '1')

  // 当前聊天模式，持久化到 localStorage，默认 AIChatPro
  const storedMode = localStorage.getItem('chat-mode')
  const chatMode = ref<ChatMode>(storedMode === 'work' ? 'work' : 'aichatpro')

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
    localStorage.setItem('sidebar-collapsed', sidebarCollapsed.value ? '1' : '0')
  }

  function setChatMode(mode: ChatMode) {
    chatMode.value = mode
    localStorage.setItem('chat-mode', mode)
  }

  return { sidebarCollapsed, chatMode, toggleSidebar, setChatMode }
})
