<script setup lang="ts">
import { Toaster as Sonner } from 'vue-sonner'
import { useThemeStore } from '@/stores/theme'

/** 全局 Toast 容器（vue-sonner），挂载一次于 App.vue */
const themeStore = useThemeStore()
</script>

<template>
  <Sonner
    class="toaster group"
    :theme="themeStore.resolved"
    position="top-center"
    rich-colors
    :toast-options="{
      classes: {
        toast:
          'group toast group-[.toaster]:bg-background group-[.toaster]:text-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg group-[.toaster]:rounded-xl',
        description: 'group-[.toast]:text-muted-foreground',
        actionButton:
          'group-[.toast]:bg-primary group-[.toast]:text-primary-foreground',
        cancelButton:
          'group-[.toast]:bg-muted group-[.toast]:text-muted-foreground',
      },
    }"
  />
</template>
