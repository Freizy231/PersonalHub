import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 开发服务器只绑定回环地址（AGENT.md §4.2：不得默认监听 0.0.0.0）。
const BACKEND_ORIGIN = 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // 开发期把 /api 代理到后端，浏览器视为同源，减少 CORS 与预检的干扰。
    // 生产形态下前端由后端同源提供，同样不需要跨源。
    proxy: {
      '/api': {
        target: BACKEND_ORIGIN,
        changeOrigin: false,
      },
    },
  },
})
