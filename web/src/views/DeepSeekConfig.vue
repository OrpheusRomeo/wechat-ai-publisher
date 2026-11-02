<template>
  <div class="redirect-hint">
    <el-result icon="info" title="DeepSeek 配置已迁移到「LLM 模型配置」">
      <template #sub-title>
        <p>
          API Key / 模型 / 温度等参数现在统一在「配置 → LLM 模型配置」里维护，
          支持 DeepSeek / OpenAI / MiniMax / Qwen 等多家供应商，并可指定默认供应商。
        </p>
        <p>这里保留本页面只是为了不打断旧书签。</p>
      </template>
      <template #extra>
        <el-button type="primary" @click="goToLLMProviders">前往 LLM 模型配置</el-button>
        <el-button @click="goToWechatConfig">返回微信配置</el-button>
      </template>
    </el-result>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

function goToLLMProviders() {
  router.push('/config/llm-providers')
}

function goToWechatConfig() {
  router.push('/config/wechat')
}
</script>

<style scoped>
.redirect-hint {
  padding: 24px;
}
</style>
