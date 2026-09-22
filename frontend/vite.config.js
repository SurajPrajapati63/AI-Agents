import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiUrl = env.API_URL || (
    mode === 'development'
      ? 'http://localhost:8000'
      : 'https://ai-agents-qigt.onrender.com'
  )

  return {
    plugins: [react()],
    define: {
      'import.meta.env.API_URL': JSON.stringify(apiUrl),
    },
    build: {
      sourcemap: false,
    },
  }
})
