<template>
  <div class="platform-accounts">
    <!-- 页面标题 -->
    <div class="page-header">
      <div class="header-left">
        <h2>平台账号管理</h2>
        <p>管理多平台发布账号，统一调度爬虫和推送任务</p>
      </div>
      <el-button type="primary" @click="showCreateDialog = true">
        <el-icon><Plus /></el-icon>
        新增账号
      </el-button>
    </div>

    <!-- 平台 Tab -->
    <el-tabs v-model="activePlatform" class="platform-tabs" @tab-change="handlePlatformChange">
      <el-tab-pane label="全部" name="all" />
      <el-tab-pane
        v-for="p in platforms"
        :key="p.platform_type"
        :label="p.display_name"
        :name="p.platform_type"
      />
    </el-tabs>

    <!-- 账号 Tab -->
    <el-tabs v-model="activeTab" class="account-tabs">
      <el-tab-pane label="有效账号" name="active">
        <template #label>
          <span>有效账号 ({{ activeAccounts.length }})</span>
        </template>
      </el-tab-pane>
      <el-tab-pane label="已删除" name="deleted">
        <template #label>
          <span class="deleted-tab">已删除 ({{ deletedAccounts.length }})</span>
        </template>
      </el-tab-pane>
    </el-tabs>

    <!-- 账号卡片列表 -->
    <div v-loading="loading" class="account-list">
      <template v-if="activeTab === 'active'">
        <div v-if="activeAccounts.length === 0" class="empty-state">
          <el-empty description="暂无有效账号" />
        </div>
        <div v-else class="cards-grid">
          <AccountCard
            v-for="account in activeAccounts"
            :key="account.account_id"
            :account="account"
            @edit="handleEdit"
            @delete="handleSoftDelete"
            @update-schedule="handleUpdateSchedule"
          />
        </div>
      </template>

      <template v-else>
        <div v-if="deletedAccounts.length === 0" class="empty-state">
          <el-empty description="暂无已删除账号" />
        </div>
        <div v-else class="cards-grid">
          <AccountCard
            v-for="account in deletedAccounts"
            :key="account.account_id"
            :account="account"
            :deleted="true"
            @restore="handleRestore"
            @permanent-delete="handlePermanentDelete"
          />
        </div>
      </template>
    </div>

    <!-- 创建/编辑账号对话框 -->
    <AccountFormDialog
      v-model="showCreateDialog"
      :account="editingAccount"
      :platforms="platforms"
      @submit="handleSubmit"
      @cancel="closeFormDialog"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getPlatforms,
  getAccounts,
  createAccount,
  updateAccount,
  softDeleteAccount,
  restoreAccount,
  permanentDeleteAccount,
  updateAccountSchedule,
  type Platform,
  type PlatformAccount,
} from '@/api/platforms'
import { tasksApi } from '@/api/articles'
import AccountCard from '@/components/AccountCard.vue'
import AccountFormDialog from '@/components/AccountFormDialog.vue'

const loading = ref(false)
const platforms = ref<Platform[]>([])
const accounts = ref<PlatformAccount[]>([])
const activePlatform = ref('all')
const activeTab = ref('active')
const showCreateDialog = ref(false)
const editingAccount = ref<PlatformAccount | null>(null)

const filteredAccounts = computed(() => {
  if (activePlatform.value === 'all') {
    return accounts.value
  }
  return accounts.value.filter(a => a.platform_type === activePlatform.value)
})

const activeAccounts = computed(() =>
  filteredAccounts.value.filter(a => !a.is_deleted)
)

const deletedAccounts = computed(() =>
  filteredAccounts.value.filter(a => a.is_deleted)
)

async function loadPlatforms() {
  try {
    const res = await getPlatforms()
    platforms.value = res.data.data || []
  } catch (e) {
    console.error('Failed to load platforms:', e)
  }
}

async function loadAccounts() {
  loading.value = true
  try {
    const res = await getAccounts({ include_deleted: true })
    accounts.value = res.data.items || []
  } catch (e) {
    ElMessage.error('加载账号列表失败')
  } finally {
    loading.value = false
  }
}

function handlePlatformChange() {
  if (activeTab.value === 'deleted' && deletedAccounts.value.length === 0) {
    activeTab.value = 'active'
  }
}

function handleEdit(account: PlatformAccount) {
  editingAccount.value = account
  showCreateDialog.value = true
}

async function handleSoftDelete(account: PlatformAccount) {
  try {
    await ElMessageBox.confirm(
      `确定要删除账号「${account.account_name}」吗？删除后可从「已删除」中恢复。`,
      '确认删除',
      { type: 'warning' }
    )
    await softDeleteAccount(account.account_id)
    ElMessage.success('账号已删除')
    await loadAccounts()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

async function handleRestore(account: PlatformAccount) {
  try {
    await restoreAccount(account.account_id)
    ElMessage.success('账号已恢复')
    await loadAccounts()
  } catch (e) {
    ElMessage.error('恢复失败')
  }
}

async function handlePermanentDelete(account: PlatformAccount) {
  try {
    await ElMessageBox.confirm(
      `确定要永久删除账号「${account.account_name}」吗？此操作不可恢复！`,
      '永久删除',
      { type: 'error', confirmButtonText: '永久删除' }
    )
    await permanentDeleteAccount(account.account_id)
    ElMessage.success('账号已永久删除')
    await loadAccounts()
  } catch (e: any) {
    if (e !== 'cancel') {
      ElMessage.error('永久删除失败')
    }
  }
}

async function handleUpdateSchedule(account: PlatformAccount, schedule: { crawl_cron?: string; push_cron?: string }) {
  try {
    await updateAccountSchedule(account.account_id, schedule)
    try {
      await tasksApi.reloadBeat()
    } catch (e) {
      console.warn('Failed to reload celery beat:', e)
    }
    ElMessage.success('调度时间已更新')
    await loadAccounts()
  } catch (e) {
    ElMessage.error('更新调度失败')
  }
}

async function handleSubmit(data: any) {
  try {
    if (editingAccount.value) {
      await updateAccount(editingAccount.value.account_id, data)
      ElMessage.success('账号已更新')
    } else {
      await createAccount(data)
      ElMessage.success('账号已创建')
    }
    closeFormDialog()
    await loadAccounts()
  } catch (e: any) {
    ElMessage.error(editingAccount.value ? '更新失败' : '创建失败')
  }
}

function closeFormDialog() {
  showCreateDialog.value = false
  editingAccount.value = null
}

onMounted(() => {
  loadPlatforms()
  loadAccounts()
})
</script>

<style scoped>
.platform-accounts {
  max-width: 1400px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
}

.header-left h2 {
  margin: 0 0 8px 0;
  font-size: 22px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.header-left p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 14px;
}

.platform-tabs {
  margin-bottom: 16px;
}

.account-tabs {
  margin-bottom: 20px;
}

.deleted-tab {
  color: var(--color-danger);
}

.account-list {
  min-height: 200px;
}

.cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 20px;
}

.empty-state {
  padding: 60px 0;
  text-align: center;
}
</style>
