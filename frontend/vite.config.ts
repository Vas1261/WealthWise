import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The dev server proxies API calls to FastAPI so the browser sees a single origin
// (keeps the httpOnly auth cookies same-site, no CORS configuration needed).
const BACKEND_URL = process.env.BACKEND_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': BACKEND_URL,
      '/health': BACKEND_URL,
    },
  },
})
