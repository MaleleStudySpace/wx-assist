import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 15173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:17327', changeOrigin: true },
      '/ws': { target: 'http://127.0.0.1:17327', ws: true },
    },
  },
  build: { outDir: 'dist' },
})
