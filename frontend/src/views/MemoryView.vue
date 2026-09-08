<script setup lang="ts">
/**
 * 记忆管理页面
 * - 布局与知识库 / 个人中心一致：侧边栏 + 主内容区
 * - 功能：列表 / 新增 / 卡片内原地编辑 / 删除（二次确认）/ 整理（合并、清理）
 * - 交互：骨架屏、按钮 loading、全局 Toast、空状态、错误兜底
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'
import { useUiStore } from '@/stores/ui'
import {
  consolidateMemories,
  createMemory,
  decayMemories,
  deleteMemory,
  fetchDuplicates,
  fetchMemories,
  updateMemory,
} from '@/api/memory'
import type { MemoryDuplicatePair, MemoryItem, MemoryType } from '@/types'

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
const newType = ref<MemoryType>('semantic')
const newContent = ref('')
const newKey = ref('')
const newEventType = ref('')
const isCreating = ref(false)

const canSubmit = computed(() => {
  const text = newContent.value.trim()
  if (!text || isCreating.value) return false
  if (newType.value === 'semantic') return text.length > 0
  return text.length > 0
})

// ===== 编辑 =====
const editingId = ref<number | null>(null)
const editingContent = ref('')
const isSavingEdit = ref(false)
const canSaveEdit = computed(() => editingContent.value.trim().length > 0 && !isSavingEdit.value)

// ===== 删除 =====
const deleteOpen = ref(false)
const deletingTarget = ref<MemoryItem | null>(null)
const isDeleting = ref(false)

// ===== 整理（合并 / 清理）=====
// 两个动作在后端都是异步入队（每组合并要调一次 LLM，清扫要扫全量记忆），
// 所以这里只负责提交并提示，结果靠用户稍后点刷新查看。
const maintainOpen = ref(false)
const isConsolidating = ref(false)
const isDecaying = ref(false)
const isMaintaining = computed(() => isConsolidating.value || isDecaying.value)

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
  // 疑似重复检测是锦上添花：失败就当没有，不挡主流程
  await loadDuplicates()
}

// ===== 疑似重复（同一个意思被记成了两条）=====
// key 由提取时的模型生成，同一个槽位可能被写成两个近义的 key。
// 后端只挑出可疑对子并给一条保留建议；合并由用户走已有的编辑/删除完成。
const duplicates = ref<MemoryDuplicatePair[]>([])
const duplicatesExpanded = ref(true)

async function loadDuplicates() {
  const result = await fetchDuplicates()
  duplicates.value = result.ok && result.data.success ? result.data.duplicates : []
}

/** 选定保留哪条，另一条走既有的删除二次确认（删除不可恢复，不跳过确认） */
function resolveDuplicate(keep: MemoryItem, drop: MemoryItem) {
  confirmDelete(drop)
}

