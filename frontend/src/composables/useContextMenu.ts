import { ref, onMounted, onUnmounted } from 'vue'

export interface MenuPosition {
  top: number
  left: number
}

export interface MenuItem {
  label: string
  danger?: boolean
  action: () => void
}

export function useContextMenu() {
  const visible = ref(false)
  const position = ref<MenuPosition>({ top: 0, left: 0 })
  const items = ref<MenuItem[]>([])

  function open(triggerEl: HTMLElement, menuItems: MenuItem[], alignRight = true) {
    items.value = menuItems
    visible.value = true

    // 使用 nextTick 等待 DOM 渲染后计算位置
    requestAnimationFrame(() => {
      const rect = triggerEl.getBoundingClientRect()
      let left = alignRight ? rect.right - 148 : rect.left
      let top = rect.bottom + 4

      // 确保不超出屏幕
      if (left < 8) left = 8
      if (top < 8) top = 8

      position.value = { top, left }
    })
  }

  function close() {
    visible.value = false
    items.value = []
  }

  function onDocumentClick(event: MouseEvent) {
    const target = event.target as HTMLElement
    if (visible.value && !target.closest('.context-menu')) {
      close()
    }
  }

  onMounted(() => {
    document.addEventListener('click', onDocumentClick)
  })

  onUnmounted(() => {
    document.removeEventListener('click', onDocumentClick)
  })

  return {
    visible,
    position,
    items,
    open,
    close,
  }
}
