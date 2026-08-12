<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const apiKey = ref('')
const provider = ref('deepseek')
const embeddingApiKey = ref('')
const embeddingBaseUrl = ref('')
const embeddingModel = ref('')
const showApiKey = ref(false)
const showEmbeddingKey = ref(false)
const isLoading = ref(false)
const notice = ref<{ message: string; type: 'info' | 'success' | 'error' }>({
  message: '',
  type: 'info',
})

async function handleSave() {
  isLoading.value = true
  try {
    const success = await authStore.saveSettings(
      apiKey.value.trim(),
      provider.value,
      embeddingApiKey.value.trim(),
      embeddingBaseUrl.value.trim(),
      embeddingModel.value.trim(),
    )
    notice.value = success
      ? { message: '设置已保存', type: 'success' }
      : { message: '保存设置失败', type: 'error' }
  } catch {
    notice.value = { message: '保存设置失败', type: 'error' }
  } finally {
    isLoading.value = false
  }
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
  embeddingApiKey.value = authStore.settings.embedding_api_key ?? ''
  embeddingBaseUrl.value = authStore.settings.embedding_base_url ?? ''
  embeddingModel.value = authStore.settings.embedding_model ?? ''
})
</script>

<template>
  <div class="profile-page">
    <div class="profile-container">
      <button class="back-btn" type="button" @click="router.push('/chat')">返回</button>
      <h1>个人中心</h1>

      <div v-if="notice.message" :class="['notice', notice.type]" style="margin: 16px 0; padding: 10px 14px;">
        {{ notice.message }}
      </div>

      <section class="profile-section">
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

      <section class="profile-section">
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
      </section>

      <section class="profile-section">
        <h2>知识库 Embedding 设置</h2>
        <p class="section-hint">
          用于 RAG 文档向量化与检索。支持 SiliconFlow、sbgpt 或自建的 OpenAI 兼容 embedding 接口。
        </p>
        <div class="form-group api-key-row">
          <label for="embeddingKeyInput">Embedding API Key</label>
          <div class="inline-field">
            <input
              id="embeddingKeyInput"
              v-model="embeddingApiKey"
              :type="showEmbeddingKey ? 'text' : 'password'"
              placeholder="请输入 Embedding API Key"
              autocomplete="off"
            >
            <button class="toggle-btn" type="button" @click="showEmbeddingKey = !showEmbeddingKey">
              {{ showEmbeddingKey ? '隐藏' : '显示' }}
            </button>
          </div>
        </div>
        <div class="form-group">
          <label for="embeddingBaseUrlInput">Embedding Base URL</label>
          <input
            id="embeddingBaseUrlInput"
            v-model="embeddingBaseUrl"
            type="text"
            placeholder="例如 https://api.siliconflow.cn/v1"
            autocomplete="off"
          >
        </div>
        <div class="form-group">
          <label for="embeddingModelInput">Embedding Model</label>
          <input
            id="embeddingModelInput"
            v-model="embeddingModel"
            type="text"
            placeholder="例如 BAAI/bge-large-zh-v1.5"
            autocomplete="off"
          >
        </div>
      </section>

      <button class="primary-action" type="button" :disabled="isLoading" @click="handleSave">
        {{ isLoading ? '保存中...' : '保存设置' }}
      </button>
    </div>
  </div>
</template>
