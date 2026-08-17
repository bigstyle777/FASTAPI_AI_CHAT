/**
 * 全局 Toast 通知 composable
 * - 统一 MemoryView / ProfileView 等页面的内联 Toast 实现
 * - 自动消失，支持 success / error 两种类型
 */
import { ref } from 'vue'

export interface ToastItem {
  id: number
  message: string
  type: 'success' | 'error'
}

let toastSeq = 0

export function useToast() {
  const toasts = ref<ToastItem[]>([])

  function showToast(message: string, type: 'success' | 'error' = 'success') {
    const id = ++toastSeq
    toasts.value.push({ id, message, type })
    window.setTimeout(() => {
      toasts.value = toasts.value.filter((t) => t.id !== id)
    }, 3000)
  }

  return { toasts, showToast }
}