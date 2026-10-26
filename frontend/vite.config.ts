import path from 'path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  // The FastAPI backend (backend/) runs on :8000 in dev.
  server: { proxy: { '/api': 'http://localhost:8000' } },
})
