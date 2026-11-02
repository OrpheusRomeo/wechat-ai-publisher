<template>
  <el-dialog
    :model-value="modelValue"
    :title="isEdit ? '编辑账号' : '新增账号'"
    width="600px"
    @update:model-value="$emit('update:modelValue', $event)"
    @closed="resetForm"
  >
    <el-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-width="100px"
    >
      <!-- 平台类型 -->
      <el-form-item label="平台" prop="platform_type">
        <el-select
          v-model="form.platform_type"
          placeholder="选择平台"
          :disabled="isEdit"
          style="width: 100%"
        >
          <el-option
            v-for="p in platforms"
            :key="p.platform_type"
            :label="p.display_name"
            :value="p.platform_type"
          />
        </el-select>
      </el-form-item>

      <!-- 账号名称 -->
      <el-form-item label="账号名称" prop="account_name">
        <el-input v-model="form.account_name" placeholder="如: 微信公众号-主账号" />
      </el-form-item>

      <!-- 凭证 (根据平台不同显示不同字段) -->
      <el-divider content-position="left">凭证配置</el-divider>

      <!-- 微信公众号凭证 -->
      <template v-if="form.platform_type === 'wechat'">
        <el-form-item label="AppID" prop="credentials.app_id">
          <el-input v-model="form.credentials.app_id" placeholder="wx..." />
        </el-form-item>
        <el-form-item label="AppSecret" prop="credentials.app_secret">
          <el-input v-model="form.credentials.app_secret" placeholder="AppSecret" show-password />
        </el-form-item>
        <el-form-item label="Token">
          <el-input v-model="form.credentials.token" placeholder="微信后台配置的Token" />
        </el-form-item>
        <el-form-item label="EncodingAESKey">
          <el-input v-model="form.credentials.encoding_aes_key" placeholder="EncodingAESKey" />
        </el-form-item>
        <el-form-item label="默认封面图 URL">
          <el-input
            v-model="form.credentials.default_cover_url"
            placeholder="留空则使用「系统配置 → 微信配置」里的默认封面"
          />
        </el-form-item>
      </template>

      <!-- 抖音凭证 -->
      <template v-else-if="form.platform_type === 'douyin'">
        <el-form-item label="Client Key" prop="credentials.client_key">
          <el-input v-model="form.credentials.client_key" placeholder="抖音开放平台 Client Key" />
        </el-form-item>
        <el-form-item label="Client Secret" prop="credentials.client_secret">
          <el-input v-model="form.credentials.client_secret" placeholder="抖音开放平台 Client Secret" show-password />
        </el-form-item>
      </template>

      <!-- 小红书凭证 -->
      <template v-else-if="form.platform_type === 'xiaohongshu'">
        <el-form-item label="XHS Token" prop="credentials.xhs_token">
          <el-input v-model="form.credentials.xhs_token" placeholder="小红书登录 Token" />
        </el-form-item>
      </template>

      <!-- 百家号凭证 -->
      <template v-else-if="form.platform_type === 'baijiahao'">
        <el-form-item label="BDUSS" prop="credentials.bduss">
          <el-input v-model="form.credentials.bduss" placeholder="百家号 BDUSS" />
        </el-form-item>
        <el-form-item label="Ptoken">
          <el-input v-model="form.credentials.ptoken" placeholder="百家号 Ptoken" />
        </el-form-item>
      </template>

      <!-- 头条凭证 -->
      <template v-else-if="form.platform_type === 'toutiao'">
        <el-form-item label="Client Key" prop="credentials.client_key">
          <el-input v-model="form.credentials.client_key" placeholder="头条开放平台 Client Key" />
        </el-form-item>
        <el-form-item label="Client Secret" prop="credentials.client_secret">
          <el-input v-model="form.credentials.client_secret" placeholder="头条开放平台 Client Secret" show-password />
        </el-form-item>
      </template>

      <!-- YouTube凭证 -->
      <template v-else-if="form.platform_type === 'youtube'">
        <el-form-item label="API Key" prop="credentials.api_key">
          <el-input v-model="form.credentials.api_key" placeholder="YouTube Data API Key" />
        </el-form-item>
        <el-form-item label="Channel ID">
          <el-input v-model="form.credentials.channel_id" placeholder="YouTube Channel ID" />
        </el-form-item>
      </template>

      <el-divider content-position="left">调度配置</el-divider>

      <!-- 爬虫时间 -->
      <el-form-item label="爬虫时间">
        <el-input v-model="form.crawl_cron" placeholder="格式: 08:00 或 0 8 * * *">
          <template #append>
            <el-tooltip content="支持 HH:MM 格式或完整 cron 表达式">
              <el-icon><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
        </el-input>
      </el-form-item>

      <!-- 推送时间 -->
      <el-form-item label="推送时间">
        <el-input v-model="form.push_cron" placeholder="格式: 09:00 或 0 9 * * *">
          <template #append>
            <el-tooltip content="支持 HH:MM 格式或完整 cron 表达式">
              <el-icon><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
        </el-input>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="$emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="handleSubmit">
        {{ isEdit ? '保存' : '创建' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { PLATFORM_TYPES, type Platform, type PlatformAccount, type AccountCredentials } from '@/api/platforms'

const props = defineProps<{
  modelValue: boolean
  account?: PlatformAccount | null
  platforms: Platform[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [data: any]
  cancel: []
}>()

const formRef = ref<FormInstance>()
const submitting = ref(false)

const isEdit = computed(() => !!props.account)

const defaultForm = {
  platform_type: 'wechat',
  account_name: '',
  credentials: {} as AccountCredentials,
  crawl_cron: '08:00',
  push_cron: '09:00',
}

const form = reactive({ ...defaultForm })

// 根据平台类型动态生成验证规则
const rules = computed<FormRules>(() => {
  const base: FormRules = {
    platform_type: [{ required: true, message: '请选择平台', trigger: 'change' }],
    account_name: [{ required: true, message: '请输入账号名称', trigger: 'blur' }],
  }

  // 微信公众号
  if (form.platform_type === 'wechat') {
    base['credentials.app_id'] = [{ required: true, message: '请输入 AppID', trigger: 'blur' }]
    base['credentials.app_secret'] = [{ required: true, message: '请输入 AppSecret', trigger: 'blur' }]
  }

  // 抖音
  if (form.platform_type === 'douyin') {
    base['credentials.client_key'] = [{ required: true, message: '请输入 Client Key', trigger: 'blur' }]
    base['credentials.client_secret'] = [{ required: true, message: '请输入 Client Secret', trigger: 'blur' }]
  }

  // 小红书
  if (form.platform_type === 'xiaohongshu') {
    base['credentials.xhs_token'] = [{ required: true, message: '请输入 XHS Token', trigger: 'blur' }]
  }

  // 百家号
  if (form.platform_type === 'baijiahao') {
    base['credentials.bduss'] = [{ required: true, message: '请输入 BDUSS', trigger: 'blur' }]
  }

  // 头条
  if (form.platform_type === 'toutiao') {
    base['credentials.client_key'] = [{ required: true, message: '请输入 Client Key', trigger: 'blur' }]
    base['credentials.client_secret'] = [{ required: true, message: '请输入 Client Secret', trigger: 'blur' }]
  }

  // YouTube
  if (form.platform_type === 'youtube') {
    base['credentials.api_key'] = [{ required: true, message: '请输入 API Key', trigger: 'blur' }]
  }

  return base
})

// 监听账号编辑
watch(() => props.account, (account) => {
  if (account) {
    form.platform_type = account.platform_type || 'wechat'
    form.account_name = account.account_name
    form.crawl_cron = account.crawl_cron || '08:00'
    form.push_cron = account.push_cron || '09:00'
    form.credentials = {}
    // 非敏感字段（默认封面等）后端原样返回，可以直接回填；
    // 敏感字段（AppSecret 等）只返回掩码，需要重新输入。
    const masked = (account.credentials_masked || {}) as Record<string, string>
    if (masked.default_cover_url) {
      form.credentials.default_cover_url = masked.default_cover_url
    }
  }
}, { immediate: true })

// 监听对话框关闭
watch(() => props.modelValue, (val) => {
  if (!val) {
    resetForm()
  }
})

function resetForm() {
  form.platform_type = 'wechat'
  form.account_name = ''
  form.credentials = {}
  form.crawl_cron = '08:00'
  form.push_cron = '09:00'
  formRef.value?.resetFields()
}

async function handleSubmit() {
  if (!formRef.value) return

  try {
    await formRef.value.validate()
  } catch {
    ElMessage.error('请检查表单填写')
    return
  }

  submitting.value = true
  try {
    emit('submit', {
      platform_type: form.platform_type,
      account_name: form.account_name,
      credentials: form.credentials,
      crawl_cron: form.crawl_cron || '08:00',
      push_cron: form.push_cron || '09:00',
    })
  } finally {
    submitting.value = false
  }
}
</script>
