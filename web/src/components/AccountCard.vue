<template>
  <el-card class="account-card" :class="{ deleted: deleted }" shadow="hover">
    <template #header>
      <div class="card-header">
        <div class="platform-info">
          <el-tag :type="getPlatformTagType(account.platform_type)" size="small" effect="dark">
            {{ getPlatformName(account.platform_type) }}
          </el-tag>
          <span class="account-name">{{ account.account_name }}</span>
        </div>
        <el-switch
          v-if="!deleted"
          v-model="account.is_active"
          active-text=""
          inactive-text=""
          @change="handleActiveChange"
        />
      </div>
    </template>

    <div class="card-body">
      <div class="info-row">
        <div class="info-item">
          <span class="info-label">凭证</span>
          <span class="info-value credentials">{{ formatCredentials(account.credentials_masked) }}</span>
        </div>
      </div>
      <el-divider style="margin: 12px 0" />
      <div class="schedule-row">
        <div class="schedule-item">
          <el-icon class="schedule-icon"><Clock /></el-icon>
          <div class="schedule-content">
            <span class="schedule-label">爬虫</span>
            <span class="schedule-value">{{ account.crawl_cron || '08:00' }}</span>
          </div>
        </div>
        <div class="schedule-item">
          <el-icon class="schedule-icon"><Bell /></el-icon>
          <div class="schedule-content">
            <span class="schedule-label">推送</span>
            <span class="schedule-value">{{ account.push_cron || '09:00' }}</span>
          </div>
        </div>
      </div>
      <el-divider style="margin: 12px 0" />
      <div class="info-row">
        <div class="info-item">
          <span class="info-label">创建时间</span>
          <span class="info-value">{{ formatDate(account.created_at) }}</span>
        </div>
      </div>
      <div v-if="deleted && account.deleted_at" class="info-row deleted-row">
        <div class="info-item">
          <span class="info-label">删除时间</span>
          <span class="info-value">{{ formatDate(account.deleted_at) }}</span>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="card-footer">
        <template v-if="!deleted">
          <el-button size="small" @click="showScheduleDialog = true">
            <el-icon><Clock /></el-icon>
            调度
          </el-button>
          <el-button size="small" type="primary" @click="$emit('edit', account)">
            <el-icon><Edit /></el-icon>
            编辑
          </el-button>
          <el-button size="small" type="danger" @click="$emit('delete', account)">
            <el-icon><Delete /></el-icon>
            删除
          </el-button>
        </template>
        <template v-else>
          <el-button size="small" type="success" @click="$emit('restore', account)">
            <el-icon><RefreshRight /></el-icon>
            恢复
          </el-button>
          <el-button size="small" type="danger" plain @click="$emit('permanent-delete', account)">
            <el-icon><Delete /></el-icon>
            永久删除
          </el-button>
        </template>
      </div>
    </template>

    <!-- 调度时间对话框 -->
    <el-dialog v-model="showScheduleDialog" title="修改调度时间" width="420px" destroy-on-close>
      <el-form :model="scheduleForm" label-width="80px">
        <el-form-item label="爬虫时间">
          <el-input v-model="scheduleForm.crawl_cron" placeholder="如: 08:00 或 0 8 * * *" />
        </el-form-item>
        <el-form-item label="推送时间">
          <el-input v-model="scheduleForm.push_cron" placeholder="如: 09:00 或 0 9 * * *" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showScheduleDialog = false">取消</el-button>
        <el-button type="primary" @click="submitSchedule">确定</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { Clock, Bell, Edit, Delete, RefreshRight } from '@element-plus/icons-vue'
import { PLATFORM_TYPES, type PlatformAccount, updateAccount } from '@/api/platforms'

const props = defineProps<{
  account: PlatformAccount
  deleted?: boolean
}>()

const emit = defineEmits<{
  edit: [account: PlatformAccount]
  delete: [account: PlatformAccount]
  restore: [account: PlatformAccount]
  'permanent-delete': [account: PlatformAccount]
  'update-schedule': [account: PlatformAccount, schedule: { crawl_cron?: string; push_cron?: string }]
}>()

const showScheduleDialog = ref(false)
const scheduleForm = reactive({
  crawl_cron: props.account.crawl_cron || '08:00',
  push_cron: props.account.push_cron || '09:00',
})

function getPlatformName(platformType: string): string {
  const p = PLATFORM_TYPES.find(p => p.value === platformType)
  return p ? p.label : platformType
}

function getPlatformTagType(platformType: string): string {
  const map: Record<string, string> = {
    wechat: 'success',
    douyin: 'danger',
    xiaohongshu: 'warning',
    baijiahao: 'info',
    toutiao: '',
    youtube: 'primary',
  }
  return map[platformType] || 'info'
}

function formatCredentials(credentials: Record<string, string>): string {
  const keys = Object.keys(credentials)
  if (keys.length === 0) return '未配置'
  return keys.map(k => `${k}: ${credentials[k]}`).join(', ')
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '-'
  const d = new Date(dateStr)
  return d.toLocaleString('zh-CN')
}

async function handleActiveChange() {
  try {
    await updateAccount(props.account.account_id, { is_active: props.account.is_active })
    ElMessage.success('账号启用状态已更新')
  } catch (e: any) {
    ElMessage.error('更新账号状态失败: ' + (e?.message || e?.response?.data?.message || '未知错误'))
    // 回滚 switch 状态
    props.account.is_active = !props.account.is_active
  }
}

function submitSchedule() {
  emit('update-schedule', props.account, {
    crawl_cron: scheduleForm.crawl_cron,
    push_cron: scheduleForm.push_cron,
  })
  showScheduleDialog.value = false
}
</script>

<style scoped>
.account-card {
  border-radius: var(--radius-lg);
  transition: all var(--transition-normal);
  border: 1px solid var(--color-border-lighter);
}

.account-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.account-card.deleted {
  opacity: 0.75;
  background-color: var(--color-bg-page);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.platform-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.account-name {
  font-weight: 600;
  font-size: 15px;
  color: var(--color-text-primary);
}

.card-body {
  padding: 4px 0;
}

.info-row {
  display: flex;
  align-items: flex-start;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.info-label {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.info-value {
  font-size: 13px;
  color: var(--color-text-regular);
  word-break: break-all;
}

.info-value.credentials {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  color: var(--color-text-secondary);
}

.deleted-row .info-value {
  color: var(--color-danger);
}

.schedule-row {
  display: flex;
  gap: 24px;
}

.schedule-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.schedule-icon {
  font-size: 16px;
  color: var(--color-primary);
}

.schedule-content {
  display: flex;
  flex-direction: column;
}

.schedule-label {
  font-size: 11px;
  color: var(--color-text-secondary);
}

.schedule-value {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-primary);
}

.card-footer {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
</style>
