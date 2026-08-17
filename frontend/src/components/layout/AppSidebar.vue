<script setup lang="ts">
/**
 * 全局共享侧边栏组件
 * - 所有页面共用，消除重复代码
 * - 支持 chat 模式（含会话列表、品牌模式切换）和 nav 模式（知识库/记忆/个人中心等）
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { useUiStore } from '@/stores/ui'

const props = withDefaults(
  defineProps<{
    /** 侧边栏模式：chat 显示会话列表，nav 显示导航 */
    mode?: 'chat' | 'nav'
    /** 当 mode='nav' 时，当前激活的导航项 ID */
    activeNav?: string
    /** 返回按钮文字，mode='nav' 时有效 */
    backLabel?: string
    /** 是否显示品牌模式切换（AIChatPro / Work） */
    showBrandToggle?: boolean
  }>(),
  {
    mode: 'chat',
    activeNav: '',
    backLabel: '返回聊天',
    showBrandToggle: false,
  },
)

const emit = defineEmits<{
  'new-chat': []
  'logout': []
  'back': []
}>()

const router = useRouter()
const authStore = useAuthStore()
const themeStore = useThemeStore()
const uiStore = useUiStore()

const isAdmin = computed(() => authStore.isAdmin)

const themeLabel = (t: string) => {
  return t === 'light' ? '亮色' : t === 'dark' ? '暗色' : '系统'
}

const navItems = [
  { id: 'chat', label: '对话', icon: '聊', route: '/chat' },
  { id: 'knowledge', label: '知识库', icon: '库', route: '/knowledge' },
  { id: 'memory', label: '记忆', icon: '忆', route: '/memory' },
  { id: 'admin', label: '管理后台', icon: '管', route: '/admin', adminOnly: true },
  { id: 'profile', label: '个人中心', icon: '我', route: '/profile' },
]

const visibleNavItems = computed(() =>
  navItems.filter((item) => !item.adminOnly || isAdmin.value),
)
</script>

<template>
  <aside :class="['sidebar', { collapsed: uiStore.sidebarCollapsed }]">
    <!-- 头部：品牌信息 + 切换按钮 -->
    <div class="sidebar-header">
      <div class="brand-row">
        <img src="/logo.png" class="brand-logo small" alt="AI Chat Pro" />
        <div class="brand-text">
          <template v-if="showBrandToggle">
            <div class="brand-mode-toggle">
              <button
                :class="['mode-segment', { active: uiStore.chatMode === 'aichatpro' }]"
                type="button"
                @click="uiStore.setChatMode('aichatpro')"
              >
                AIChatPro
              </button>
              <button
                :class="['mode-segment', { active: uiStore.chatMode === 'work' }]"
                type="button"
                @click="uiStore.setChatMode('work')"
              >
                Work
              </button>
            </div>
            <p>{{ uiStore.chatMode === 'work' ? 'Agent 任务工作台' : '智能对话工作台' }}</p>
          </template>
          <template v-else>
            <h2>AI Chat Pro</h2>
            <p>智能对话工作台</p>
          </template>
        </div>
        <button
          class="sidebar-toggle"
          type="button"
          :title="uiStore.sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
          @click="uiStore.toggleSidebar()"
        >
          {{ uiStore.sidebarCollapsed ? '>' : '<' }}
        </button>
      </div>

      <!-- 动作按钮 -->
      <button
        v-if="mode === 'chat'"
        class="new-chat-btn"
        type="button"
        :title="uiStore.sidebarCollapsed ? '新建聊天' : ''"
        @click="emit('new-chat')"
      >
        {{ uiStore.sidebarCollapsed ? '+' : '新建聊天' }}
      </button>
      <button
        v-else
        class="new-chat-btn"
        type="button"
        @click="emit('back')"
      >
        {{ uiStore.sidebarCollapsed ? '+' : backLabel }}
      </button>
    </div>

    <!-- 会话列表 (chat 模式) -->
    <div v-if="mode === 'chat' && !uiStore.sidebarCollapsed" class="session-list">
      <slot name="session-list" />
    </div>

    <!-- 导航 (nav 模式) -->
    <div v-if="mode === 'nav'" class="sidebar-nav">
      <button
        v-for="item in visibleNavItems"
        :key="item.id"
        :class="['nav-item', { active: activeNav === item.id }]"
        type="button"
        :title="item.label"
        @click="router.push(item.route)"
      >
        <span class="nav-icon">{{ item.icon }}</span>
        <span class="nav-label">{{ item.label }}</span>
      </button>
    </div>

    <!-- 底部：主题切换、退出、元信息 -->
    <div class="sidebar-footer">
      <div v-if="!uiStore.sidebarCollapsed" class="theme-switcher">
        <button
          v-for="t in (['light', 'dark', 'system'] as const)"
          :key="t"
          :class="['theme-btn', { active: themeStore.theme === t }]"
          type="button"
          @click="themeStore.setTheme(t)"
        >
          {{ themeLabel(t) }}
        </button>
      </div>
      <button
        class="ghost-btn danger"
        type="button"
        :title="uiStore.sidebarCollapsed ? '退出登录' : ''"
        @click="emit('logout')"
      >
        {{ uiStore.sidebarCollapsed ? '退' : '退出登录' }}
      </button>
      <div v-if="!uiStore.sidebarCollapsed" class="sidebar-meta">
        <span>{{ authStore.user?.username || '--' }}</span>
        <span>{{ authStore.user?.role || '--' }}</span>
      </div>
    </div>
  </aside>
</template>