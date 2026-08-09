<script setup lang="ts">
import { ref } from 'vue'
import type { TokenUsage } from '@/types'

const props = defineProps<{
  usage: TokenUsage | null
}>()

const expanded = ref(false)

function hasUsage(usage: TokenUsage | null): boolean {
  return !!usage && Number(usage.total_tokens || 0) > 0
}
</script>

<template>
  <div v-if="hasUsage(props.usage)" class="token-usage" :class="{ open: expanded }">
    <button
      type="button"
      class="token-usage-summary"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <span>total_tokens: {{ props.usage!.total_tokens }}</span>
      <span class="token-usage-arrow">v</span>
    </button>
    <div v-if="expanded" class="token-usage-detail">
      <div>model: {{ props.usage!.model || '--' }}</div>
      <div>prompt_tokens: {{ props.usage!.prompt_tokens }}</div>
      <div>completion_tokens: {{ props.usage!.completion_tokens }}</div>
      <div>total_tokens: {{ props.usage!.total_tokens }}</div>
    </div>
  </div>
</template>
