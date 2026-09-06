import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 产物输出到 dist，由本地后端同源提供；base 用相对路径以便 file/打包均可用。
export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
  server: { host: '127.0.0.1', port: 5173 },
})
