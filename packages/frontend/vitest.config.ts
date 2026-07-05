/// <reference types="vitest" />
/**
 * Vitest 配置 — 独立文件，不污染 vite.config.ts。
 *
 * 解决 vitest 嵌套 vite 版本与项目 vite 6 不匹配的类型冲突：
 * 不引用 vue plugin 的具体类型，用 unknown 规避。
 */
import { defineConfig } from 'vitest/config';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  plugins: [vue() as unknown as any],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['tests/**/*.spec.ts'],
  },
});
