<template>
  <div class="keyword-article">
    <div class="page-header">
      <h2>📚 深度文章</h2>
      <p>输入关键词，AI 将生成高质量的深度文章并发布到平台</p>
    </div>

    <!-- 关键词生成器 -->
    <el-card class="generator-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <span class="card-title">✏️ 关键词文章生成</span>
        </div>
      </template>
      <el-form :model="form" label-width="0" :inline="false">
        <el-form-item>
          <el-input
            v-model="form.keyword"
            placeholder="请输入搜索关键词，如 '量子计算最新突破'"
            size="large"
            clearable
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </el-form-item>

        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="文章风格" label-width="80px">
              <el-select v-model="form.style" placeholder="选择文章风格" style="width: 100%">
                <el-option label="标准 (1500-2000字)" value="standard" />
                <el-option label="详细 (3000-4000字)" value="detailed" />
                <el-option label="简洁 (800-1200字)" value="brief" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="自动发布" label-width="80px">
              <el-checkbox v-model="form.autoPublish" style="margin-top: 8px">
                生成后自动发布到草稿箱
              </el-checkbox>
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item>
          <el-input
            v-model="form.customPrompt"
            type="textarea"
            :rows="3"
            placeholder="自定义 Prompt (可选, 覆盖默认模板)"
          />
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            size="large"
            :icon="DocumentAdd"
            :loading="submitting"
            @click="handleSubmit"
          >
            生成文章
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 文章列表 -->
    <el-card class="content-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <span class="card-title">📋 全部文章</span>
          <span class="card-count">共 {{ total }} 篇</span>
        </div>
      </template>

      <!-- 筛选工具栏 -->
      <div class="toolbar">
        <div class="toolbar-left">
          <el-select v-model="selectedPlatform" placeholder="选择平台" clearable style="width: 150px" @change="handlePlatformChange">
            <el-option label="全部平台" value="" />
            <el-option v-for="p in platformOptions" :key="p.value" :label="p.label" :value="p.value" />
          </el-select>
          <el-select v-model="selectedAccount" placeholder="选择账号" clearable style="width: 200px" :disabled="!selectedPlatform" @change="loadArticles">
            <el-option label="全部账号" value="" />
            <el-option v-for="a in accountOptions" :key="a.account_id" :label="a.account_name" :value="a.account_id" />
          </el-select>
        </div>
        <div class="toolbar-right">
          <el-button :icon="Refresh" @click="loadArticles" :loading="loading">刷新</el-button>
          <el-button
            type="danger"
            :icon="Delete"
            :disabled="selectedArticles.length === 0"
            :loading="batchDeleting"
            @click="handleBatchDelete"
          >
            批量删除 ({{ selectedArticles.length }})
          </el-button>
        </div>
      </div>

      <el-table
        :data="articles"
        stripe
        size="small"
        max-height="450"
        @selection-change="handleSelectionChange"
        v-loading="loading"
      >
        <el-table-column type="selection" width="45" />
        <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
        <el-table-column label="平台" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.platform_account_id" size="small" :type="getPlatformTagType(row.platform_type)">
              {{ getPlatformName(row.platform_type) }}
            </el-tag>
            <el-tag v-else size="small" type="info">全局</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="110" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.topic_id" type="warning" size="small" effect="plain">🔥 热搜</el-tag>
            <el-tag v-else-if="row.extra_metadata?.source === 'manual_keyword'" type="success" size="small" effect="plain">📝 关键词</el-tag>
            <el-tag v-else-if="row.extra_metadata?.is_digest" type="info" size="small" effect="plain">☀️ 日报</el-tag>
            <el-tag v-else size="small" type="info">其他</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small" effect="light">
              {{ statusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="生成时间" width="160">
          <template #default="{ row }">
            <span class="time-text">{{ formatTime(row.create_time) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="220" align="center">
          <template #default="{ row }">
            <el-button size="small" @click="viewArticle(row)">查看</el-button>
            <el-button
              size="small"
              type="success"
              @click="openPlatformPublishDialog(row)"
            >
              发布到平台
            </el-button>
            <el-button
              v-if="row.status === 'draft'"
              size="small"
              type="warning"
              @click="retryPublish(row)"
            >
              重试
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 详情弹窗 -->
    <el-dialog v-model="showDetail" title="文章预览" width="700px" destroy-on-close>
      <div v-if="detail" class="detail-content">
        <div class="detail-row">
          <span class="detail-label">标题</span>
          <span class="detail-value">{{ detail.title }}</span>
        </div>
        <div class="detail-row">
          <span class="detail-label">来源</span>
          <el-tag v-if="detail.topic_id" type="warning" size="small">🔥 热搜 #{{ detail.topic_id }}</el-tag>
          <el-tag v-else-if="detail.extra_metadata?.source === 'manual_keyword'" type="success" size="small">
            📝 关键词 {{ detail.extra_metadata?.keyword || '' }}
          </el-tag>
          <el-tag v-else-if="detail.extra_metadata?.is_digest" type="info" size="small">☀️ 每日日报</el-tag>
          <el-tag v-else size="small">其他</el-tag>
        </div>
        <div class="detail-row">
          <span class="detail-label">状态</span>
          <el-tag :type="statusType(detail.status)" size="small">{{ statusText(detail.status) }}</el-tag>
        </div>
        <div v-if="detail.error_msg" class="detail-error">
          <el-icon><Warning /></el-icon>
          {{ detail.error_msg }}
        </div>
        <div class="detail-preview">
          <div class="preview-label">内容预览</div>
          <div class="preview-text">{{ detail.content?.slice(0, 2000) }}{{ (detail.content?.length || 0) > 2000 ? '…' : '' }}</div>
        </div>
      </div>
      <template #footer>
        <el-button @click="showDetail = false">关闭</el-button>
        <el-button v-if="detail && detail.status !== 'published'" type="success" @click="publishCurrent">
          发布到草稿箱
        </el-button>
      </template>
    </el-dialog>

    <!-- 发布到平台账号弹窗 -->
    <el-dialog v-model="showPlatformDialog" title="发布到平台" width="500px" destroy-on-close>
      <el-form label-width="90px">
        <el-form-item label="选择平台">
          <el-select v-model="platformDialogForm.platformType" placeholder="选择平台" style="width: 100%" @change="handlePlatformDialogTypeChange">
            <el-option
              v-for="p in platformOptions.filter(p => p.value !== 'wechat')"
              :key="p.value"
              :label="p.label"
              :value="p.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="选择账号">
          <el-select v-model="platformDialogForm.platformAccountId" placeholder="请先选择平台" style="width: 100%" :disabled="!platformDialogForm.platformType">
            <el-option
              v-for="a in platformDialogAccountOptions"
              :key="a.id"
              :label="a.account_name"
              :value="a.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showPlatformDialog = false">取消</el-button>
        <el-button type="primary" :loading="platformDialogForm.publishing" @click="confirmPlatformPublish">
          确认发布
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onUnmounted, computed } from 'vue'
import { Delete, Refresh, DocumentAdd, Search, Warning } from '@element-plus/icons-vue'
import { articlesApi, platformAccountsApi } from '@/api/articles'
import { keywordArticlesApi } from '@/api/keywordArticles'
import { ElMessage, ElMessageBox } from 'element-plus'
import { PLATFORM_TYPES } from '@/api/platforms'

const form = reactive({
  keyword: '',
  style: 'standard',
  customPrompt: '',
  autoPublish: false,
})
const submitting = ref(false)
const loading = ref(false)
const articles = ref<any[]>([])
const total = ref(0)
const selectedArticles = ref<any[]>([])
const batchDeleting = ref(false)
const showDetail = ref(false)
const detail = ref<any>(null)

// 发布到平台弹窗
const showPlatformDialog = ref(false)
const platformDialogForm = reactive({
  articleId: null as number | null,
  platformType: '',
  platformAccountId: null as number | null,
  publishing: false,
})

const platformDialogAccountOptions = computed(() => {
  if (!platformDialogForm.platformType) return []
  return accounts.value.filter(a => a.platform_type === platformDialogForm.platformType && !a.is_deleted && a.is_active)
})

const selectedPlatform = ref('')
const selectedAccount = ref('')
const accounts = ref<any[]>([])

// AbortController for cancelling in-flight requests on unmount
// TODO: 透传 signal 到 platformAccountsApi.getAccountArticles() 以真正取消请求
const articlesController = ref<AbortController | null>(null)

const platformOptions = PLATFORM_TYPES.map(p => ({ value: p.value, label: p.label }))

const accountOptions = computed(() => {
  if (!selectedPlatform.value) return []
  return accounts.value
    .filter(a => a.platform_type === selectedPlatform.value && !a.is_deleted)
    .map(a => ({ account_id: a.account_id, account_name: a.account_name }))
})

async function loadAccounts() {
  try {
    const res = await platformAccountsApi.getAccounts({ include_deleted: false })
    accounts.value = res.items || []
  } catch (e) {
    console.error('Failed to load accounts:', e)
  }
}

function handlePlatformChange() {
  selectedAccount.value = ''
  loadArticles()
}

function statusType(status: string) {
  const map: Record<string, string> = {
    generated: 'success',
    published: '',
    draft: 'info',
    failed: 'danger',
    generating: 'warning',
  }
  return map[status] || 'info'
}

function statusText(status: string) {
  const map: Record<string, string> = {
    generated: '已生成',
    published: '已发布',
    draft: '草稿',
    failed: '失败',
    generating: '生成中',
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

function handleSelectionChange(val: any[]) { selectedArticles.value = val }

function getPlatformName(platformType: string | undefined): string {
  if (!platformType) return '全局'
  const p = PLATFORM_TYPES.find(p => p.value === platformType)
  return p ? p.label : platformType
}

function getPlatformTagType(platformType: string | undefined): string {
  if (!platformType) return 'info'
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

async function loadArticles() {
  // Cancel any in-flight request from a previous call
  articlesController.value?.abort()
  articlesController.value = new AbortController()
  loading.value = true
  try {
    if (selectedAccount.value) {
      const res = await platformAccountsApi.getAccountArticles(selectedAccount.value, { page_size: 50 })
      articles.value = res.data || []
      total.value = res.total || 0
    } else if (selectedPlatform.value) {
      const platformAccounts = accounts.value.filter(a => a.platform_type === selectedPlatform.value)
      const results = await Promise.allSettled(
        platformAccounts.map(acc =>
          platformAccountsApi.getAccountArticles(acc.account_id, { page_size: 50 })
        )
      )
      const allArticles: any[] = []
      for (const result of results) {
        if (result.status === 'fulfilled') {
          allArticles.push(...((result.value as any).data || []))
        } else {
          console.error('Failed to load articles:', result.reason)
        }
      }
      articles.value = allArticles
      total.value = allArticles.length
    } else {
      const res = await articlesApi.getAll({ page_size: 50 })
      articles.value = res.items || []
      total.value = res.total || 0
    }
  } catch (e: any) {
    ElMessage.error('加载文章失败: ' + (e?.message || e?.response?.data?.message || '未知错误'))
  }
  finally { loading.value = false }
}

async function handleBatchDelete() {
  if (selectedArticles.value.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${selectedArticles.value.length} 篇文章? 此操作不可恢复。`,
      '批量删除',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch { return }
  batchDeleting.value = true
  try {
    const ids = selectedArticles.value.map(a => a.id)
    const res = await articlesApi.batchDelete(ids)
    ElMessage.success(res.message || `已删除 ${ids.length} 篇`)
    selectedArticles.value = []
    await loadArticles()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '批量删除失败')
  } finally {
    batchDeleting.value = false
  }
}

async function handleSubmit() {
  if (!form.keyword.trim()) { ElMessage.warning('请输入关键词'); return }
  submitting.value = true
  try {
    const res = await keywordArticlesApi.create(
      form.keyword,
      form.style,
      form.autoPublish,
      form.customPrompt,
    )
    const taskId = res?.data?.task_id
    ElMessage.success(`任务已提交${taskId ? `，task_id: ${taskId}` : ''}`)
    form.keyword = ''
    setTimeout(loadArticles, 5000)
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '生成失败')
  } finally { submitting.value = false }
}

async function viewArticle(row: any) {
  try {
    const res = await articlesApi.getOne(row.id)
    detail.value = res.data
    showDetail.value = true
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '获取详情失败')
  }
}

async function publishArticle(row: any) {
  try {
    await articlesApi.publish(row.id)
    ElMessage.success('已提交发布任务')
    loadArticles()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '发布失败')
  }
}

async function publishCurrent() {
  if (!detail.value) return
  await publishArticle(detail.value)
  showDetail.value = false
}

async function retryPublish(row: any) {
  await publishArticle(row)
}

function openPlatformPublishDialog(row: any) {
  platformDialogForm.articleId = row.id
  platformDialogForm.platformType = ''
  platformDialogForm.platformAccountId = null
  showPlatformDialog.value = true
}

function handlePlatformDialogTypeChange() {
  platformDialogForm.platformAccountId = null
}

async function confirmPlatformPublish() {
  if (!platformDialogForm.articleId || !platformDialogForm.platformAccountId) {
    ElMessage.warning('请选择平台和账号')
    return
  }
  platformDialogForm.publishing = true
  try {
    const res = await articlesApi.publishToPlatform(
      platformDialogForm.articleId,
      platformDialogForm.platformAccountId,
    )
    ElMessage.success('发布任务已提交')
    showPlatformDialog.value = false
    loadArticles()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '发布失败')
  } finally {
    platformDialogForm.publishing = false
  }
}

onMounted(async () => {
  await loadAccounts()
  loadArticles()
})

onUnmounted(() => {
  articlesController.value?.abort()
})
</script>

<style scoped>
.keyword-article {
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

.generator-card,
.content-card {
  border-radius: var(--radius-lg);
  margin-bottom: 20px;
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

.time-text {
  color: var(--color-text-secondary);
  font-size: 12px;
}

.detail-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.detail-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}

.detail-label {
  width: 60px;
  color: var(--color-text-secondary);
  flex-shrink: 0;
}

.detail-value {
  color: var(--color-text-primary);
  font-weight: 500;
}

.detail-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  background: #fef0f0;
  border-radius: var(--radius-md);
  color: var(--color-danger);
  font-size: 13px;
}

.detail-preview {
  margin-top: 8px;
}

.preview-label {
  color: var(--color-text-secondary);
  font-size: 13px;
  margin-bottom: 8px;
}

.preview-text {
  max-height: 400px;
  overflow: auto;
  white-space: pre-wrap;
  background: var(--color-bg-page);
  padding: 16px;
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--color-text-regular);
}
</style>
