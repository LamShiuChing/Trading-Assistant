import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev only: proxy to the backend. In production the backend serves dist/ itself.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // changeOrigin rewrites Host to the backend's, which passes its Host check.
      '/api': { target: 'http://127.0.0.1:8765', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8765', ws: true, changeOrigin: true },
    },
  },
})
