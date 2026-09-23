import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiUrl = '/api'

  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': {
          target: env.API_URL || 'http://localhost:8000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
    define: {
      'import.meta.env.API_URL': JSON.stringify(apiUrl),
    },
    build: {
      sourcemap: false,
    },
  }
})
