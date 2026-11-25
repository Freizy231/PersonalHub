import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// 开发服务器只绑定回环地址（AGENT.md §4.2：不得默认监听 0.0.0.0）。
// 后端地址可用 PH_BACKEND_ORIGIN 覆盖，便于在备用端口上做端到端验证。
const BACKEND_ORIGIN = process.env.PH_BACKEND_ORIGIN ?? 'http://127.0.0.1:8000'

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
  test: {
    // 组件测试需要 DOM。用 jsdom 而不是 happy-dom：前者对表单/焦点语义更完整，
    // 而本项目关心的是"用户能否看懂并操作"，而不是渲染速度。
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
