<template>
  <div class="llm-providers">
    <!-- 页面标题 -->
    <div class="page-header">
      <div class="header-left">
        <h2>🤖 LLM 模型配置</h2>
        <p>管理大模型供应商，支持 DeepSeek / OpenAI / MiniMax / Qwen 等</p>
      </div>
      <el-button type="primary" @click="openCreateDialog">
        <el-icon><Plus /></el-icon>
        添加供应商
      </el-button>
    </div>

    <!-- 供应商列表 -->
    <div v-loading="loading" class="provider-list">
      <el-empty v-if="!loading && providers.length === 0" description="暂无供应商，请添加" />

      <el-table v-else :data="providers" stripe>
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="名称" min-width="120">
          <template #default="{ row }">
            <span>{{ row.name }}</span>
            <el-tag v-if="row.is_default" type="success" size="small" class="ml-2">默认</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="provider_type" label="类型" width="100">
          <template #default="{ row }">
            <el-tag>{{ PROVIDER_TYPES.find(t => t.value === row.provider_type)?.label || row.provider_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="api_url" label="API 地址" min-width="200" show-overflow-tooltip />
        <el-table-column prop="model" label="模型" min-width="150" />
        <el-table-column prop="temperature" label="温度" width="70" />
        <el-table-column prop="timeout" label="超时(s)" width="80" />
        <el-table-column prop="is_active" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="openEditDialog(row)">编辑</el-button>
            <el-button v-if="!row.is_default" size="small" type="success" @click="handleSetDefault(row)">设为默认</el-button>
            <el-button size="small" type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 创建/编辑对话框 -->
    <el-dialog
      v-model="showDialog"
      :title="editingProvider ? '编辑供应商' : '添加供应商'"
      width="600px"
      @closed="resetForm"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="如：DeepSeek 官方" maxlength="50" />
        </el-form-item>

        <el-form-item label="类型" prop="provider_type">
          <el-select v-model="form.provider_type" placeholder="选择模型类型" style="width: 100%">
            <el-option
              v-for="t in PROVIDER_TYPES"
              :key="t.value"
              :label="t.label"
              :value="t.value"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="API 地址" prop="api_url">
          <el-input v-model="form.api_url" placeholder="如：https://api.deepseek.com/v1" />
        </el-form-item>

        <el-form-item label="API Key" :prop="editingProvider ? 'api_key_optional' : 'api_key'">
          <el-input
            v-model="form.api_key"
            type="password"
            :placeholder="editingProvider ? '留空则不修改' : '请输入 API Key'"
            show-password
          />
        </el-form-item>

        <el-form-item label="模型" prop="model">
          <el-input v-model="form.model" placeholder="如：deepseek-chat" maxlength="100" />
        </el-form-item>

        <el-form-item label="温度" prop="temperature">
          <el-slider v-model="form.temperature" :min="0" :max="2" :step="0.1" show-stops :marks="{0: '0', 0.7: '0.7', 1.5: '1.5', 2: '2'}" style="width: 90%" />
        </el-form-item>

        <el-form-item label="超时(秒)" prop="timeout">
          <el-input-number v-model="form.timeout" :min="10" :max="300" />
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
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getProviders,
  createProvider,
  updateProvider,
  deleteProvider,
  setDefaultProvider,
  type LLMProvider,
} from '@/api/llmProviders'

const PROVIDER_TYPES = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'minimax', label: 'MiniMax' },
  { value: 'qwen', label: 'Qwen (通义千问)' },
]

const loading = ref(false)
const saving = ref(false)
const providers = ref<LLMProvider[]>([])
const showDialog = ref(false)
const editingProvider = ref<LLMProvider | null>(null)
const formRef = ref()

const form = reactive({
  name: '',
  provider_type: 'deepseek',
  api_url: '',
  api_key: '',
  model: '',
  temperature: 0.7,
  timeout: 60,
  is_active: true,
})

const rules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  provider_type: [{ required: true, message: '请选择类型', trigger: 'change' }],
  api_url: [
    { required: true, message: '请输入 API 地址', trigger: 'blur' },
    { type: 'url', message: '请输入有效的 URL', trigger: 'blur' },
  ],
  api_key: [{ required: true, message: '请输入 API Key', trigger: 'blur' }],
  model: [{ required: true, message: '请输入模型名称', trigger: 'blur' }],
}

async function loadProviders() {
  loading.value = true
  try {
    const res = await getProviders()
    providers.value = res.data.data.items
  } catch {
    ElMessage.error('加载供应商列表失败')
  } finally {
    loading.value = false
  }
}

function openCreateDialog() {
  editingProvider.value = null
  showDialog.value = true
}

function openEditDialog(row: LLMProvider) {
  editingProvider.value = row
  form.name = row.name
  form.provider_type = row.provider_type
  form.api_url = row.api_url
  form.api_key = ''  // 不回填
  form.model = row.model
  form.temperature = row.temperature
  form.timeout = row.timeout
  form.is_active = row.is_active
  showDialog.value = true
}

function resetForm() {
  form.name = ''
  form.provider_type = 'deepseek'
  form.api_url = ''
  form.api_key = ''
  form.model = ''
  form.temperature = 0.7
  form.timeout = 60
  form.is_active = true
  editingProvider.value = null
}

async function handleSave() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  saving.value = true
  try {
    const payload: any = {
      name: form.name,
      provider_type: form.provider_type,
      api_url: form.api_url,
      model: form.model,
      temperature: form.temperature,
      timeout: form.timeout,
      is_active: form.is_active,
    }
    // 编辑时 api_key 留空代表不修改
    if (form.api_key) {
      payload.api_key = form.api_key
    }

    if (editingProvider.value) {
      await updateProvider(editingProvider.value.id, payload)
      ElMessage.success('更新成功')
    } else {
      payload.api_key = form.api_key
      await createProvider(payload)
      ElMessage.success('添加成功')
    }
    showDialog.value = false
    await loadProviders()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleSetDefault(row: LLMProvider) {
  try {
    await ElMessageBox.confirm(`将 "${row.name}" 设为默认模型？`, '设为默认')
    await setDefaultProvider(row.id)
    ElMessage.success('已设为默认')
    await loadProviders()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error('操作失败')
  }
}

async function handleDelete(row: LLMProvider) {
  try {
    await ElMessageBox.confirm(`删除供应商 "${row.name}"？此操作可恢复。`, '确认删除', { type: 'warning' })
    await deleteProvider(row.id)
    ElMessage.success('删除成功')
    await loadProviders()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

onMounted(loadProviders)
</script>

<style scoped>
.llm-providers {
  padding: 24px;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
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

.provider-list {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
}

.ml-2 {
  margin-left: 8px;
}
</style>
