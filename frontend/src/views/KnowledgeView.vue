<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useRagStore } from '@/stores/rag'
import { useThemeStore } from '@/stores/theme'
import { useUiStore } from '@/stores/ui'

const router = useRouter()
const authStore = useAuthStore()
const ragStore = useRagStore()
const themeStore = useThemeStore()
const uiStore = useUiStore()

const searchQuery = ref('')
const isDragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const isAdmin = computed(() => authStore.isAdmin)
const supportedTypes = '.txt,.md,.markdown,.csv,.json,.log'

const stats = computed(() => {
  const total = ragStore.documents.length
  const ready = ragStore.documents.filter((doc) => doc.status === 'ready').length
  const pending = ragStore.documents.filter(
    (doc) => doc.status === 'pending' || doc.status === 'processing',
  ).length
  const failed = ragStore.documents.filter((doc) => doc.status === 'failed').length
  const totalChunks = ragStore.documents.reduce((sum, doc) => sum + (doc.chunk_count || 0), 0)
  return { total, ready, pending, failed, totalChunks }
})

const stagePercent: Record<string, number> = {
  starting: 0,
  validating: 8,
  saving: 20,
  saved: 32,
  parsing: 45,
  chunking: 58,
  embedding: 78,
  indexing: 92,
  done: 100,
}

const progressPercent = computed(() => {
  const stage = ragStore.uploadProgress?.stage
  return stage ? (stagePercent[stage] ?? 50) : 0
})

function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    starting: '准备上传',
    validating: '校验文件',
    saving: '保存文件',
    saved: '已保存',
    parsing: '解析文档',
    chunking: '切分文本',
    embedding: '生成向量',
    indexing: '写入索引',
    done: '完成',
  }
  return labels[stage] ?? stage
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '--'
  return new Date(dateStr).toLocaleString('zh-CN')
}

function statusText(status: string): string {
  if (status === 'ready') return '已就绪'
  if (status === 'pending') return '等待处理'
  if (status === 'processing') return '处理中'
  if (status === 'failed') return '失败'
  return status
}

function statusIcon(status: string): string {
  if (status === 'ready') return '✓'
  if (status === 'pending' || status === 'processing') return '…'
  if (status === 'failed') return '!'
  return '?'
}

async function handleFileSelect(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (file) {
    await ragStore.upload(file)
  }
  target.value = ''
}

async function handleDrop(event: DragEvent) {
  isDragging.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) {
    await ragStore.upload(file)
  }
}

function triggerFileInput() {
  if (!ragStore.isUploading) {
    fileInput.value?.click()
  }
}

async function handleSearch() {
  await ragStore.search(searchQuery.value)
}

function handleSearchKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter') {
    void handleSearch()
  }
}

function clearSearch() {
  searchQuery.value = ''
  ragStore.clearSearch()
}

async function handleDelete(documentId: number) {
  await ragStore.removeDocument(documentId)
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
  await ragStore.loadDocuments()
})
</script>

