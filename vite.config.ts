import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

const apiTarget = process.env.TONGXUE_API_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: { input: 'dev.html' },
  },
  server: {
    proxy: {
      '/api': apiTarget,
      '/mcp': apiTarget,
    },
  },
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    environment: 'jsdom',
    css: true,
    setupFiles: ['./src/test-setup.ts'],
  },
})
