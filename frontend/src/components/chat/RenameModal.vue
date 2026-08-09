<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'

const props = defineProps<{
  visible: boolean
  currentTitle: string
}>()

const emit = defineEmits<{
  close: []
  save: [title: string]
}>()

const inputValue = ref(props.currentTitle)
const inputRef = ref<HTMLInputElement | null>(null)
const isSaving = ref(false)

watch(
  () => props.visible,
  async (visible) => {
    if (visible) {
      inputValue.value = props.currentTitle || '新会话'
      await nextTick()
      inputRef.value?.focus()
      inputRef.value?.select()
    }
  },
)

function handleSave() {
  const newTitle = inputValue.value.trim()
  if (!newTitle) return
  if (newTitle === props.currentTitle) {
    emit('close')
    return
  }
  isSaving.value = true
  emit('save', newTitle)
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter') {
    event.preventDefault()
    handleSave()
  } else if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
  }
}

function handleOverlayClick(event: MouseEvent) {
  if (event.target === event.currentTarget) {
    emit('close')
  }
}

defineExpose({
  setSaving: (val: boolean) => {
    isSaving.value = val
  },
})
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="modal-overlay" @click="handleOverlayClick">
      <div class="modal-card">
        <h3>重命名会话</h3>
        <input
          ref="inputRef"
          v-model="inputValue"
          type="text"
          class="modal-input"
          maxlength="100"
          placeholder="请输入会话名称"
          @keydown="handleKeydown"
        >
        <div class="modal-actions">
          <button type="button" class="modal-btn ghost" :disabled="isSaving" @click="emit('close')">取消</button>
          <button type="button" class="modal-btn primary" :disabled="isSaving" @click="handleSave">
            {{ isSaving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
