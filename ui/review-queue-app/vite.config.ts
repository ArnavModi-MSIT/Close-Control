import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Builds to dist/, which review_backend/main.py mounts at /review-queue --
// same single-local-process story as the rest of this project (no CORS to
// configure once built). The dev-time proxy below only matters when running
// `npm run dev` directly against Vite's own server; it forwards /api calls
// to the FastAPI backend so fetch("/api/...") works identically in dev and
// in the built app without any code branching.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/review-queue/',
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
  },
})
