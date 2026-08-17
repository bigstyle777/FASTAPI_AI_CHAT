<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted, nextTick, computed, reactive } from 'vue'
import type { ChatMessage } from '@/types'

const props = defineProps<{
  messages: ChatMessage[]
  containerRef: HTMLElement | null
}>()

const minimapEl = ref<HTMLElement | null>(null)
const activeId = ref<number | null>(null)
const mouseY = ref<number | null>(null)
const hoveredId = ref<number | null>(null)
const tooltipMsg = ref('')
const tooltipY = ref(0)
let observer: IntersectionObserver | null = null

// 存储每个消息条的缩放值（用实际 DOM 位置计算）
const barScales = reactive<Record<number, { width: number; height: number; opacity: number }>>({})

function summary(msg: ChatMessage): string {
  const text = msg.content.replace(/\s+/g, ' ').trim()
  return text.length > 14 ? text.slice(0, 14) + '…' : text
}

function getMessageEl(id: number): HTMLElement | null {
  if (!props.containerRef) return null
  return props.containerRef.querySelector(`[data-message-id="${id}"]`)
}

function scrollToMessage(id: number) {
  const el = getMessageEl(id)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    activeId.value = id
  }
}

// 用实际 DOM 位置计算所有横条的缩放值
function recalcScales(myY: number) {
  if (!minimapEl.value) return
  const rect = minimapEl.value.getBoundingClientRect()
  const track = minimapEl.value.querySelector('.minimap-track')
  if (!track) return

  const children = track.children
  const newScales: Record<number, { width: number; height: number; opacity: number }> = {}
  let bestId: number | null = null
  let bestDist = Infinity

  for (let i = 0; i < children.length; i++) {
    const bar = children[i] as HTMLElement
    const id = Number(bar.dataset.barId)
    if (Number.isNaN(id)) continue

    const barRect = bar.getBoundingClientRect()
    const barCenter = barRect.top - rect.top + barRect.height / 2
    const dist = Math.abs(myY - barCenter)
    const maxDist = 120
    const t = Math.max(0, 1 - dist / maxDist)
    const ease = t * t * (3 - 2 * t)

    newScales[id] = {
      width: 1.0 + ease * 0.8,
      height: 1.0 + ease * 2.0,
      opacity: 0.25 + ease * 0.75,
    }

    if (dist < bestDist) {
      bestDist = dist
      bestId = id
    }
  }

  // 批量更新
  Object.assign(barScales, newScales)

  // 清理不在当前消息中的旧 key
  const currentIds = new Set(props.messages.map((m) => m.message_id))
  for (const key of Object.keys(barScales)) {
    if (!currentIds.has(Number(key))) {
      delete barScales[Number(key)]
    }
  }

  // 更新 hover 状态
  if (bestId !== null && bestId !== hoveredId.value) {
    hoveredId.value = bestId
    const msg = props.messages.find((m) => m.message_id === bestId)
    if (msg) {
      tooltipMsg.value = summary(msg)
      tooltipY.value = myY
    }
  }
}

function handleMouseMove(event: MouseEvent) {
  if (!minimapEl.value) return
  const rect = minimapEl.value.getBoundingClientRect()
  const y = event.clientY - rect.top
  mouseY.value = y
  recalcScales(y)
}

function handleMouseLeave() {
  mouseY.value = null
  hoveredId.value = null
  // 重置所有缩放
  for (const key of Object.keys(barScales)) {
    delete barScales[Number(key)]
  }
}

function getScale(msgId: number) {
  return barScales[msgId] ?? { width: 1.0, height: 1.0, opacity: 0.25 }
}

const hoveredMsg = computed(() => {
  if (hoveredId.value === null) return null
  return props.messages.find((m) => m.message_id === hoveredId.value) ?? null
})

function setupObserver() {
  if (!props.containerRef) return
  observer?.disconnect()

  const visibilityMap = new Map<number, number>()

  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const el = entry.target as HTMLElement
        const id = Number(el.dataset.messageId)
        if (!Number.isNaN(id)) {
          visibilityMap.set(id, entry.intersectionRatio)
        }
      }
      let bestId: number | null = null
      let bestRatio = 0
      for (const [id, ratio] of visibilityMap) {
        if (ratio > bestRatio) {
          bestRatio = ratio
          bestId = id
        }
      }
      activeId.value = bestId
    },
    {
      root: props.containerRef,
      threshold: [0, 0.1, 0.25, 0.5, 0.75, 1],
    },
  )

  nextTick(() => {
    const groups = props.containerRef!.querySelectorAll('[data-message-id]')
    for (const el of groups) {
      observer!.observe(el)
    }
  })
}

watch(
  () => props.messages.length,
  () => { nextTick(() => setupObserver()) },
)

watch(
  () => props.containerRef,
  (container) => {
    if (container) { nextTick(() => setupObserver()) }
  },
)

onMounted(() => {
  if (props.containerRef) { nextTick(() => setupObserver()) }
})

onUnmounted(() => {
  observer?.disconnect()
})
</script>

<template>
  <div
    v-if="messages.length > 0"
    ref="minimapEl"
    class="chat-minimap"
    @mousemove="handleMouseMove"
    @mouseleave="handleMouseLeave"
  >
    <div class="minimap-track">
      <div
        v-for="msg in messages"
        :key="msg.message_id"
        :data-bar-id="msg.message_id"
        :class="[
          'minimap-bar',
          msg.role,
          { active: activeId === msg.message_id },
        ]"
        :style="{
          '--scale-w': getScale(msg.message_id).width,
          '--scale-h': getScale(msg.message_id).height,
          '--opacity': getScale(msg.message_id).opacity,
        }"
        @click="scrollToMessage(msg.message_id)"
      />
    </div>

    <Transition name="minimap-bubble">
      <div
        v-if="hoveredId !== null && hoveredMsg"
        class="minimap-bubble"
        :style="{ top: tooltipY + 'px' }"
      >
        <span class="minimap-bubble-role">{{ hoveredMsg.role === 'user' ? 'You' : 'AI' }}</span>
        <span class="minimap-bubble-text">{{ tooltipMsg }}</span>
      </div>
    </Transition>
  </div>
</template>