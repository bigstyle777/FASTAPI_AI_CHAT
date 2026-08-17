<script setup lang="ts">
/**
 * 个人中心页面
 * - 布局与知识库 / 记忆页一致：侧边栏 + 主内容区
 * - 功能：查看用户信息、配置聊天模型（API Key / Provider）
 * - Embedding 设置不在前端暴露，保存时原样回传后端已有配置，避免被清空
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { useUiStore } from '@/stores/ui'

const router = useRouter()
const authStore = useAuthStore()
const themeStore = useThemeStore()
const uiStore = useUiStore()

const apiKey = ref('')
const provider = ref('deepseek')
const showApiKey = ref(false)
const isLoading = ref(false)

// ===== 全局 Toast（与记忆页一致） =====
interface ToastItem {
  id: number
  message: string
  type: 'success' | 'error'
}
const toasts = ref<ToastItem[]>([])
let toastSeq = 0

function showToast(message: string, type: 'success' | 'error' = 'success') {
  const id = ++toastSeq
  toasts.value.push({ id, message, type })
  window.setTimeout(() => {
    toasts.value = toasts.value.filter((t) => t.id !== id)
  }, 3000)
}

async function handleSave() {
  isLoading.value = true
  try {
    // embedding 三项不再展示编辑，取后端已保存的值原样回传，防止配置被清空
    const success = await authStore.saveSettings(
      apiKey.value.trim(),
      provider.value,
      authStore.settings.embedding_api_key ?? '',
      authStore.settings.embedding_base_url ?? '',
      authStore.settings.embedding_model ?? '',
    )
    if (success) {
      showToast('设置已保存')
    } else {
      showToast('保存设置失败', 'error')
    }
  } catch {
    showToast('保存设置失败', 'error')
  } finally {
    isLoading.value = false
  }
}

async function handleLogout() {
  await authStore.logout()
  router.push('/login')
}

onMounted(async () => {
  if (!authStore.isLoggedIn) {
    router.push('/login')
    return
  }
  if (!authStore.user) {
    await authStore.loadProfile()
  }
  await authStore.loadSettings()
  apiKey.value = authStore.settings.api_key
  provider.value = authStore.settings.provider
})
</script>

<template>
  <div class="knowledge-page">
    <!-- 侧边栏（与知识库 / 记忆页一致的结构） -->
    <aside :class="['sidebar', { collapsed: uiStore.sidebarCollapsed }]">
      <div class="sidebar-header">
        <div class="brand-row">
          <img src="/logo.png" class="brand-logo small" alt="AI Chat Pro">
          <div class="brand-text">
            <h2>AI Chat Pro</h2>
            <p>智能对话工作台</p>
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
        <button class="new-chat-btn" type="button" @click="router.push('/chat')">
          {{ uiStore.sidebarCollapsed ? '+' : '返回聊天' }}
        </button>
      </div>

      <div class="sidebar-nav">
        <button class="nav-item" type="button" title="对话" @click="router.push('/chat')">
          <span class="nav-icon">聊</span>
          <span class="nav-label">对话</span>
        </button>
        <button class="nav-item" type="button" title="知识库" @click="router.push('/knowledge')">
          <span class="nav-icon">库</span>
          <span class="nav-label">知识库</span>
        </button>
        <button class="nav-item" type="button" title="记忆" @click="router.push('/memory')">
          <span class="nav-icon">忆</span>
          <span class="nav-label">记忆</span>
        </button>
        <button
          v-if="authStore.isAdmin"
          class="nav-item"
          type="button"
          title="管理后台"
          @click="router.push('/admin')"
        >
          <span class="nav-icon">管</span>
          <span class="nav-label">管理后台</span>
        </button>
        <button class="nav-item active" type="button" title="个人中心">
          <span class="nav-icon">我</span>
          <span class="nav-label">个人中心</span>
        </button>
      </div>

      <div class="sidebar-footer">
        <div v-if="!uiStore.sidebarCollapsed" class="theme-switcher">
          <button
            v-for="theme in ['light', 'dark', 'system']"
            :key="theme"
            :class="['theme-btn', { active: themeStore.theme === theme }]"
            type="button"
            @click="themeStore.setTheme(theme)"
          >
            {{ theme === 'light' ? '亮色' : theme === 'dark' ? '暗色' : '系统' }}
          </button>
        </div>
        <button class="ghost-btn danger" type="button" @click="handleLogout">
          {{ uiStore.sidebarCollapsed ? '退' : '退出登录' }}
        </button>
        <div v-if="!uiStore.sidebarCollapsed" class="sidebar-meta">
          <span>{{ authStore.user?.username || '--' }}</span>
          <span>{{ authStore.user?.role || '--' }}</span>
        </div>
      </div>
    </aside>

    <!-- 主内容区 -->
    <main class="knowledge-main profile-main">
      <header class="page-header">
        <div>
          <h1>个人中心</h1>
          <p>管理账户信息与聊天模型配置。</p>
        </div>
        <div class="status-pill">
          <span class="status-dot"></span>
          {{ authStore.user?.username || '--' }}
        </div>
      </header>

      <!-- 用户信息 -->
      <section class="profile-card">
        <h2>用户信息</h2>
        <div class="info-row">
          <label>用户名</label>
          <span>{{ authStore.user?.username || '--' }}</span>
        </div>
        <div class="info-row">
          <label>用户 ID</label>
          <span>{{ authStore.user?.user_id || '--' }}</span>
        </div>
        <div class="info-row">
          <label>角色</label>
          <span>{{ authStore.user?.role || '--' }}</span>
        </div>
      </section>

      <!-- 聊天模型设置 -->
      <section class="profile-card">
        <h2>聊天模型设置</h2>
        <div class="form-group api-key-row">
          <label for="apiKeyInput">聊天 API Key</label>
          <div class="inline-field">
            <input
              id="apiKeyInput"
              v-model="apiKey"
              :type="showApiKey ? 'text' : 'password'"
              placeholder="请输入聊天模型 API Key"
              autocomplete="off"
            >
            <button class="toggle-btn" type="button" @click="showApiKey = !showApiKey">
              {{ showApiKey ? '隐藏' : '显示' }}
            </button>
          </div>
        </div>
        <div class="form-group">
          <label for="providerSelect">聊天 Provider</label>
          <select id="providerSelect" v-model="provider">
            <option value="deepseek">DeepSeek</option>
            <option value="openai">OpenAI 兼容接口</option>
          </select>
        </div>
        <div class="profile-card-footer">
          <button
            class="primary-action"
            type="button"
            :disabled="isLoading"
            @click="handleSave"
          >
            <span v-if="isLoading" class="btn-spinner"></span>
            {{ isLoading ? '保存中...' : '保存设置' }}
          </button>
        </div>
      </section>
    </main>

    <!-- 全局 Toast -->
    <div class="toast-stack">
      <div v-for="toast in toasts" :key="toast.id" :class="['toast-item', toast.type]">
        <span class="toast-icon">{{ toast.type === 'success' ? '✓' : '✕' }}</span>
        {{ toast.message }}
      </div>
    </div>
  </div>
</template>
