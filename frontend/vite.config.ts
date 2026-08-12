import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// SPA 回退：仅对前端路由返回 index.html
// 避免 Vite 代理将这些路由转发到后端
function spaFallbackPlugin() {
  const SPA_ROUTES = new Set(['/', '/chat', '/login', '/profile', '/admin', '/knowledge'])
  return {
    name: 'spa-fallback',
    configureServer(server: any) {
      server.middlewares.use((req: any, _res: any, next: any) => {
        const url: string = req.url || ''
        if (SPA_ROUTES.has(url) && req.method === 'GET') {
          req.url = '/index.html'
        }
        next()
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
    spaFallbackPlugin(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/users': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/chat': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/admin': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/rag': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
