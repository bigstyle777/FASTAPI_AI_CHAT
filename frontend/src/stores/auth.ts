import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { UserProfile } from '@/types'
import {
  fetchCaptcha,
  fetchUserProfile,
  fetchUserSettings,
  loginUser,
  logoutUser,
  registerUser,
  saveUserSettings,
} from '@/api/auth'
import { removeToken, getToken, setToken } from '@/api/client'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserProfile | null>(null)
  const settings = ref({
    api_key: '',
    provider: 'deepseek',
    embedding_api_key: null as string | null,
    embedding_base_url: null as string | null,
    embedding_model: null as string | null,
  })

  const isLoggedIn = computed(() => !!getToken())
  const isAdmin = computed(() => user.value?.role === 'admin')

  async function loadCaptcha() {
    return fetchCaptcha()
  }

  async function login(username: string, password: string, captchaId: string, captchaCode: string) {
    const data = await loginUser(username, password, captchaId, captchaCode)
    if (data?.success && data.access_token) {
      setToken(data.access_token)
      return true
    }
    return false
  }

  async function register(username: string, password: string) {
    return registerUser(username, password)
  }

  async function logout() {
    if (getToken()) {
      await logoutUser()
    }
    removeToken()
    user.value = null
    settings.value = {
      api_key: '',
      provider: 'deepseek',
      embedding_api_key: null,
      embedding_base_url: null,
      embedding_model: null,
    }
  }

  async function loadProfile() {
    const data = await fetchUserProfile()
    if (data?.success) {
      user.value = {
        user_id: data.user_id,
        username: data.username,
        role: data.role || 'user',
        permissions: Array.isArray(data.permissions) ? data.permissions : [],
      }
    }
    return data
  }

  async function loadSettings() {
    const data = await fetchUserSettings()
    if (data) {
      settings.value = {
        api_key: data.api_key || '',
        provider: data.provider || 'deepseek',
        embedding_api_key: data.embedding_api_key ?? null,
        embedding_base_url: data.embedding_base_url ?? null,
        embedding_model: data.embedding_model ?? null,
      }
    }
  }

  async function saveSettings(
    apiKey: string,
    provider: string,
    embeddingApiKey = '',
    embeddingBaseUrl = '',
    embeddingModel = '',
  ) {
    const data = await saveUserSettings(
      apiKey,
      provider,
      embeddingApiKey,
      embeddingBaseUrl,
      embeddingModel,
    )
    if (data) {
      settings.value = {
        api_key: data.api_key || '',
        provider: data.provider || 'deepseek',
        embedding_api_key: data.embedding_api_key ?? null,
        embedding_base_url: data.embedding_base_url ?? null,
        embedding_model: data.embedding_model ?? null,
      }
      return true
    }
    return false
  }

  function clearAuth() {
    removeToken()
    user.value = null
    settings.value = {
      api_key: '',
      provider: 'deepseek',
      embedding_api_key: null,
      embedding_base_url: null,
      embedding_model: null,
    }
  }

  return {
    user,
    settings,
    isLoggedIn,
    isAdmin,
    loadCaptcha,
    login,
    register,
    logout,
    loadProfile,
    loadSettings,
    saveSettings,
    clearAuth,
  }
})