// ===== 新增 =====
async function handleCreate() {
  const content = newContent.value.trim()
  if (!content || isCreating.value) return

  isCreating.value = true
  const payload = newType.value === 'semantic'
    ? { type: 'semantic' as const, value: content, key: newKey.value.trim() || null }
    : {
        type: 'episodic' as const,
        summary: content,
        event_type: newEventType.value.trim() || null,
      }

  const result = await createMemory(payload)
  if (!result.ok || !result.data.success) {
    showToast(!result.ok ? result.message : '保存失败，请稍后再试', 'error')
  } else {
    memories.value.unshift(result.data.memory)
    newContent.value = ''
    newKey.value = ''
    newEventType.value = ''
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
  if (e.key !== 'Escape') return
  if (editingId.value !== null) {
    cancelEdit()
    return
  }
  if (maintainOpen.value) {
    closeMaintain()
    return
  }
  if (deleteOpen.value) {
    closeDelete()
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
    // 用户可能刚把两条合并成了一条，重新检测一遍
    await loadDuplicates()
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
    // 被删的可能是某组"疑似重复"里的一条，重新算一遍
    await loadDuplicates()
  }
  isDeleting.value = false
}

// ===== 整理（合并 / 清理）=====
function openMaintain() {
  maintainOpen.value = true
}

function closeMaintain() {
  if (!isMaintaining.value) maintainOpen.value = false
}

async function handleConsolidate() {
  if (isMaintaining.value) return

  isConsolidating.value = true
  const result = await consolidateMemories()
  if (!result.ok || !result.data.success) {
    showToast(!result.ok ? result.message : '合并任务提交失败，请稍后再试', 'error')
  } else {
    showToast(result.data.message || '记忆合并任务已提交')
    maintainOpen.value = false
  }
  isConsolidating.value = false
}

async function handleDecay() {
  if (isMaintaining.value) return

  isDecaying.value = true
  const result = await decayMemories()
  if (!result.ok || !result.data.success) {
    showToast(!result.ok ? result.message : '清理任务提交失败，请稍后再试', 'error')
  } else {
    showToast(result.data.message || '记忆清理任务已提交')
    maintainOpen.value = false
  }
  isDecaying.value = false
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
            title="整理记忆：合并重复事件、归档长期未用的记忆"
            :disabled="isLoading || isMaintaining"
            @click="openMaintain"
          >
            整理
          </button>
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
        <div class="memory-type-tabs">
          <button
            v-for="opt in [
              { value: 'semantic', label: '语义', hint: '稳定事实 / 偏好' },
              { value: 'episodic', label: '事件', hint: '这次对话里发生了什么' },
            ]"
            :key="opt.value"
            :class="['memory-type-tab', { active: newType === opt.value }]"
            type="button"
            @click="newType = opt.value as MemoryType"
          >
            {{ opt.label }}
          </button>
        </div>
        <input
          v-if="newType === 'semantic'"
          v-model="newKey"
          class="memory-key-input"
          maxlength="120"
          placeholder="key（可选，留空按内容自动生成）"
        />
        <input
          v-else
          v-model="newEventType"
          class="memory-key-input"
          maxlength="40"
          placeholder="事件类型，例如 decision / preference_change（可选）"
        />
        <textarea
          v-model="newContent"
          class="memory-textarea"
          rows="3"
          maxlength="2000"
          :placeholder="newType === 'semantic' ? '记录一条稳定的事实或偏好，例如：偏好 4 个空格缩进。' : '记录这次对话里发生了什么决定，例如：决定知识库改用 pgvector。'"
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

      <!-- 疑似重复提醒：同一个意思被记成了两条 -->
      <div
        v-if="!isLoading && !loadError && duplicates.length > 0"
        class="duplicate-card"
      >
        <button
          class="duplicate-header"
          type="button"
          @click="duplicatesExpanded = !duplicatesExpanded"
        >
          <span class="duplicate-title">疑似重复的记忆</span>
          <span class="duplicate-count">{{ duplicates.length }} 组</span>
          <span class="duplicate-toggle">{{ duplicatesExpanded ? '收起' : '展开' }}</span>
        </button>

        <div v-if="duplicatesExpanded">
          <p class="duplicate-hint">
            每组两条内容高度相似，可能是同一个意思被记成了两条（例如 key 写成了
            backend_framework 和 backend_stack）。同时存在会让 AI 在对话里看到
            自相矛盾的信息。删掉其中一条即可，或先点"编辑"把内容合并再删另一条。
          </p>
          <div
            v-for="(pair, index) in duplicates"
            :key="`${pair.keep.id}-${pair.drop.id}-${index}`"
            class="duplicate-pair"
          >
            <div class="duplicate-side">
              <div class="duplicate-side-head">
                <span class="duplicate-badge keep">建议保留</span>
                <span class="duplicate-key">{{ pair.keep.key || '（无 key）' }}</span>
              </div>
              <p class="duplicate-text">{{ pair.keep.content }}</p>
            </div>
            <div class="duplicate-side">
              <div class="duplicate-side-head">
                <span class="duplicate-badge drop">
                  疑似重复 · 相似度 {{ Math.round(pair.similarity * 100) }}%
                </span>
                <span class="duplicate-key">{{ pair.drop.key || '（无 key）' }}</span>
              </div>
              <p class="duplicate-text">{{ pair.drop.content }}</p>
              <div class="duplicate-actions">
                <button
                  class="memory-action-btn danger"
                  type="button"
                  @click="resolveDuplicate(pair.keep, pair.drop)"
                >保留另一条，删除这条</button>
              </div>
            </div>
          </div>
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
            <div class="memory-content-row">
              <span :class="['memory-type-pill', memory.type]">
                {{ memory.type === 'semantic' ? '语义' : '事件' }}
              </span>
              <p class="memory-content">{{ memory.content }}</p>
            </div>
            <div v-if="memory.key || memory.event_type" class="memory-sub-meta">
              <span v-if="memory.key">key: {{ memory.key }}</span>
              <span v-if="memory.event_type">事件类型: {{ memory.event_type }}</span>
            </div>
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

    <!-- 整理记忆弹窗：合并重复事件 / 归档长期未用 -->
    <div v-if="maintainOpen" class="modal-overlay" @click.self="closeMaintain">
      <div class="modal-card">
        <h3>整理记忆</h3>
        <p style="color: var(--text-muted); font-size: 13px; margin-bottom: 14px;">
          两个动作都在后台异步执行（需要调用模型与扫描全量记忆），
          提交后稍等片刻再点刷新查看结果。
        </p>

        <div class="memory-maintain-item">
          <div class="memory-maintain-text">
            <div class="memory-maintain-title">合并重复事件</div>
            <div class="memory-maintain-hint">
              把近期反复发生的同类事件归纳成一条更高层的总结。
              原文不会删除，会标为“已合并”并保留溯源。
            </div>
          </div>
          <button
            class="modal-btn primary"
            type="button"
            :disabled="isMaintaining"
            @click="handleConsolidate"
          >
            <span
              v-if="isConsolidating"
              class="btn-spinner"
              style="border-color: rgba(255,255,255,0.35); border-top-color: #fff;"
            ></span>
            {{ isConsolidating ? '提交中…' : '合并' }}
          </button>
        </div>

        <div class="memory-maintain-item">
          <div class="memory-maintain-text">
            <div class="memory-maintain-title">清理长期未用的记忆</div>
            <div class="memory-maintain-hint">
              归档已过期的、以及长期没被用到且重要性低的记忆。
              归档只是不再参与检索与展示，不会物理删除。
            </div>
          </div>
          <button
            class="modal-btn ghost"
            type="button"
            :disabled="isMaintaining"
            @click="handleDecay"
          >
            <span v-if="isDecaying" class="btn-spinner"></span>
            {{ isDecaying ? '提交中…' : '清理' }}
          </button>
        </div>

        <div class="modal-actions">
          <button
            class="modal-btn ghost"
            type="button"
            :disabled="isMaintaining"
            @click="closeMaintain"
          >关闭</button>
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

.memory-type-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.memory-type-tab {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--border-tertiary, rgba(0, 0, 0, 0.12));
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
  transition: all 0.15s ease;
}

