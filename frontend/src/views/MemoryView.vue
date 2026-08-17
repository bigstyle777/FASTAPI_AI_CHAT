<script setup lang="ts">
/**
 * 记忆管理页面
 * - 布局与知识库 / 个人中心一致：侧边栏 + 主内容区
 * - 功能：列表 / 新增 / 卡片内原地编辑 / 删除（二次确认）
 * - 交互：骨架屏、按钮 loading、全局 Toast、空状态、错误兜底
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { useUiStore } from '@/stores/ui'
import { createMemory, deleteMemory, fetchMemories, updateMemory } from '@/api/memory'
import type { MemoryItem } from '@/types'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const themeStore = useThemeStore()
const uiStore = useUiStore()

// ===== Toast =====
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

// ===== 列表状态 =====
const memories = ref<MemoryItem[]>([])
const isLoading = ref(true)
const loadError = ref('')

// ===== 新增 =====
const newContent = ref('')
const isCreating = ref(false)
const canSubmit = computed(() => newContent.value.trim().length > 0 && !isCreating.value)

// ===== 编辑 =====
const editingId = ref<number | null>(null)
const editingContent = ref('')
const isSavingEdit = ref(false)
const canSaveEdit = computed(() => editingContent.value.trim().length > 0 && !isSavingEdit.value)

// ===== 删除 =====
const deleteOpen = ref(false)
const deletingTarget = ref<MemoryItem | null>(null)
const isDeleting = ref(false)

// ===== 工具函数 =====
function formatTime(value: string | null): string {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

// ===== 数据加载 =====
async function loadMemories() {
  isLoading.value = true
  loadError.value = ''
  const result = await fetchMemories()
  if (!result.ok) {
    loadError.value = result.message
  } else if (!result.data.success) {
    loadError.value = '记忆列表加载失败，请稍后重试'
  } else {
    memories.value = [...result.data.memories].reverse()
  }
  isLoading.value = false
}

// ===== 新增 =====
async function handleCreate() {
  const content = newContent.value.trim()
  if (!content || isCreating.value) return

  isCreating.value = true
  const result = await createMemory(content)
  if (!result.ok || !result.data.success) {
    showToast(!result.ok ? result.message : '保存失败，请稍后再试', 'error')
  } else {
    memories.value.unshift(result.data.memory)
    newContent.value = ''
    showToast('记忆已保存')
  }
  isCreating.value = false
}

// ===== 编辑 =====
function startEdit(memory: MemoryItem) {
  editingId.value = memory.id
  editingContent.value = memory.content
}

function cancelEdit() {
  editingId.value = null
  editingContent.value = ''
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && editingId.value !== null) {
    cancelEdit()
  }
}

async function saveEdit() {
  const id = editingId.value
  if (id === null || isSavingEdit.value) return
  const content = editingContent.value.trim()
  if (!content) {
    showToast('记忆内容不能为空', 'error')
    return
  }

  isSavingEdit.value = true
  const result = await updateMemory(id, content)
  if (!result.ok || !result.data.success) {
    showToast(!result.ok ? result.message : '更新失败，请稍后再试', 'error')
  } else {
    const index = memories.value.findIndex((m) => m.id === id)
    if (index >= 0) memories.value[index] = result.data.memory
    cancelEdit()
    showToast('记忆已更新')
  }
  isSavingEdit.value = false
}

// ===== 删除 =====
function confirmDelete(memory: MemoryItem) {
  deletingTarget.value = memory
  deleteOpen.value = true
}

function closeDelete() {
  if (!isDeleting.value) {
    deleteOpen.value = false
    deletingTarget.value = null
  }
}

async function handleDelete() {
  const target = deletingTarget.value
  if (!target || isDeleting.value) return

  isDeleting.value = true
  const result = await deleteMemory(target.id)
  if (!result.ok || !result.data.success) {
    showToast(!result.ok ? result.message : '删除失败，请稍后再试', 'error')
  } else {
    memories.value = memories.value.filter((m) => m.id !== target.id)
    deleteOpen.value = false
    deletingTarget.value = null
    showToast(result.data.message || '记忆已删除')
  }
  isDeleting.value = false
}

// ===== 侧边栏 =====
const isAdmin = computed(() => authStore.isAdmin)

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
  await loadMemories()
  document.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="knowledge-page">
    <!-- ===== 侧边栏 ===== -->
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
        <button class="nav-item active" type="button" title="记忆">
          <span class="nav-icon">忆</span>
          <span class="nav-label">记忆</span>
        </button>
        <button
          v-if="isAdmin"
          class="nav-item"
          type="button"
          title="管理后台"
          @click="router.push('/admin')"
        >
          <span class="nav-icon">管</span>
          <span class="nav-label">管理后台</span>
        </button>
        <button class="nav-item" type="button" title="个人中心" @click="router.push('/profile')">
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

    <!-- ===== 主内容区 ===== -->
    <main class="knowledge-main memory-main">
      <!-- 页头 -->
      <header class="page-header">
        <div>
          <h1>记忆管理</h1>
          <p>管理你的长期记忆，AI 会在对话中使用它们更好地理解你。</p>
        </div>
        <div class="header-actions">
          <span class="status-pill">
            <span class="status-dot"></span>
            共 {{ memories.length }} 条
          </span>
          <button
            class="ghost-btn"
            type="button"
            title="刷新列表"
            :disabled="isLoading"
            @click="loadMemories"
          >
            {{ isLoading ? '...' : '↻' }}
          </button>
        </div>
      </header>

      <!-- 新增记忆卡片 -->
      <div class="memory-create-card">
        <div class="memory-create-label">新增记忆</div>
        <textarea
          v-model="newContent"
          class="memory-textarea"
          rows="3"
          maxlength="2000"
          placeholder="记录一条新的记忆…例如：我是一名后端工程师，偏好简洁的回答。"
        ></textarea>
        <div class="memory-create-footer">
          <span
            class="memory-char-count"
            :class="{ 'over-limit': newContent.length >= 2000 }"
          >{{ newContent.length }}/2000</span>
          <button
            class="memory-submit-btn"
            :disabled="!canSubmit"
            @click="handleCreate"
          >
            <span v-if="isCreating" class="btn-spinner"></span>
            <span v-else>+</span>
            {{ isCreating ? '保存中…' : '保存记忆' }}
          </button>
        </div>
      </div>

      <!-- 加载骨架屏 -->
      <div v-if="isLoading" class="memory-grid">
        <div v-for="i in 6" :key="i" class="memory-card skeleton-card">
          <div class="skeleton-line w-90"></div>
          <div class="skeleton-line w-70"></div>
          <div class="skeleton-line w-50 short"></div>
        </div>
      </div>

      <!-- 加载失败 -->
      <div v-else-if="loadError" class="memory-error-state">
        <div class="memory-error-icon">!</div>
        <h2>加载失败</h2>
        <p>{{ loadError }}</p>
        <button class="modal-btn primary" style="margin-top: 12px;" @click="loadMemories">
          ↻ 重新加载
        </button>
      </div>

      <!-- 空状态 -->
      <div v-else-if="memories.length === 0" class="memory-empty-state">
        <div class="memory-empty-icon">忆</div>
        <h2>还没有任何记忆</h2>
        <p>在上方输入框写下第一条记忆，AI 会在之后的对话中记住它，让回答更懂你。</p>
        <button
          class="modal-btn primary"
          style="margin-top: 16px;"
          @click="router.push('/chat')"
        >
          去对话中试试
        </button>
      </div>

      <!-- 记忆卡片网格 -->
      <div v-else class="memory-grid">
        <div
          v-for="memory in memories"
          :key="memory.id"
          :class="['memory-card', { editing: editingId === memory.id }]"
        >
          <!-- 阅读模式 -->
          <template v-if="editingId !== memory.id">
            <p class="memory-content">{{ memory.content }}</p>
            <div class="memory-card-footer">
              <div class="memory-meta">
                <span>创建 {{ formatTime(memory.created_at) }}</span>
                <span v-if="memory.updated_at && memory.updated_at !== memory.created_at">
                  更新 {{ formatTime(memory.updated_at) }}
                </span>
              </div>
              <div class="memory-actions">
                <button
                  class="memory-action-btn"
                  type="button"
                  @click="startEdit(memory)"
                >编辑</button>
                <button
                  class="memory-action-btn danger"
                  type="button"
                  @click="confirmDelete(memory)"
                >删除</button>
              </div>
            </div>
          </template>

          <!-- 编辑模式 -->
          <template v-else>
            <textarea
              v-model="editingContent"
              class="memory-textarea edit"
              rows="4"
              maxlength="2000"
            ></textarea>
            <div class="memory-edit-footer">
              <span class="memory-edit-hint">Esc 取消</span>
              <div class="memory-edit-actions">
                <span class="memory-char-count">{{ editingContent.length }}/2000</span>
                <button
                  class="modal-btn ghost"
                  type="button"
                  :disabled="isSavingEdit"
                  @click="cancelEdit"
                >取消</button>
                <button
                  class="modal-btn primary"
                  type="button"
                  :disabled="!canSaveEdit"
                  @click="saveEdit"
                >
                  <span v-if="isSavingEdit" class="btn-spinner" style="border-color: rgba(255,255,255,0.35); border-top-color: #fff;"></span>
                  {{ isSavingEdit ? '保存中…' : '保存' }}
                </button>
              </div>
            </div>
          </template>
        </div>
      </div>
    </main>

    <!-- 删除确认弹窗 -->
    <div v-if="deleteOpen" class="modal-overlay" @click.self="closeDelete">
      <div class="modal-card">
        <h3>删除这条记忆？</h3>
        <p style="color: var(--text-muted); font-size: 13px; margin-bottom: 14px;">
          删除后不可恢复，AI 将不再记住这条内容。
        </p>
        <div class="memory-delete-preview">
          {{ deletingTarget?.content }}
        </div>
        <div class="modal-actions">
          <button
            class="modal-btn ghost"
            type="button"
            :disabled="isDeleting"
            @click="closeDelete"
          >取消</button>
          <button
            class="modal-btn danger-solid"
            type="button"
            :disabled="isDeleting"
            @click="handleDelete"
          >
            <span v-if="isDeleting" class="btn-spinner"></span>
            {{ isDeleting ? '删除中…' : '确认删除' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 全局 Toast -->
    <div v-if="toasts.length" class="toast-stack">
      <div
        v-for="toast in toasts"
        :key="toast.id"
        :class="['toast-item', toast.type]"
      >
        <span class="toast-icon">{{ toast.type === 'success' ? '✓' : '!' }}</span>
        {{ toast.message }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.over-limit {
  color: var(--danger) !important;
}
</style>