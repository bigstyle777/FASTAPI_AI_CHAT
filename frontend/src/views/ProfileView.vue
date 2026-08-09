<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const apiKey = ref('')
const provider = ref('deepseek')
const showApiKey = ref(false)
const isLoading = ref(false)
const notice = ref<{ message: string; type: 'info' | 'success' | 'error' }>({ message: '', type: 'info' })

async function handleSave() {
  isLoading.value = true
  try {
    const success = await authStore.saveSettings(apiKey.value.trim(), provider.value)
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
        <h2>API 设置</h2>
        <div class="form-group api-key-row">
          <label for="apiKeyInput">API Key</label>
          <div class="inline-field">
            <input
              id="apiKeyInput"
              v-model="apiKey"
              :type="showApiKey ? 'text' : 'password'"
              placeholder="请输入你的 API Key"
            >
            <button class="toggle-btn" type="button" @click="showApiKey = !showApiKey">
              {{ showApiKey ? '隐藏' : '显示' }}
            </button>
          </div>
        </div>
        <div class="form-group">
          <label for="providerSelect">Provider</label>
          <select id="providerSelect" v-model="provider">
            <option value="deepseek">DeepSeek</option>
            <option value="openai">OpenAI</option>
          </select>
        </div>
        <button class="primary-action" type="button" :disabled="isLoading" @click="handleSave">
          {{ isLoading ? '保存中...' : '保存设置' }}
        </button>
      </section>
    </div>
  </div>
</template>
