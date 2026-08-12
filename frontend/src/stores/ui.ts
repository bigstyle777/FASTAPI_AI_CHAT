import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUiStore = defineStore('ui', () => {
  const sidebarCollapsed = ref(localStorage.getItem('sidebar-collapsed') === '1')

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
    localStorage.setItem('sidebar-collapsed', sidebarCollapsed.value ? '1' : '0')
  }

  return { sidebarCollapsed, toggleSidebar }
})
