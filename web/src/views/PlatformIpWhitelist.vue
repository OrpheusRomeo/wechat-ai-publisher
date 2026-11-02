<template>
  <div class="platform-ip-whitelist">
    <!-- 页面标题 -->
    <div class="page-header">
      <div class="header-left">
        <h2>🌐 平台 IP 白名单</h2>
        <p>记录本系统部署服务器向各平台发起调用时使用的出口 IP，需填入各平台的 IP 白名单中</p>
      </div>
      <el-button type="primary" @click="handleDetectIp">
        <el-icon><Connection /></el-icon>
        检测本机 IP
      </el-button>
    </div>

    <!-- 平台 Tab -->
    <div class="platform-tabs">
      <el-tabs v-model="activePlatform" @tab-change="handleTabChange">
        <el-tab-pane
          v-for="p in platformSummary"
          :key="p.platform_type"
          :label="`${p.display_name} (${p.ip_count})`"
          :name="p.platform_type"
        />
      </el-tabs>
    </div>

    <!-- 操作栏 -->
    <div class="action-bar">
      <el-button type="primary" @click="openCreateDialog">
        <el-icon><Plus /></el-icon>
        添加 IP
      </el-button>
    </div>

    <!-- 表格 -->
    <div v-loading="loading" class="table-wrap">
      <el-empty v-if="!loading && records.length === 0" description="暂无记录，请添加或点击「检测本机 IP 并同步」" />
      <el-table v-else :data="records" stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="ip_address" label="IP 地址" min-width="160" />
        <el-table-column prop="label" label="备注" min-width="160" />
        <el-table-column prop="is_auto_synced" label="来源" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.is_auto_synced" type="success" size="small">自动同步</el-tag>
            <el-tag v-else type="info" size="small">手动添加</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="is_active" label="状态" width="90">
          <template #default="{ row }">
            <el-switch
              v-model="row.is_active"
              @change="(v: boolean) => handleToggle(row, v)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="添加时间" width="160">
          <template #default="{ row }">
            {{ row.created_at ? row.created_at.slice(0, 16) : '-' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="openEditDialog(row)">编辑</el-button>
            <el-button size="small" type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 创建/编辑对话框 -->
    <el-dialog
      v-model="showDialog"
      :title="editingRecord ? '编辑记录' : '添加 IP'"
      width="500px"
      @closed="resetForm"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="90px">
        <el-form-item label="平台" prop="platform_type" v-if="!editingRecord">
          <el-select v-model="form.platform_type" placeholder="选择平台" style="width: 100%">
            <el-option
              v-for="p in PLATFORM_OPTIONS"
              :key="p.value"
              :label="p.label"
              :value="p.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="平台" v-else>
          <el-input :value="platformDisplayName(editingRecord.platform_type)" disabled />
        </el-form-item>

        <el-form-item label="IP 地址" prop="ip_address">
          <el-input v-model="form.ip_address" placeholder="如 114.246.34.137" />
        </el-form-item>

        <el-form-item label="备注" prop="label">
          <el-input v-model="form.label" placeholder="如 主服务器电信出口" maxlength="128" />
        </el-form-item>

        <el-form-item label="启用">
          <el-switch v-model="form.is_active" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="handleSave">保存</el-button>
      </template>
    </el-dialog>

    <!-- 检测本机 IP 弹出框 -->
    <el-dialog
      v-model="ipDialogVisible"
      title="检测本机 IP"
      width="420px"
    >
      <div class="ip-display-box">
        <p class="ip-label">本机公网出口 IP：</p>
        <div class="ip-value-row">
          <span class="ip-value">{{ detectedIp }}</span>
          <el-button size="small" @click="copyIp" :disabled="!detectedIp">复制</el-button>
        </div>
        <p class="ip-tip">请复制上方 IP，填入对应平台的 IP 白名单中</p>
      </div>
      <template #footer>
        <el-button @click="ipDialogVisible = false">关闭</el-button>
        <el-button type="primary" @click="saveDetectedIp" :loading="saving">添加到列表</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Connection } from '@element-plus/icons-vue'
import {
  getPlatforms,
  getList,
  create,
  update,
  remove,
  detectMyIp,
  type PlatformIPRecord,
  type PlatformSummary,
} from '@/api/platformIpWhitelist'

const PLATFORM_OPTIONS = [
  { value: 'wechat', label: '微信公众号' },
  { value: 'douyin', label: '抖音' },
  { value: 'toutiao', label: '今日头条' },
  { value: 'xiaohongshu', label: '小红书' },
  { value: 'youtube', label: 'YouTube' },
]

const platformDisplayName = (pt: string) =>
  PLATFORM_OPTIONS.find(p => p.value === pt)?.label || pt

const loading = ref(false)
const saving = ref(false)
const platformSummary = ref<PlatformSummary[]>([])
const records = ref<PlatformIPRecord[]>([])
const activePlatform = ref('wechat')
const showDialog = ref(false)
const ipDialogVisible = ref(false)
const detectedIp = ref('')
const editingRecord = ref<PlatformIPRecord | null>(null)
const formRef = ref()

const form = reactive({
  platform_type: 'wechat',
  ip_address: '',
  label: '',
  is_active: true,
})

const rules = {
  platform_type: [{ required: true, message: '请选择平台', trigger: 'change' }],
  ip_address: [
    { required: true, message: '请输入 IP 地址', trigger: 'blur' },
    {
      pattern: /^(\d{1,3}\.){3}\d{1,3}$|^(?:[\da-f]{0,4}:){1,7}[\da-f]{0,4}$/i,
      message: '请输入有效的 IPv4 或 IPv6 地址',
      trigger: 'blur',
    },
  ],
}

async function loadPlatforms() {
  try {
    const res = await getPlatforms()
    platformSummary.value = res.data.data || []
    // 默认选中第一个有数据的平台，否则默认 wechat
    if (platformSummary.value.length > 0 && !platformSummary.value.find(p => p.platform_type === activePlatform.value)) {
      activePlatform.value = platformSummary.value[0].platform_type
    }
  } catch {
    platformSummary.value = []
  }
}

async function loadRecords() {
  loading.value = true
  try {
    const res = await getList(activePlatform.value)
    records.value = res.data.data || []
  } catch {
    ElMessage.error('加载失败')
    records.value = []
  } finally {
    loading.value = false
  }
}

function handleTabChange() {
  loadRecords()
}

async function handleDetectIp() {
  try {
    const res = await detectMyIp()
    detectedIp.value = res.data.data.ip_address
    ipDialogVisible.value = true
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '检测失败')
  }
}

