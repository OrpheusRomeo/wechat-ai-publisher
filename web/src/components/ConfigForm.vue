<template>
  <div>
    <h2 v-if="title">{{ title }}</h2>
    <el-form :model="formData" label-width="180px">
      <el-form-item
        v-for="item in items"
        :key="item.key"
        :label="item.description || item.key"
      >
        <el-input
          v-if="item.is_secret"
          v-model="formData[item.key]"
          type="password"
          show-password
          :placeholder="item.placeholder"
        />
        <el-input
          v-else
          v-model="formData[item.key]"
          :placeholder="item.placeholder"
        />
        <span v-if="item.is_secret && formData[item.key]" class="mask-hint">
          当前值: {{ item.value_masked || '****' }}
        </span>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="saving" @click="handleSave">保存配置</el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, watch } from 'vue'
import { settingsApi } from '@/api/settings'
import { ElMessage } from 'element-plus'

const props = defineProps<{ category: string; title?: string }>()

const items = ref<any[]>([])
const formData = reactive<Record<string, string>>({})
const saving = ref(false)

async function load() {
  try {
    items.value = await settingsApi.getAll(props.category)
    for (const item of items.value) {
      if (item.is_secret) {
        formData[item.key] = '' // 敏感字段不自动填充，需要用户手动输入
      } else {
        formData[item.key] = item.value || ''
      }
    }
  } catch { /* ignore */ }
}

async function handleSave() {
  saving.value = true
  try {
    const updates = items.value
      .filter((i: any) => formData[i.key] !== undefined && formData[i.key] !== '')
      .map((i: any) => ({ key: i.key, value: formData[i.key] || '' }))
    if (updates.length === 0) { ElMessage.warning('没有需要保存的配置'); return }
    await settingsApi.batchUpdate(updates)
    ElMessage.success('配置已保存')
  } catch (e: any) {
    ElMessage.error(e.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
watch(() => props.category, load)
</script>

<style scoped>
.mask-hint {
  font-size: 12px;
  color: #999;
  margin-left: 8px;
}
</style>
