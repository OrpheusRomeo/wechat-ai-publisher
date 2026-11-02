<template>
  <div class="config-page">
    <h2>🔧 系统配置</h2>
    <p class="subtitle">
      修改后立即生效：后台进程约 15 秒内同步；调度时间约 1 分钟内同步到 beat。
      微信凭据留空表示沿用 .env；多账号发布请到「平台账号管理」配置。
    </p>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="微信配置" name="wechat">
        <ConfigForm category="wechat" />
      </el-tab-pane>
      <el-tab-pane label="定时任务" name="scheduler">
        <ConfigForm category="scheduler" />
      </el-tab-pane>
      <el-tab-pane label="系统开关" name="system">
        <ConfigForm category="system" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import ConfigForm from '@/components/ConfigForm.vue'

const activeTab = ref('wechat')
</script>

<style scoped>
.config-page h2 { margin-bottom: 4px; }
.subtitle { color: #999; font-size: 13px; margin-bottom: 16px; max-width: 720px; line-height: 1.6; }
</style>