.memory-type-tab.active {
  background: var(--accent, #378add);
  border-color: var(--accent, #378add);
  color: #fff;
}

.memory-key-input {
  width: 100%;
  padding: 8px 12px;
  margin-bottom: 10px;
  border: 1px solid var(--border-tertiary, rgba(0, 0, 0, 0.12));
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary);
  font-size: 13px;
  box-sizing: border-box;
}

.memory-content-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 6px;
}

.memory-type-pill {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  line-height: 18px;
}

.memory-type-pill.semantic {
  background: rgba(55, 138, 221, 0.12);
  color: #185fa5;
}

.memory-type-pill.episodic {
  background: rgba(186, 117, 23, 0.12);
  color: #854f0b;
}

.memory-content-row .memory-content {
  flex: 1;
  margin: 0;
}

.memory-sub-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 11px;
  color: var(--text-tertiary);
  margin-bottom: 8px;
}

/* ===== 疑似重复提醒 ===== */
.duplicate-card {
  margin-bottom: 16px;
  padding: 14px 16px;
  border: 1px solid rgba(133, 79, 11, 0.28);
  border-radius: 12px;
  background: rgba(234, 243, 222, 0.55);
}

.duplicate-header {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.duplicate-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.duplicate-count {
  padding: 1px 8px;
  border-radius: 999px;
  background: rgba(133, 79, 11, 0.14);
  color: #854f0b;
  font-size: 11px;
  font-weight: 500;
}

.duplicate-toggle {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-tertiary);
}

.duplicate-hint {
  margin: 10px 0 12px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.duplicate-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding: 12px;
  margin-bottom: 10px;
  border: 1px solid var(--border-tertiary, rgba(0, 0, 0, 0.12));
  border-radius: 10px;
  background: var(--bg-secondary, rgba(255, 255, 255, 0.6));
}

.duplicate-pair:last-child {
  margin-bottom: 0;
}

.duplicate-side-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 6px;
}

.duplicate-badge {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
}

.duplicate-badge.keep {
  background: rgba(99, 153, 34, 0.16);
  color: #3b6d11;
}

.duplicate-badge.drop {
  background: rgba(226, 75, 74, 0.14);
  color: #a32d2d;
}

.duplicate-key {
  font-size: 11px;
  color: var(--text-tertiary);
  word-break: break-all;
}

.duplicate-text {
  margin: 0 0 8px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
  word-break: break-word;
}

.duplicate-actions {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 720px) {
  .duplicate-pair {
    grid-template-columns: 1fr;
  }
}
</style>