import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// In dev, Vite serves the SPA on :5173 and proxies API/WS/media to FastAPI on :8000.
// In production FastAPI serves the built dist/ directly.
const backend = process.env.VTT_BACKEND ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': backend,
      '/media': backend,
      '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
    },
  },
  build: {
    target: 'es2022',
    sourcemap: false,
  },
})
