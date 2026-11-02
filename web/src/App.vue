<template>
  <router-view />
  <!--
    右下角构建元信息: 一眼看出当前看到的是哪个版本.
    调试"我刷新了看不到新功能"类问题时秒排掉浏览器缓存.
    点击展开查看 git commit / branch / commit message.
  -->
  <div class="build-tag" @click="expanded = !expanded" :title="`构建于 ${buildTime}`">
    <span>build {{ buildTime }}</span>
    <div v-if="expanded" class="build-detail" @click.stop>
      <div><b>commit:</b> {{ gitCommit }}</div>
      <div><b>branch:</b> {{ gitBranch }}</div>
      <div v-if="gitMsg"><b>msg:</b> {{ gitMsg }}</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

// vite.config.ts 里 define 注入的全局常量
declare const __BUILD_TIMESTAMP__: string
declare const __GIT_COMMIT__: string
declare const __GIT_BRANCH__: string
declare const __GIT_MSG__: string

const buildTime = (__BUILD_TIMESTAMP__ || new Date().toISOString()).slice(0, 19).replace('T', ' ')
const gitCommit = __GIT_COMMIT__ || 'unknown'
const gitBranch = __GIT_BRANCH__ || 'unknown'
const gitMsg = (__GIT_MSG__ || '').slice(0, 80)  // 截断长 commit message
const expanded = ref(false)
</script>

<style scoped>
.build-tag {
  position: fixed;
  bottom: 6px;
  right: 12px;
  font-size: 11px;
  color: #aaa;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  pointer-events: auto;
  user-select: none;
  z-index: 9999;
  cursor: pointer;
}
.build-tag:hover {
  color: #666;
}
.build-detail {
  position: absolute;
  bottom: calc(100% + 4px);
  right: 0;
  min-width: 320px;
  max-width: 480px;
  padding: 8px 12px;
  background: #fff;
  border: 1px solid #ddd;
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  color: #333;
  font-size: 11px;
  line-height: 1.6;
  text-align: left;
  white-space: normal;
}
.build-detail > div {
  word-break: break-all;
}
</style>
