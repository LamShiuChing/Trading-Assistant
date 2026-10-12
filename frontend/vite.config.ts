import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev only: proxy to the backend. In production the backend serves dist/ itself.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/ws': { target: 'ws://127.0.0.1:8765', ws: true },
    },
  },
})
