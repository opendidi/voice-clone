import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  css: {
    // Vite 内置 SCSS 支持（装 sass 即可，不需要额外插件）
    // 变量与 mixin 由各组件显式 @use，路径相对组件文件解析，避免别名/importer 差异
    preprocessorOptions: {
      scss: { quietDeps: true, charset: false },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/v1': { target: 'http://127.0.0.1:5002', changeOrigin: true },
    },
  },
})
