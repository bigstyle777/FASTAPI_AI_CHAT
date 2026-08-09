<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import type { CaptchaResponse } from '@/types'

const router = useRouter()
const authStore = useAuthStore()

const activeTab = ref<'login' | 'register'>('login')
const loginUsername = ref('')
const loginPassword = ref('')
const loginCaptchaCode = ref('')
const registerUsername = ref('')
const registerPassword = ref('')
const captchaData = ref<CaptchaResponse | null>(null)
const isLoading = ref(false)
const notice = ref<{ message: string; type: 'info' | 'success' | 'error' }>({ message: '', type: 'info' })

async function loadCaptcha() {
  captchaData.value = await authStore.loadCaptcha()
  loginCaptchaCode.value = ''
}

async function handleLogin() {
  if (!loginUsername.value || !loginPassword.value || !loginCaptchaCode.value || !captchaData.value) {
    notice.value = { message: '请输入用户名、密码和验证码', type: 'error' }
    return
  }

  isLoading.value = true
  try {
    const success = await authStore.login(
      loginUsername.value,
      loginPassword.value,
      captchaData.value.captcha_id,
      loginCaptchaCode.value,
    )
    if (success) {
      loginUsername.value = ''
      loginPassword.value = ''
      loginCaptchaCode.value = ''
      await authStore.loadProfile()
      router.push('/chat')
    } else {
      notice.value = { message: '登录失败，请检查用户名、密码和验证码', type: 'error' }
      await loadCaptcha()
    }
  } catch {
    notice.value = { message: '登录失败，请重试', type: 'error' }
    await loadCaptcha()
  } finally {
    isLoading.value = false
  }
}

async function handleRegister() {
  if (!registerUsername.value || !registerPassword.value) {
    notice.value = { message: '请输入用户名和密码', type: 'error' }
    return
  }
  if (registerPassword.value.length < 6) {
    notice.value = { message: '密码至少 6 位', type: 'error' }
    return
  }

  isLoading.value = true
  try {
    const data = await authStore.register(registerUsername.value, registerPassword.value)
    if (data?.success) {
      notice.value = { message: '注册成功，请登录', type: 'success' }
      registerUsername.value = ''
      registerPassword.value = ''
      activeTab.value = 'login'
    } else {
      notice.value = { message: data?.message || '注册失败', type: 'error' }
    }
  } catch {
    notice.value = { message: '注册失败，请重试', type: 'error' }
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  if (authStore.isLoggedIn) {
    router.push('/chat')
  } else {
    loadCaptcha()
  }
})
</script>

<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand-mark">AI</div>
      <h1>AI Chat Pro</h1>
      <p class="login-subtitle">登录后继续你的对话工作台</p>

      <div v-if="notice.message" :class="['notice', notice.type]" style="margin-bottom: 16px; padding: 10px 14px;">
        {{ notice.message }}
      </div>

      <div class="tabs">
        <button
          :class="['tab', { active: activeTab === 'login' }]"
          type="button"
          @click="activeTab = 'login'"
        >
          登录
        </button>
        <button
          :class="['tab', { active: activeTab === 'register' }]"
          type="button"
          @click="activeTab = 'register'"
        >
          注册
        </button>
      </div>

      <form v-if="activeTab === 'login'" @submit.prevent="handleLogin">
        <div class="form-group">
          <label for="loginUsername">用户名</label>
          <input id="loginUsername" v-model="loginUsername" type="text" placeholder="请输入用户名" autocomplete="username">
        </div>
        <div class="form-group">
          <label for="loginPassword">密码</label>
          <input id="loginPassword" v-model="loginPassword" type="password" placeholder="请输入密码" autocomplete="current-password">
        </div>
        <div class="form-group">
          <label for="loginCaptchaCode">验证码</label>
          <div class="captcha-row">
            <input id="loginCaptchaCode" v-model="loginCaptchaCode" type="text" placeholder="请输入验证码" autocomplete="off" maxlength="10">
            <button class="captcha-button" type="button" aria-label="刷新验证码" @click="loadCaptcha">
              <img v-if="captchaData?.image" :src="captchaData.image" alt="验证码">
            </button>
          </div>
        </div>
        <button class="primary-action" type="submit" :disabled="isLoading">登录</button>
      </form>

      <form v-else @submit.prevent="handleRegister">
        <div class="form-group">
          <label for="registerUsername">用户名</label>
          <input id="registerUsername" v-model="registerUsername" type="text" placeholder="请输入用户名" autocomplete="username">
        </div>
        <div class="form-group">
          <label for="registerPassword">密码</label>
          <input id="registerPassword" v-model="registerPassword" type="password" placeholder="请输入密码，至少 6 位" autocomplete="new-password">
        </div>
        <button class="primary-action" type="submit" :disabled="isLoading">注册</button>
      </form>
    </div>
  </div>
</template>
