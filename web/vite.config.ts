import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// 注入构建元信息到产物. 前端 App.vue 右下角小字显示构建时间,
// 点击展开看 git commit / branch / commit message, 一眼区分
// "我看到的是不是最新版本", 把浏览器缓存问题秒排掉.
// Dockerfile.web 通过 ARG BUILD_TIMESTAMP / GIT_* 传入,
// 缺省时 Dockerfile 自己兜底生成 UTC 时间, git 信息为 unknown.
const BUILD_TIMESTAMP = process.env.BUILD_TIMESTAMP || new Date().toISOString()
const GIT_COMMIT = process.env.GIT_COMMIT || 'unknown'
const GIT_BRANCH = process.env.GIT_BRANCH || 'unknown'
const GIT_MSG = process.env.GIT_MSG || ''

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  define: {
    // 用 define 而不是 import.meta.env, 这些不是用户配置的 VITE_* 变量,
    // 避免污染 env 类型定义
    __BUILD_TIMESTAMP__: JSON.stringify(BUILD_TIMESTAMP),
    __GIT_COMMIT__: JSON.stringify(GIT_COMMIT),
    __GIT_BRANCH__: JSON.stringify(GIT_BRANCH),
    __GIT_MSG__: JSON.stringify(GIT_MSG),
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_URL || 'http://localhost:8888',
        changeOrigin: true,
      },
      '/webhook': {
        target: process.env.VITE_API_URL || 'http://localhost:8888',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    // 分割 chunks 减少单文件体积
    rollupOptions: {
      output: {
        manualChunks: {
          // Element Plus 及其依赖单独打包
          'element-plus': ['element-plus', '@element-plus/icons-vue'],
          // Vue 核心框架单独打包
          'vue-vendor': ['vue', 'vue-router', 'pinia'],
          // 其他 vendor 库
          'vendor': ['axios', 'dayjs'],
        },
      },
    },
    // 增大警告阈值
    chunkSizeWarningLimit: 600,
  },
})
