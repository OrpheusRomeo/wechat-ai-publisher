<template>
  <div>
    <h2>🛡️ IP 白名单</h2>
    <p style="color: #999; font-size: 13px; margin-bottom: 16px;">
      白名单为空时放行所有请求。支持单个 IP 或 CIDR 网段（如 192.168.1.0/24）。
    </p>

    <div style="margin-bottom: 16px; display: flex; gap: 12px; align-items: flex-end;">
      <el-form :inline="true">
        <el-form-item label="IP/CIDR">
          <el-input v-model="form.ip_or_cidr" placeholder="如 192.168.1.1 或 10.0.0.0/8" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="form.label" placeholder="如 办公网络" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleAdd" :loading="adding">添加</el-button>
        </el-form-item>
      </el-form>
    </div>

    <el-table :data="list" stripe>
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="ip_or_cidr" label="IP / CIDR" width="200" />
      <el-table-column prop="label" label="备注" />
      <el-table-column prop="enabled" label="状态" width="80">
        <template #default="{ row }">
          <el-switch v-model="row.enabled" @change="(v: boolean) => handleToggle(row, v)" />
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="160">
        <template #default="{ row }">{{ row.created_at?.slice(0, 16) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button type="danger" size="small" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ipWhitelistApi } from '@/api/ipWhitelist'
import { ElMessage, ElMessageBox } from 'element-plus'

const list = ref<any[]>([])
const form = reactive({ ip_or_cidr: '', label: '' })
const adding = ref(false)

async function load() {
  try { list.value = await ipWhitelistApi.getAll() } catch { /* ignore */ }
}

async function handleAdd() {
  if (!form.ip_or_cidr.trim()) { ElMessage.warning('请输入 IP 或 CIDR'); return }
  adding.value = true
  try {
    await ipWhitelistApi.create({ ip_or_cidr: form.ip_or_cidr, label: form.label, enabled: true })
    ElMessage.success('已添加')
    form.ip_or_cidr = ''
    form.label = ''
    load()
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '添加失败')
  } finally { adding.value = false }
}

async function handleToggle(row: any, value: boolean) {
  try {
    await ipWhitelistApi.update(row.id, { enabled: value })
  } catch {
    row.enabled = !value // 回退
  }
}

async function handleDelete(row: any) {
  try {
    await ElMessageBox.confirm(`确定删除 ${row.ip_or_cidr}?`, '确认删除', { type: 'warning' })
    await ipWhitelistApi.delete(row.id)
    ElMessage.success('已删除')
    load()
  } catch { /* cancel */ }
}

onMounted(load)
</script>