<template>
  <div class="knowledge-page">
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
        <button class="nav-item active" type="button" title="知识库">
          <span class="nav-icon">库</span>
          <span class="nav-label">知识库</span>
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

    <main class="knowledge-main">
      <header class="page-header">
        <div>
          <h1>知识库管理</h1>
          <p>上传 Markdown 或文本文件，解析并写入 pgvector 后会用于聊天检索增强。</p>
        </div>
        <div class="status-pill">
          <span class="status-dot"></span>
          RAG 已启用
        </div>
      </header>

      <div v-if="ragStore.notice.message" :class="['notice', ragStore.notice.type]">
        {{ ragStore.notice.message }}
      </div>

      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-icon ready">文</div>
          <div class="stat-info">
            <span class="stat-label">文档总数</span>
            <strong>{{ stats.total }}</strong>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon chunks">块</div>
          <div class="stat-info">
            <span class="stat-label">切片总数</span>
            <strong>{{ stats.totalChunks }}</strong>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon ready">✓</div>
          <div class="stat-info">
            <span class="stat-label">已就绪</span>
            <strong>{{ stats.ready }}</strong>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon pending">…</div>
          <div class="stat-info">
            <span class="stat-label">处理中</span>
            <strong>{{ stats.pending }}</strong>
          </div>
        </div>
      </div>

      <div
        :class="['upload-zone', { dragging: isDragging, uploading: ragStore.isUploading }]"
        @click="triggerFileInput"
        @dragover.prevent="isDragging = true"
        @dragleave.prevent="isDragging = false"
        @drop.prevent="handleDrop"
      >
        <input
          ref="fileInput"
          type="file"
          :accept="supportedTypes"
          class="file-input-hidden"
          :disabled="ragStore.isUploading"
          @change="handleFileSelect"
        >
        <div class="upload-inner">
          <div class="upload-icon" :class="{ spinning: ragStore.isUploading }">
            {{ ragStore.isUploading ? '…' : '+' }}
          </div>
          <div class="upload-text">
            <h3>{{ ragStore.isUploading ? '正在处理文件...' : '拖拽文件到这里，或点击上传' }}</h3>
            <p v-if="!ragStore.isUploading">支持 .txt / .md / .markdown / .csv / .json / .log</p>
            <p v-else class="upload-progress-text">
              {{ ragStore.uploadProgress?.message || '处理中...' }}
            </p>
          </div>
          <button
            v-if="ragStore.isUploading"
            class="upload-cancel-btn"
            type="button"
            @click.stop="ragStore.cancelUpload()"
          >
            取消
          </button>
        </div>
      </div>

      <div v-if="ragStore.isUploading && ragStore.uploadProgress" class="upload-progress-bar">
        <div class="progress-track">
          <div class="progress-fill" :style="{ width: progressPercent + '%' }"></div>
        </div>
        <span class="progress-stage">{{ stageLabel(ragStore.uploadProgress.stage) }}</span>
      </div>

      <div class="search-section">
        <div class="search-bar">
          <span class="search-icon">搜</span>
          <input
            v-model="searchQuery"
            type="text"
            class="search-input"
            placeholder="输入关键词检索知识库..."
            @keydown="handleSearchKeydown"
          >
          <button v-if="searchQuery" class="search-clear" type="button" @click="clearSearch">×</button>
          <button class="search-btn" type="button" :disabled="ragStore.isSearching" @click="handleSearch">
            {{ ragStore.isSearching ? '检索中...' : '检索' }}
          </button>
        </div>

        <div v-if="ragStore.hasSearched" class="search-results">
          <div class="results-header">
            <h3>检索结果（{{ ragStore.searchResults.length }}）</h3>
            <button class="text-btn" type="button" @click="clearSearch">清除</button>
          </div>
          <div v-if="ragStore.searchResults.length === 0" class="empty-results">
            没有找到相关内容
          </div>
          <div
            v-for="hit in ragStore.searchResults"
            :key="hit.chunk_id"
            class="result-card"
          >
            <div class="result-meta">
              <span class="result-file">{{ hit.filename }}</span>
              <span class="result-score">相似度 {{ (hit.score * 100).toFixed(1) }}%</span>
            </div>
            <p class="result-content">{{ hit.content }}</p>
          </div>
        </div>
      </div>

      <div class="doc-section">
        <div class="section-header">
          <h3>文档列表</h3>
          <button class="text-btn" type="button" :disabled="ragStore.isLoading" @click="ragStore.loadDocuments()">
            {{ ragStore.isLoading ? '刷新中...' : '刷新' }}
          </button>
        </div>

        <div v-if="ragStore.isLoading && ragStore.documents.length === 0" class="doc-loading">
          <div class="loading-spinner"></div>
          <span>加载中...</span>
        </div>

        <div v-else-if="ragStore.documents.length === 0" class="doc-empty">
          <div class="doc-empty-icon">库</div>
          <p>还没有上传文档</p>
          <span>上传文档后，AI 对话会自动检索知识库内容。</span>
        </div>

        <div v-else class="doc-list">
          <div
            v-for="doc in ragStore.documents"
            :key="doc.document_id"
            :class="['doc-card', doc.status]"
          >
            <div class="doc-info">
              <div class="doc-name-row">
                <span :class="['doc-status-badge', doc.status]">
                  {{ statusIcon(doc.status) }}
                </span>
                <span class="doc-name">{{ doc.filename }}</span>
              </div>
              <div class="doc-meta">
                <span>{{ formatFileSize(doc.file_size) }}</span>
                <span v-if="doc.chunk_count > 0">{{ doc.chunk_count }} 个切片</span>
                <span>{{ formatDate(doc.created_at) }}</span>
                <span v-if="doc.embedding_model" class="doc-model">{{ doc.embedding_model }}</span>
              </div>
              <div v-if="doc.error_message" class="doc-error">{{ doc.error_message }}</div>
            </div>
            <div class="doc-actions">
              <span :class="['doc-status-text', doc.status]">{{ statusText(doc.status) }}</span>
              <button
                class="doc-delete-btn"
                type="button"
                title="删除文档"
                @click="handleDelete(doc.document_id)"
              >
                ×
              </button>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>
