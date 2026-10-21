import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, 'API_')

  return {
    plugins: [react()],
    resolve: { alias: { '@': path.resolve(__dirname, './src') } },
    server: {
      port: 5173,
      // Prepare the small login entry during startup instead of on its first visit.
      warmup: {
        clientFiles: ['./index.html', './src/main.tsx', './src/screens/auth/login.tsx'],
      },
      proxy: {
        '/api': {
          target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
