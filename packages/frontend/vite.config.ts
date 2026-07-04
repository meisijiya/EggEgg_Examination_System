/// <reference types="vitest" />
/**
 * Vite 构建配置。
 *
 * - 开发代理：将 /api → http://localhost:8000
 * - 生产构建：产物输出到 dist/
 */
import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          'echarts-vendor': ['echarts'],
          'element-plus-vendor': ['element-plus', '@element-plus/icons-vue'],
        },
      },
    },
  },
});
