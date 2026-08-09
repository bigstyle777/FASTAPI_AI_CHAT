import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

const THEME_STORAGE_KEY = 'aichatpro-theme'
const VALID_THEMES = ['light', 'dark', 'system'] as const
type Theme = (typeof VALID_THEMES)[number]

export const useThemeStore = defineStore('theme', () => {
  const theme = ref<Theme>(getStoredTheme())
  const systemDark = ref(window.matchMedia('(prefers-color-scheme: dark)').matches)

  const resolved = computed<'light' | 'dark'>(() => {
    if (theme.value === 'system') {
      return systemDark.value ? 'dark' : 'light'
    }
    return theme.value
  })

  function getStoredTheme(): Theme {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)
    return VALID_THEMES.includes(stored as Theme) ? (stored as Theme) : 'system'
  }

  function applyTheme() {
    document.documentElement.setAttribute('data-theme', resolved.value)
    const lightLink = document.getElementById('md-theme-light') as HTMLLinkElement | null
    const darkLink = document.getElementById('md-theme-dark') as HTMLLinkElement | null
    if (lightLink) lightLink.disabled = resolved.value === 'dark'
    if (darkLink) darkLink.disabled = resolved.value !== 'dark'
  }

  function setTheme(newTheme: string) {
    const next = VALID_THEMES.includes(newTheme as Theme) ? (newTheme as Theme) : 'system'
    theme.value = next
    localStorage.setItem(THEME_STORAGE_KEY, next)
    applyTheme()
  }

  function initTheme() {
    applyTheme()
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
      systemDark.value = e.matches
      if (theme.value === 'system') {
        applyTheme()
      }
    })
  }

  return {
    theme,
    resolved,
    setTheme,
    initTheme,
  }
})