async function saveDetectedIp() {
  if (!detectedIp.value) return
  saving.value = true
  try {
    await create({
      platform_type: 'wechat',
      ip_address: detectedIp.value,
      label: '自动同步',
      is_active: true,
    })
    ElMessage.success('已添加到列表')
    ipDialogVisible.value = false
    await loadPlatforms()
    await loadRecords()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '添加失败')
  } finally {
    saving.value = false
  }
}

function copyIp() {
  navigator.clipboard.writeText(detectedIp.value).then(() => {
    ElMessage.success('已复制到剪贴板')
  }).catch(() => {
    ElMessage.error('复制失败，请手动复制')
  })
}

function openCreateDialog() {
  editingRecord.value = null
  form.platform_type = activePlatform.value
  form.ip_address = ''
  form.label = ''
  form.is_active = true
  showDialog.value = true
}

function openEditDialog(row: PlatformIPRecord) {
  editingRecord.value = row
  form.ip_address = row.ip_address
  form.label = row.label
  form.is_active = row.is_active
  showDialog.value = true
}

function resetForm() {
  editingRecord.value = null
  form.platform_type = 'wechat'
  form.ip_address = ''
  form.label = ''
  form.is_active = true
}

async function handleSave() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  saving.value = true
  try {
    if (editingRecord.value) {
      await update(editingRecord.value.id, {
        ip_address: form.ip_address,
        label: form.label,
        is_active: form.is_active,
      })
      ElMessage.success('更新成功')
    } else {
      await create({
        platform_type: form.platform_type,
        ip_address: form.ip_address,
        label: form.label,
        is_active: form.is_active,
      })
      ElMessage.success('添加成功')
    }
    showDialog.value = false
    await loadPlatforms()
    await loadRecords()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleToggle(row: PlatformIPRecord, value: boolean) {
  try {
    await update(row.id, { is_active: value })
  } catch {
    row.is_active = !value
    ElMessage.error('更新失败')
  }
}

async function handleDelete(row: PlatformIPRecord) {
  try {
    await ElMessageBox.confirm(`删除 ${row.ip_address}？`, '确认删除', { type: 'warning' })
    await remove(row.id)
    ElMessage.success('已删除')
    await loadPlatforms()
    await loadRecords()
  } catch { /* cancel */ }
}

onMounted(async () => {
  await loadPlatforms()
  await loadRecords()
})
</script>

<style scoped>
.platform-ip-whitelist {
  padding: 24px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 20px;
}

.header-left h2 {
  margin: 0 0 4px;
  font-size: 20px;
}

.header-left p {
  margin: 0;
  color: #909399;
  font-size: 13px;
}

.platform-tabs {
  margin-bottom: 16px;
}

.action-bar {
  margin-bottom: 12px;
}

.table-wrap {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
}

.ip-display-box {
  padding: 8px 0;
}

.ip-label {
  margin: 0 0 12px;
  font-size: 14px;
  color: #606266;
}

.ip-value-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.ip-value {
  font-size: 22px;
  font-weight: 600;
  font-family: 'Courier New', monospace;
  color: #409eff;
  letter-spacing: 1px;
}

.ip-tip {
  margin: 0;
  font-size: 12px;
  color: #909399;
}
</style>
