<template>
  <div class="toutiao-hot">
    <div class="page-header">
      <h2>📰 今日头条热搜</h2>
      <p>爬取头条热榜 → 选择话题 → AI 生成文章 → 发布到平台草稿箱</p>
    </div>

    <!-- 工具栏 -->
    <div class="toolbar">
      <div class="toolbar-left">
        <el-select v-model="selectedAccount" placeholder="选择头条账号" clearable style="width: 220px" @change="handleAccountChange">
          <el-option label="全部账号" value="" />
          <el-option v-for="a in toutiaoAccounts" :key="a.account_id" :label="a.account_name" :value="a.account_id" />
        </el-select>
      </div>
      <div class="toolbar-right">
        <el-button type="primary" :icon="RefreshRight" :loading="crawling" @click="handleCrawl" :disabled="!selectedAccount">
          立即爬取
        </el-button>
        <el-button :icon="Refresh" @click="loadTopics" :loading="loading">刷新列表</el-button>
        <el-button
          type="danger"
          :icon="Delete"
          :disabled="selectedTopics.length === 0"
          :loading="batchDeleting"
          @click="handleBatchDeleteTopics"
        >
          批量删除 ({{ selectedTopics.length }})
        </el-button>
      </div>
    </div>

    <!-- 话题列表 -->
    <el-card class="content-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <span class="card-title">话题列表</span>
          <span class="card-count">共 {{ topics.length }} 条</span>
        </div>
      </template>
      <el-table :data="topics" stripe max-height="450" @selection-change="handleSelectionChange" v-loading="loading">
        <el-table-column type="selection" width="45" />
        <el-table-column prop="title" label="热搜话题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="heat_score" label="热度" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" type="danger" effect="plain">{{ row.heat_score }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small" effect="light">
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="入库时间" width="160">
          <template #default="{ row }">
            <span class="time-text">{{ formatTime(row.create_time) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="140" align="center">
          <template #default="{ row }">
            <el-button size="small" type="primary" :loading="generating[row.id]" @click="handleGenerate(row)">
              生成文章
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div v-if="selectedTopics.length > 0" class="batch-action">
        <el-button type="success" :loading="batchGenerating" @click="handleBatchGenerate">
          <el-icon><Crop /></el-icon>
          批量生成选中话题 ({{ selectedTopics.length }}) 并发布到草稿箱
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { Delete, Refresh, RefreshRight, Crop } from '@element-plus/icons-vue'
import { topicsApi, articlesApi, tasksApi, platformAccountsApi } from '@/api/articles'
import { ElMessage, ElMessageBox } from 'element-plus'
import { PLATFORM_TYPES } from '@/api/platforms'

const topics = ref<any[]>([])
const selectedTopics = ref<any[]>([])
const loading = ref(false)
const crawling = ref(false)
const batchDeleting = ref(false)
const generating = ref<Record<number, boolean>>({})
const batchGenerating = ref(false)

const selectedAccount = ref('')
const accounts = ref<any[]>([])

const toutiaoAccounts = computed(() =>
  accounts.value.filter(a => a.platform_type === 'toutiao' && !a.is_deleted)
)

const platformLabel = computed(() => {
  const p = PLATFORM_TYPES.find(t => t.value === 'toutiao')
  return p ? p.label : '头条'
})

async function loadAccounts() {
  try {
    const res = await platformAccountsApi.getAccounts({ platform_type: 'toutiao', include_deleted: false })
    accounts.value = res.items || []
  } catch (e) {
    console.error('Failed to load toutiao accounts:', e)
  }
}

function handleSelectionChange(val: any[]) { selectedTopics.value = val }

function handleAccountChange() {
  loading.value = true
  loadTopics()
}

function statusType(status: string) {
  const map: Record<string, string> = {
    pending: 'warning',
    selected: 'primary',
    processed: 'success',
    failed: 'danger',
  }
  return map[status] || 'info'
}

function statusText(status: string) {
  const map: Record<string, string> = {
    pending: '待处理',
    selected: '已选择',
    processed: '已处理',
    failed: '失败',
  }
  return map[status] || status
}

function formatTime(iso: string | undefined): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

async function loadTopics() {
  loading.value = true
  try {
    if (selectedAccount.value) {
      const res = await platformAccountsApi.getAccountTopics(selectedAccount.value, { page_size: 50 })
      topics.value = res.data || []
    } else {
      // 并行加载所有账号的话题
      const results = await Promise.all(
        toutiaoAccounts.value.map(acc =>
          platformAccountsApi.getAccountTopics(acc.account_id, { page_size: 50 })
            .then(res => res.data || [])
            .catch(e => {
              console.error('Failed to load topics for account:', acc.account_id)
              return []
            })
        )
      )
      topics.value = results.flat()
    }
  } catch (e: any) { ElMessage.error('加载失败') }
  finally { loading.value = false }
}

async function pollTaskUntilDone(taskId: string, timeoutMs = 120_000): Promise<void> {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    await new Promise(r => setTimeout(r, 2000))
    try {
      const status = await tasksApi.getStatus(taskId)
      if (status.state === 'SUCCESS') return
      if (status.state === 'FAILURE') throw new Error(status.error || '任务失败')
    } catch (e: any) {
      if (e?.message?.includes('任务失败')) throw e
    }
  }
  throw new Error('任务执行超时, 请稍后手动刷新')
}

async function handleCrawl() {
  if (!selectedAccount.value) {
    ElMessage.warning('请先选择头条账号')
    return
  }
  crawling.value = true
  try {
    const resp = await topicsApi.triggerCrawlForAccount(selectedAccount.value)
    const taskId = resp?.data?.task_id
    if (!taskId) throw new Error('未拿到任务 ID')
    ElMessage.info('爬取任务已提交, 等待完成...')
    await pollTaskUntilDone(taskId)
    ElMessage.success('爬取完成')
    await loadTopics()
  } catch (e: any) {
    ElMessage.error(e.message || e.response?.data?.detail || '爬取失败')
  } finally {
    crawling.value = false
  }
}

async function handleGenerate(topic: any) {
  generating.value[topic.id] = true
  try {
    const res = await articlesApi.generate(topic.id)
    ElMessage.success(`文章 "${res.title || topic.title}" 已生成`)
  } catch (e: any) { ElMessage.error('生成失败') }
  finally { generating.value[topic.id] = false }
}

async function handleBatchDeleteTopics() {
  if (selectedTopics.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${selectedTopics.value.length} 个话题? 关联的用户选择记录也会一并清理.`,
      '批量删除话题',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  batchDeleting.value = true
  try {
    const ids = selectedTopics.value.map(t => t.id)
    const res = await topicsApi.batchDelete(ids)
    ElMessage.success(res.message || `已删除 ${ids.length} 条`)
    selectedTopics.value = []
    await loadTopics()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '批量删除失败')
  } finally {
    batchDeleting.value = false
  }
}

async function handleBatchGenerate() {
  batchGenerating.value = true
  try {
    await Promise.all(
      selectedTopics.value.map(topic =>
        (async () => {
          generating.value[topic.id] = true
          try {
            await articlesApi.generate(topic.id)
          } finally {
            generating.value[topic.id] = false
          }
        })()
      )
    )
    ElMessage.success(`已为 ${selectedTopics.value.length} 个话题提交生成任务`)
  } catch (e: any) {
    ElMessage.error('批量生成过程中出现错误')
  } finally { batchGenerating.value = false }
}

onMounted(async () => {
  await loadAccounts()
  loadTopics()
})
</script>

<style scoped>
.toutiao-hot {
  max-width: 1400px;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h2 {
  margin: 0 0 8px 0;
  font-size: 22px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.page-header p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 14px;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 12px;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.content-card {
  border-radius: var(--radius-lg);
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.card-count {
  font-size: 13px;
  color: var(--color-text-secondary);
}

.time-text {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.batch-action {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--color-border-lighter);
}
</style>
