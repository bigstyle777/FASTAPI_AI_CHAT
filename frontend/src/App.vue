<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useThemeStore } from '@/stores/theme'
import { useAuthStore } from '@/stores/auth'
import { useRouter } from 'vue-router'
import { Toaster } from '@/components/ui/sonner'

const themeStore = useThemeStore()
const authStore = useAuthStore()
const router = useRouter()

onMounted(() => {
  themeStore.initTheme()

  // 监听 401 未授权事件
  window.addEventListener('auth:unauthorized', () => {
    authStore.clearAuth()
    router.push('/login')
  })

  // 如果已登录，加载用户信息
  if (authStore.isLoggedIn) {
    authStore.loadProfile()
  }
})

// 当用户状态变化时，如果未登录则跳转到登录页
watch(
  () => authStore.isLoggedIn,
  (loggedIn) => {
    if (!loggedIn && router.currentRoute.value.meta.requiresAuth) {
      router.push('/login')
    }
  },
)
</script>

<template>
  <RouterView />
  <!-- 全局 Toast 提示（成功 / 报错），页面通过 vue-sonner 的 toast() 调用 -->
  <Toaster />
</template>
