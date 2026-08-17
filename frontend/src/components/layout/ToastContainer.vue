<script setup lang="ts">
import type { ToastItem } from '@/composables/useToast'

defineProps<{
  toasts: ToastItem[]
}>()
</script>

<template>
  <div v-if="toasts.length" class="toast-stack">
    <div
      v-for="toast in toasts"
      :key="toast.id"
      :class="['toast-item', toast.type]"
    >
      <span class="toast-icon">{{ toast.type === 'success' ? '✓' : '✕' }}</span>
      {{ toast.message }}
    </div>
  </div>
</template>